"""
cptdm.pipeline.runner
~~~~~~~~~~~~~~~~~~~~~
Composable, testable transform pipeline.

Each step is a pure function:
    step(ds: xr.Dataset, request: Request, **cfg) -> xr.Dataset

Steps are applied in order:
  1. subset        – spatial bbox + time selection
  2. normalise     – coordinate conventions, calendar, units
  3. aggregate     – temporal aggregation (monthly → seasonal, daily → weekly)
  4. ensemble      – reduce ensemble members (median or mean)
  5. anomalies     – subtract climatological mean (for hindcasts)

The runner dispatches based on request.timescale and the role of each
dataset ('hindcast', 'forecast', 'obs').
"""
from __future__ import annotations

import calendar
import logging
from typing import Literal

import numpy as np
import xarray as xr

from cptdm.request import Request

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(
    raw: dict[str, xr.Dataset],
    request: Request,
) -> dict[str, xr.Dataset]:
    """
    Apply the full transform pipeline to all fetched datasets.
    """
    processed: dict[str, xr.Dataset] = {}

    for role, ds in raw.items():
        log.debug("Pipeline: processing '%s'", role)
        ds = step_normalise_coords(ds)        # rename lat/lon, fix 0-360, N→S
        ds = step_subset(ds, request)
        ds = step_normalise_units(ds, request, role)
        if role in ("obs", "rean"):
            ds = step_aggregate_obs(ds, request)
        else:
            ds = step_aggregate(ds, request, role)
            if role == "hindcast":
                ds = step_ensemble_reduce(ds, request)
                if request.compute_anomalies:
                    ds = step_anomalies(ds, request)
            elif role == "forecast":
                ds = step_ensemble_reduce(ds, request)
        # Squeeze out any remaining size-1 dimensions
        ds = _squeeze_size1_dims(ds, keep=["time", "lat", "lon",
                                            "forecast_reference_time",
                                            "indexing_time", "valid_time"])
        processed[role] = ds

    return processed


# ---------------------------------------------------------------------------
# Step 0: Coordinate normalisation (always first)
# ---------------------------------------------------------------------------

def step_normalise_coords(ds: xr.Dataset) -> xr.Dataset:
    """
    Normalise coordinate names and conventions regardless of source:
      - latitude/longitude → lat/lon
      - forecastMonth/leadtime_month/lead → leadtime_month  (canonical name)
      - lon 0–360 → -180–180
      - lat ascending → descending (N→S for CPT)
    """
    renames = {}

    # Spatial coords
    for old, new in [("latitude", "lat"), ("longitude", "lon"),
                     ("Latitude", "lat"), ("Longitude", "lon")]:
        if old in ds.coords or old in ds.dims:
            renames[old] = new

    # Lead/forecast month dim — many names in the wild
    for old in ("forecastMonth", "leadtime_month", "lead_month",
                "LeadTime", "leadtime"):
        if old in ds.dims and old != "leadtime_month":
            renames[old] = "leadtime_month"
            break

    # ERA5-Land (and some other CDS products) use valid_time instead of time
    if "valid_time" in ds.dims and "time" not in ds.dims:
        renames["valid_time"] = "time"

    if renames:
        ds = ds.rename(renames)

    # Lon: 0–360 → -180–180
    if "lon" in ds.coords and float(ds.lon.max()) > 180:
        ds = ds.assign_coords(lon=((ds.lon + 180) % 360) - 180)
        ds = ds.sortby("lon")

    # Lat: ensure descending (N→S)
    if "lat" in ds.coords and ds.lat.values[0] < ds.lat.values[-1]:
        ds = ds.sortby("lat", ascending=False)

    return ds


# ---------------------------------------------------------------------------
# Step 1: Spatial subset
# ---------------------------------------------------------------------------

def step_subset(ds: xr.Dataset, request: Request) -> xr.Dataset:
    """
    Subset the dataset to the requested bounding box.
    Assumes coords are already named 'lat' / 'lon' (normalised by provider).
    """
    lonW, latS, lonE, latN = request.bbox

    if "lat" in ds.coords:
        ds = ds.sel(lat=slice(latN, latS))  # N→S order
    if "lon" in ds.coords:
        ds = ds.sel(lon=slice(lonW, lonE))

    return ds


# ---------------------------------------------------------------------------
# Step 2: Unit normalisation
# ---------------------------------------------------------------------------

def step_normalise_units(
    ds: xr.Dataset,
    request: Request,
    role: str,
) -> xr.Dataset:
    """
    Convert raw units to CPT output units.
    For forecast/hindcast: uses request.variable to determine conversion.
    For obs/rean: detects actual data variable to handle predictor datasets
    (ERSST SST, RONI index) which may differ from the forecast variable.
    """
    pvar  = _first_data_var(ds)
    units = ds[pvar].attrs.get("units", "").strip().lower()

    # Skip conversion if data has no meaningful units (e.g. NMME clim files)
    if units in ("none", ""):
        log.debug("  step_normalise_units: units='%s' — skipping conversion", units)
        return ds

    # For obs/rean roles, detect the actual variable from the data var name
    # so predictor datasets (sst, enso_index) are handled correctly
    if role in ("obs", "rean"):
        actual_var = pvar  # use the actual nc variable name
    else:
        actual_var = request.variable

    if actual_var in ("prcp",) or (actual_var not in ("sst", "enso_index", "rfreq") and request.variable == "prcp"):
        ds = _prcp_to_mm(ds, request, role)
    elif actual_var in ("tmean", "tmax", "tmin", "t2m") or (
        actual_var not in ("sst", "enso_index") and request.variable in ("tmean", "tmax", "tmin")
    ):
        ds = _temp_to_celsius(ds)
    elif actual_var == "sst":
        ds = _temp_to_celsius(ds)  # SST is in K from some sources, degC from others
    # enso_index: dimensionless — no conversion needed

    return ds


def _prcp_to_mm(ds, request, role):
    """
    Convert precipitation to mm/month for CPT output.
    Handles:
      - m/s  (CDS seasonal rate)   → × days_in_month × 86400 × 1000
      - mm/day (NMME)              → × days_in_month
      - mm/month, kg m-2, mm       → already OK, no conversion
    """
    import pandas as pd

    pvar = _first_data_var(ds)
    data = ds[pvar]
    units = data.attrs.get("units", "").strip()

    if units in ("mm", "mm/month", "kg m-2", "kg/m2", "kg m**-2"):
        return ds

    # NMME prate is often stored as kg/m^2/s (= mm/s), which is effectively m/s * 1000
    if units in ("kg/m^2/s", "kg m^-2 s^-1", "kg/m2/s", "kg m-2 s-1"):
        units = "m/s"   # fall through to m/s → mm/month converter below

    time_coord = _find_time_coord(ds)
    init_month = request.init.month

    def _days_per_lead_array(lead_vals):
        return np.array([
            float(calendar.monthrange(2000,
                ((init_month - 1 + int(lm) - 1) % 12) + 1)[1])
            for lm in lead_vals
        ])

    # ── mm/day → mm/month (NMME) ──────────────────────────────────────
    if units in ("mm/day", "mm day-1", "mm d-1", "mm/d"):
        if "leadtime_month" in data.dims and time_coord in data.dims:
            lead_vals = ds["leadtime_month"].values
            days = _days_per_lead_array(lead_vals)
            dims = data.dims
            shape = [1] * len(dims)
            shape[dims.index("leadtime_month")] = len(days)
            converted = data * days.reshape(shape)
        elif time_coord in data.dims:
            times = pd.DatetimeIndex(ds[time_coord].values)
            lead_start = request.lead.lead_start if request.lead else 1
            target_m = ((init_month - 1 + lead_start - 1) % 12) + 1
            days = np.array([float(calendar.monthrange(t.year, target_m)[1]) for t in times])
            dims = data.dims
            shape = [1] * len(dims)
            shape[dims.index(time_coord)] = len(days)
            converted = data * days.reshape(shape)
        else:
            log.warning("No time dim for mm/day conversion; using 30-day month")
            converted = data * 30.0
        converted.attrs.update(data.attrs)
        converted.attrs["units"] = "mm"
        ds[pvar] = converted
        return ds

    # ── m/s → mm/month (CDS seasonal) ────────────────────────────────
    if "leadtime_month" in data.dims and time_coord in data.dims:
        lead_vals = ds["leadtime_month"].values
        secs = _days_per_lead_array(lead_vals) * 86400.0
        dims = data.dims
        shape = [1] * len(dims)
        shape[dims.index("leadtime_month")] = len(secs)
        converted = data * secs.reshape(shape) * 1000.0
    elif time_coord in data.dims:
        times = pd.DatetimeIndex(ds[time_coord].values)
        lead_start = request.lead.lead_start if request.lead else 1
        target_m = ((init_month - 1 + lead_start - 1) % 12) + 1
        secs = np.array([float(calendar.monthrange(t.year, target_m)[1]) * 86400.0 for t in times])
        dims = data.dims
        shape = [1] * len(dims)
        shape[dims.index(time_coord)] = len(secs)
        converted = data * secs.reshape(shape) * 1000.0
    else:
        log.warning("No time dim for precip conversion; using 30-day month")
        converted = data * 30 * 86400.0 * 1000.0

    converted.attrs.update(data.attrs)
    converted.attrs["units"] = "mm"
    ds[pvar] = converted
    return ds



def _temp_to_celsius(ds: xr.Dataset) -> xr.Dataset:
    """Convert Kelvin → Celsius if needed."""
    pvar = _first_data_var(ds)
    data = ds[pvar]
    units = data.attrs.get("units", "")
    if units in ("K", "Kelvin", "kelvin"):
        ds[pvar] = data - 273.15
        ds[pvar].attrs["units"] = "degree_Celsius"
    return ds


# ---------------------------------------------------------------------------
# Step 3: Temporal aggregation
# ---------------------------------------------------------------------------

def step_aggregate(
    ds: xr.Dataset,
    request: Request,
    role: str,
) -> xr.Dataset:
    """
    Aggregate over the lead period:
      - Seasonal: sum (prcp) or mean (temp) over lead months
      - Intraseasonal: sum (prcp) or mean (temp) over target days
    For hindcasts this preserves the year axis.
    For forecasts a single aggregated value per member remains.
    """
    pvar = _first_data_var(ds)
    var = request.variable

    if request.timescale == "seasonal":
        ds = _aggregate_seasonal(ds, pvar, var, request, role)
    else:
        ds = _aggregate_intraseasonal(ds, pvar, var, request, role)

    return ds


def _aggregate_seasonal(
    ds: xr.Dataset,
    pvar: str,
    variable: str,
    request: Request,
    role: str,
) -> xr.Dataset:
    """
    For a multi-month seasonal lead, reduce the leadtime_month dimension.
    If only one lead month was requested, just squeeze that dim.
    """
    if "leadtime_month" not in ds.dims:
        return ds  # nothing to aggregate

    n_leads = len(ds.leadtime_month)
    if n_leads == 1:
        ds = ds.squeeze("leadtime_month", drop=True)
        return ds

    # Multi-month: aggregate
    if variable == "prcp" and request.aggreg == "total":
        ds[pvar] = ds[pvar].sum(dim="leadtime_month", min_count=1)
    else:
        ds[pvar] = ds[pvar].mean(dim="leadtime_month")

    return ds


def _aggregate_intraseasonal(
    ds: xr.Dataset,
    pvar: str,
    variable: str,
    request: Request,
    role: str,
) -> xr.Dataset:
    """
    Aggregate over the target days (weekly window).
    Assumes ds has a 'time' (or 'step') dimension of daily data.
    """
    time_coord = _find_time_coord(ds)
    if time_coord is None:
        log.warning("No time coord found for intraseasonal aggregation; skipping")
        return ds

    import pandas as pd

    # If the time dimension has very few steps (≤4), the data is likely
    # already aggregated (e.g. SubC emean weekly anomalies, or single-field
    # forecasts).  Skip aggregation and return as-is.
    n_times = ds.sizes.get(time_coord, 0)
    if n_times <= 4:
        log.info("  Intraseasonal: %d time step(s) — data appears pre-aggregated; skipping", n_times)
        return ds

    times = pd.DatetimeIndex(ds[time_coord].values)
    tgt_start = request.intra_lead.tgt_start
    tgt_end = request.intra_lead.tgt_end

    # If there is no step dimension, the data has already been aggregated
    # over the lead window (e.g. S2S provider returns (time, lat, lon)
    # where time = init dates).  Return as-is — do not collapse time.
    if "step" not in ds.dims:
        log.info(
            "  Intraseasonal: no step dim — data is pre-aggregated "            "(time=%d init dates); skipping.",
            n_times,
        )
        return ds

    mask = (times >= pd.Timestamp(tgt_start)) & (times <= pd.Timestamp(tgt_end))
    ds_sel = ds.isel({time_coord: mask})

    if ds_sel.sizes[time_coord] == 0:
        log.warning(
            "  Intraseasonal aggregation: no time steps in target window %s–%s "
            "(time range: %s–%s). Returning unaggregated data.",
            tgt_start, tgt_end, times.min().date(), times.max().date(),
        )
        return ds

    if variable == "prcp" and request.aggreg == "total":
        result = ds_sel[pvar].sum(dim=time_coord, min_count=1, keep_attrs=True)
    else:
        result = ds_sel[pvar].mean(dim=time_coord, keep_attrs=True)

    # Keep as Dataset with a scalar "time" coordinate representing period midpoint
    ds_out = ds_sel.isel({time_coord: 0}, drop=True).copy()
    ds_out[pvar] = result
    return ds_out


# ---------------------------------------------------------------------------
# Step 4: Ensemble reduction
# ---------------------------------------------------------------------------

def step_ensemble_reduce(ds: xr.Dataset, request: Request) -> xr.Dataset:
    """
    Reduce the ensemble member dimension using median (default) or mean.
    CDS uses 'number' as the member dimension name.
    """
    member_dims = [d for d in ds.dims if d in ("number", "member", "realization")]
    if not member_dims:
        return ds  # already reduced or single-member

    dim = member_dims[0]
    stat = request.ensemble_stat

    pvar = _first_data_var(ds)
    if stat == "median":
        ds[pvar] = ds[pvar].median(dim=dim, keep_attrs=True)
    else:
        ds[pvar] = ds[pvar].mean(dim=dim, keep_attrs=True)

    # Drop the member dimension cleanly
    if dim in ds.dims:
        ds = ds.squeeze(dim, drop=True) if ds.sizes.get(dim, 0) == 1 else ds
        # After reduction the dim should be gone from the data var;
        # rebuild dataset without it
        ds = ds.drop_vars([dim], errors="ignore")

    return ds


# ---------------------------------------------------------------------------
# Step 3b: Obs/rean temporal aggregation (CHIRPS, ERA5-Land, CRU)
# ---------------------------------------------------------------------------

def step_aggregate_obs(ds: xr.Dataset, request: Request) -> xr.Dataset:
    """
    Aggregate monthly obs/rean data to seasonal or intraseasonal targets.

    For seasonal: for each year in clim_start–clim_end, select the target
    months and sum (prcp) or average (temp), yielding one value per year.
    Output time dim has one entry per year, labelled by the init year.

    For intraseasonal: select daily data within tgt_start–tgt_end and
    sum or average.
    """
    import pandas as pd

    pvar = _first_data_var(ds)
    time_coord = _find_time_coord(ds)

    if time_coord is None or time_coord not in ds.dims:
        log.warning("step_aggregate_obs: no time dimension; returning as-is")
        return ds

    times = pd.DatetimeIndex(ds[time_coord].values)

    if request.timescale == "seasonal" and request.lead:
        init_month = request.init.month
        clim_start = request.clim_start or 1991
        clim_end   = request.clim_end   or 2020

        # Target calendar months for this lead.
        # lead_start=1 means the init month itself (CDS convention: leadtime_month=1
        # verifies in the init month, confirmed from GRIB verifyingMonth metadata).
        target_months = [
            ((init_month - 1 + (offset - 1)) % 12) + 1
            for offset in range(request.lead.lead_start, request.lead.lead_end + 1)
        ]

        # Detect if the target season crosses a calendar year boundary.
        # This happens when any target month is numerically less than the
        # first target month, e.g. NDJ from Nov init: N=11, D=12, J=1 — the
        # January (1) is less than November (11), so it's in the next year.
        crosses_year = any(m < target_months[0] for m in target_months)

        chunks = []
        labels = []

        for year in range(clim_start, clim_end + 1):
            # Build month masks, handling cross-year seasons
            month_slices = []
            for i, m in enumerate(target_months):
                # If month is earlier in calendar than first target month, it's next year
                yr = year + 1 if (crosses_year and m < target_months[0]) else year
                mask = (times.month == m) & (times.year == yr)
                if mask.any():
                    month_slices.append(ds[pvar].isel({time_coord: mask}))

            if len(month_slices) != len(target_months):
                log.debug("  obs year %d: only %d/%d months available; skipping",
                          year, len(month_slices), len(target_months))
                continue

            # Stack months and aggregate.
            # Drop the 'time' coordinate from each slice first — each slice
            # is a single timestep selected from the obs dataset, so its
            # 'time' coordinate conflicts when concatenating along a new dim.
            slices_clean = [s.drop_vars("time", errors="ignore")
                            for s in month_slices]
            stacked = xr.concat(slices_clean, dim="month", join="override")
            if request.variable in ("prcp", "rfreq") and request.aggreg == "total":
                agg = stacked.sum("month", skipna=True, min_count=1)
            else:
                agg = stacked.mean("month", skipna=True)

            chunks.append(agg)
            labels.append(pd.Timestamp(f"{year}-{target_months[0]:02d}-01"))

        if not chunks:
            raise ValueError(
                f"No complete obs seasons found for {clim_start}–{clim_end} "
                f"target months={target_months}. Check obs dataset coverage."
            )

        # Stack all years along a new 'time' dimension
        result = xr.concat(chunks, dim="time")
        result = result.assign_coords(time=("time", labels))
        result.attrs.update(ds[pvar].attrs)

        ds_out = xr.Dataset({pvar: result}, attrs=ds.attrs)
        # Carry over lat/lon coordinates if present
        for coord in ("lat", "lon"):
            if coord in ds.coords:
                ds_out = ds_out.assign_coords({coord: ds.coords[coord]})

        log.info("  obs aggregated: %d years × %d months → %d seasonal values",
                 len(chunks), len(target_months), len(chunks))
        return ds_out

    elif request.timescale == "intraseasonal" and request.intra_lead:
        import pandas as pd
        tgt_start = pd.Timestamp(request.intra_lead.tgt_start)
        tgt_end   = pd.Timestamp(request.intra_lead.tgt_end)
        mask = (times >= tgt_start) & (times <= tgt_end)
        ds_sel = ds.isel({time_coord: mask})

        if request.variable in ("prcp", "rfreq") and request.aggreg == "total":
            result = ds_sel[pvar].sum(dim=time_coord, skipna=True, min_count=1)
        else:
            result = ds_sel[pvar].mean(dim=time_coord, skipna=True)

        ds = xr.Dataset({pvar: result}, attrs=ds.attrs)

    return ds


# ---------------------------------------------------------------------------
# Step 5: Anomalies (hindcast only)
# ---------------------------------------------------------------------------

def step_anomalies(ds: xr.Dataset, request: Request) -> xr.Dataset:
    """
    Subtract the climatological mean from each year's value.
    Applied to hindcasts only.  The mean is computed over the full
    time (year) dimension of the dataset.
    """
    pvar = _first_data_var(ds)
    time_coord = _find_time_coord(ds)

    if time_coord is None or time_coord not in ds.dims:
        log.warning("Cannot compute anomalies: no time dimension found")
        return ds

    clim_mean = ds[pvar].mean(dim=time_coord, skipna=True)
    ds[pvar] = ds[pvar] - clim_mean
    ds[pvar].attrs["description"] = "anomaly (departure from climatological mean)"

    return ds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_data_var(ds: xr.Dataset) -> str:
    """Return the name of the first (and usually only) data variable."""
    dvars = list(ds.data_vars)
    if not dvars:
        raise ValueError("Dataset has no data variables")
    if len(dvars) > 1:
        log.debug("Dataset has multiple data vars: %s; using '%s'", dvars, dvars[0])
    return dvars[0]


def _find_time_coord(ds: xr.Dataset) -> str | None:
    """
    Return the name of the time-like dimension (must be in ds.dims, not just coords).
    Checks forecast_reference_time first (CDS seasonal), then common names.
    """
    for candidate in ("forecast_reference_time", "indexing_time", "time", "valid_time", "step"):
        if candidate in ds.dims:          # must be a dimension, not just a coordinate
            return candidate
    return None


def _squeeze_size1_dims(
    ds: xr.Dataset,
    keep: list,
) -> xr.Dataset:
    """
    Squeeze out any dimensions with size 1 that are not in the keep list.
    Handles stray leadtime_month, number/member, realization, step dims
    that may remain after aggregation/reduction.
    """
    for dim in list(ds.dims):
        if dim not in keep and ds.sizes.get(dim, 0) == 1:
            ds = ds.squeeze(dim, drop=True)
    return ds
