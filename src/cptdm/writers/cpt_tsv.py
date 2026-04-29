"""
cptdm.writers.cpt_tsv
~~~~~~~~~~~~~~~~~~~~~
Write CPT v10 tab-separated files from processed xarray Datasets.

CPT TSV format (from CPT_formatV11.f90 and example files):

    xmlns:cpt=http://iri.columbia.edu/CPT/v10/
    cpt:nfields=N
    cpt:T\t<year1-month1>\t<year2-month1>\t...     (hindcast) or
    cpt:T\t<year-month>                              (forecast)
    <field header line>
    <lon header row>
    <lat> \t <val1> \t <val2> \t ...
    ...
    [repeat field header + lon row + data rows for each year]

Missing value: -999  (written as -999.000000)
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import xarray as xr

from cptdm.request import Request, VARIABLE_CPT_FIELD, VARIABLE_CPT_UNITS
from cptdm.pipeline.runner import _first_data_var, _find_time_coord

log = logging.getLogger(__name__)

MISSING = -999.0
CPT_XMLNS = "xmlns:cpt=http://iri.columbia.edu/CPT/v10/"
FMT_FLOAT = "{:.6f}"


def write_cpt_tsv(
    processed: dict[str, xr.Dataset],
    request: Request,
    outdir: Path,
) -> list[Path]:
    """
    Write all available CPT TSV files for the given request.
    Returns list of written file paths.
    """
    written: list[Path] = []

    role_filename = {
        "hindcast": request.hcast_filename,
        "forecast": request.fcast_filename,
        "obs": request.obs_filename,
        "rean": request.obs_filename,
    }

    for role, ds in processed.items():
        pvar = _first_data_var(ds) if ds.data_vars else None
        if pvar:
            log.info("  [%s] dims=%s  shape=%s", role,
                     dict(ds.sizes), dict(ds[pvar].sizes))

        fname = role_filename.get(role)
        if not fname:
            log.warning("No filename for role '%s'; skipping write", role)
            continue

        out_path = outdir / fname
        log.info("Writing CPT TSV: %s", out_path)

        # Route to the correct writer based on dataset type:
        #   - NMME clim (no year axis) → _write_single_field
        #   - Spatial multi-year (obs, rean, hindcast with lat/lon) → _write_multi_year
        #   - 1-D index (RONI / ENSO, no lat/lon dims at all) → _write_index
        is_nmme_clim = ds.attrs.get("nmme_clim") == "true"
        pvar_r = _first_data_var(ds) if ds.data_vars else None
        # Check the data variable's actual dims — most reliable signal
        da_dims = set(ds[pvar_r].dims) if pvar_r else set()
        has_spatial = "lat" in da_dims and "lon" in da_dims
        if is_nmme_clim:
            _write_single_field(ds, request, out_path)
        elif role in ("hindcast", "obs", "rean") and has_spatial:
            _write_multi_year(ds, request, role, out_path)
        elif role == "forecast" and has_spatial:
            # Multi-init forecast (e.g. S2S): time dim > 1 → write as multi-year
            time_coord = _find_time_coord(ds)
            if time_coord and time_coord in ds.dims and ds.sizes[time_coord] > 1:
                _write_multi_year(ds, request, "forecast", out_path)
            else:
                _write_single_field(ds, request, out_path)
        elif role == "forecast":
            _write_single_field(ds, request, out_path)
        else:
            # 1-D index (RONI / ENSO) — no lat/lon dims
            _write_index(ds, request, out_path)

        written.append(out_path)

    return written


# ---------------------------------------------------------------------------
# Multi-year writer (hindcast / obs / rean)
# ---------------------------------------------------------------------------

def _write_multi_year(
    ds: xr.Dataset,
    request: Request,
    role: str,
    out_path: Path,
) -> None:
    """
    Write a CPT v11 multi-year hindcast/obs TSV.

    Format (per CPT_formatV11.f90 write_cpt_grid_v11):
      xmlns:cpt=http://iri.columbia.edu/CPT/v10/
      cpt:nfields=1
      cpt:field=tp, cpt:S=1993-03-01, cpt:T=1993-04/06, cpt:nrow=N, cpt:ncol=M, ...
      \t<lon1>\t<lon2>...
      <lat1>\t<val>\t<val>...
      ...nrow rows...
      cpt:field=tp, cpt:S=1994-03-01, cpt:T=1994-04/06, ...
      ...
    """
    import numpy as np

    pvar = _first_data_var(ds)
    time_coord = _find_time_coord(ds)

    if time_coord is None or time_coord not in ds.dims:
        raise ValueError(
            f"Cannot write multi-year CPT TSV for '{role}': no time dimension. "
            f"Dataset dims: {dict(ds.sizes)}"
        )

    data_arr = ds[pvar]
    for dim in list(data_arr.dims):
        if dim not in (time_coord, "lat", "lon") and data_arr.sizes[dim] == 1:
            data_arr = data_arr.squeeze(dim, drop=True)

    expected_dims = [time_coord, "lat", "lon"]
    present = [d for d in expected_dims if d in data_arr.dims]
    if len(present) == 3:
        data_arr = data_arr.transpose(*expected_dims)
    elif len(present) < 3:
        raise ValueError(
            f"Dataset for '{role}' missing lat or lon. Dims: {list(data_arr.dims)}"
        )

    data_np = np.array(data_arr, dtype=float)
    if data_np.ndim != 3:
        raise ValueError(
            f"Expected 3-D array for '{role}', got shape {data_np.shape}"
        )

    import pandas as pd
    times = pd.DatetimeIndex(ds[time_coord].values)
    lats  = ds.lat.values
    lons  = ds.lon.values
    nrow  = len(lats)
    ncol  = len(lons)

    field_tag = VARIABLE_CPT_FIELD.get(request.variable, request.variable)
    units_tag = VARIABLE_CPT_UNITS.get(request.variable, "unknown")

    # Target season: first and last month
    init_m = request.init.month
    if request.lead:
        # lead 1 = init month (CDS convention) so subtract 1 to get correct month
        tgt_m_start = ((init_m - 1 + request.lead.lead_start - 1) % 12) + 1
        tgt_m_end   = ((init_m - 1 + request.lead.lead_end   - 1) % 12) + 1
        # RONI is a 3-month running mean — always write the full season span
        # regardless of --lead (e.g. --lead 1-1 from March → 03/05 = MAM)
        if request.variable == "enso_index":
            tgt_m_end = ((tgt_m_start - 1 + 2) % 12) + 1
    else:
        tgt_m_start = tgt_m_end = init_m

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="ascii") as fh:
        # ── File header ────────────────────────────────────────────────
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")

        # ── One block per time step ────────────────────────────────────
        for ti, t in enumerate(times):
            year_data = _mask_missing(data_np[ti])

            if request.timescale == "intraseasonal" and request.intra_lead:
                # S = actual init date, T = target window
                import pandas as _pd
                init_dt = _pd.Timestamp(t)
                # Compute target window for this specific init date
                delta_start = (request.intra_lead.tgt_start - request.init).days
                delta_end   = (request.intra_lead.tgt_end   - request.init).days
                from datetime import timedelta as _td
                tgt_s = (init_dt + _pd.Timedelta(days=delta_start)).date()
                tgt_e = (init_dt + _pd.Timedelta(days=delta_end)).date()
                s_tag = init_dt.strftime("%Y-%m-%d")
                t_tag = f"{tgt_s.isoformat()}/{tgt_e.isoformat()}"
            else:
                # Seasonal: S = first of init month, T = target season
                init_year = t.year
                tgt_year = init_year + (1 if tgt_m_start < init_m and request.lead and request.lead.lead_start > 0 else 0)
                s_tag = f"{init_year}-{init_m:02d}-01"
                t_tag = f"{tgt_year}-{tgt_m_start:02d}/{tgt_m_end:02d}"

            # obs/rean have no initialisation date — omit cpt:S
            if role in ("obs", "rean"):
                fh.write(
                    f"cpt:field={field_tag},"
                    f" cpt:T={t_tag},"
                    f" cpt:nrow={nrow},"
                    f" cpt:ncol={ncol},"
                    f" cpt:row=Y,"
                    f" cpt:col=X,"
                    f" cpt:units={units_tag},"
                    f" cpt:missing={int(MISSING)}\n"
                )
            else:
                fh.write(
                    f"cpt:field={field_tag},"
                    f" cpt:T={t_tag},"
                    f" cpt:S={s_tag},"
                    f" cpt:nrow={nrow},"
                    f" cpt:ncol={ncol},"
                    f" cpt:row=Y,"
                    f" cpt:col=X,"
                    f" cpt:units={units_tag},"
                    f" cpt:missing={int(MISSING)}\n"
                )
            # Lon header row (tab-prefixed)
            fh.write("\t" + "\t".join(_fmt(lon) for lon in lons) + "\n")
            # Data rows
            for ri in range(nrow):
                row = "\t".join(_fmt(year_data[ri, ci]) for ci in range(ncol))
                fh.write(f"{_fmt(lats[ri])}\t{row}\n")


def _write_single_field(
    ds: xr.Dataset,
    request: Request,
    out_path: Path,
) -> None:
    """
    Write a CPT v11 single-field forecast TSV.

    Format:
      xmlns:cpt=http://iri.columbia.edu/CPT/v10/
      cpt:nfields=1
      cpt:field=tp, cpt:S=2026-03-01, cpt:T=2026-04/06, cpt:nrow=N, cpt:ncol=M, ...
      \t<lon1>\t<lon2>...
      <lat1>\t<val>...
    """
    import numpy as np

    pvar = _first_data_var(ds)
    data_arr = ds[pvar]

    for dim in list(data_arr.dims):
        if dim not in ("lat", "lon") and data_arr.sizes[dim] == 1:
            data_arr = data_arr.squeeze(dim, drop=True)

    if "leadtime_month" in data_arr.dims:
        if request.variable == "prcp" and request.aggreg == "total":
            data_arr = data_arr.sum(dim="leadtime_month", skipna=True)
        else:
            data_arr = data_arr.mean(dim="leadtime_month", skipna=True)

    for dim in list(data_arr.dims):
        if dim not in ("lat", "lon"):
            log.warning("Unexpected dim '%s' in forecast; taking mean", dim)
            data_arr = data_arr.mean(dim=dim, skipna=True)

    lats = ds.lat.values
    lons = ds.lon.values
    nrow = len(lats)
    ncol = len(lons)

    arr_2d = _mask_missing(np.array(data_arr, dtype=float))
    if arr_2d.ndim != 2:
        raise ValueError(f"Forecast not 2-D after reduction: shape={arr_2d.shape}")

    field_tag = VARIABLE_CPT_FIELD.get(request.variable, request.variable)
    units_tag = VARIABLE_CPT_UNITS.get(request.variable, "unknown")

    if request.timescale == "intraseasonal" and request.intra_lead:
        # S = actual init date, T = target window start/end
        s_tag = request.init.isoformat()
        t_tag = (
            f"{request.intra_lead.tgt_start.isoformat()}/"
            f"{request.intra_lead.tgt_end.isoformat()}"
        )
    else:
        init_m = request.init.month
        if request.lead:
            tgt_m_start = ((init_m - 1 + request.lead.lead_start - 1) % 12) + 1
            tgt_m_end   = ((init_m - 1 + request.lead.lead_end   - 1) % 12) + 1
            if request.variable == "enso_index":
                tgt_m_end = ((tgt_m_start - 1 + 2) % 12) + 1
        else:
            tgt_m_start = tgt_m_end = init_m
        tgt_year = request.init.year + request.season_year_offset
        s_tag = f"{request.init.year}-{init_m:02d}-01"
        t_tag = f"{tgt_year}-{tgt_m_start:02d}/{tgt_m_end:02d}"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="ascii") as fh:
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")
        fh.write(
            f"cpt:field={field_tag},"
            f" cpt:T={t_tag},"
            f" cpt:S={s_tag},"
            f" cpt:nrow={nrow},"
            f" cpt:ncol={ncol},"
            f" cpt:row=Y,"
            f" cpt:col=X,"
            f" cpt:units={units_tag},"
            f" cpt:missing={int(MISSING)}\n"
        )
        fh.write("\t" + "\t".join(_fmt(lon) for lon in lons) + "\n")
        for ri in range(nrow):
            row = "\t".join(_fmt(arr_2d[ri, ci]) for ci in range(ncol))
            fh.write(f"{_fmt(lats[ri])}\t{row}\n")



def _write_index(
    ds: xr.Dataset,
    request: Request,
    out_path: Path,
) -> None:
    """
    Write a CPT v11 index file (1-D time series, no lat/lon).
    Used for RONI/ENSO index and other scalar predictor series.

    Format (single block, all years as rows):
      xmlns:cpt=http://iri.columbia.edu/CPT/v10/
      cpt:nfields=1
      cpt:field=index, cpt:ncol=1, cpt:col=index, cpt:nrow=N,
          cpt:row=T, cpt:units=dimensionless, cpt:missing=-999
      \tindex
      YYYY-MM\t<value>
      YYYY-MM\t<value>
      ...
    """
    import numpy as np
    import pandas as pd

    pvar = _first_data_var(ds)
    time_coord = _find_time_coord(ds)

    if time_coord is None or time_coord not in ds.dims:
        raise ValueError(
            f"Cannot write index CPT TSV: no time dimension. "
            f"Dataset dims: {dict(ds.sizes)}"
        )

    data_arr = ds[pvar]
    times = pd.DatetimeIndex(ds[time_coord].values)
    values = np.array(data_arr.values, dtype=float)
    values = np.where(np.isnan(values), MISSING, values)

    field_tag = VARIABLE_CPT_FIELD.get(request.variable, "index")
    units_tag = VARIABLE_CPT_UNITS.get(request.variable, "dimensionless")
    nrow = len(times)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="ascii") as fh:
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")

        # Single header block for the entire time series
        fh.write(
            f"cpt:field={field_tag},"
            f" cpt:ncol=1,"
            f" cpt:col=index,"
            f" cpt:nrow={nrow},"
            f" cpt:row=T,"
            f" cpt:units={units_tag},"
            f" cpt:missing={int(MISSING)}\n"
        )
        # Column header
        fh.write("\tindex\n")

        # Data rows: target date (YYYY-MM) + value
        init_m = request.init.month
        for ti, (t, val) in enumerate(zip(times, values)):
            dt = pd.Timestamp(t)
            if request.lead and request.lead.lead_start > 0:
                # lead 1 = init month (CDS convention)
                tgt_m_s = ((init_m - 1 + request.lead.lead_start - 1) % 12) + 1
                tgt_y = dt.year + (1 if tgt_m_s < init_m else 0)
                # RONI: always write full 3-month season span (e.g. 03/05 for MAM)
                if request.variable == "enso_index":
                    tgt_m_e = ((tgt_m_s - 1 + 2) % 12) + 1
                    date_label = f"{tgt_y}-{tgt_m_s:02d}/{tgt_m_e:02d}"
                else:
                    date_label = f"{tgt_y}-{tgt_m_s:02d}"
            else:
                date_label = dt.strftime("%Y-%m")
            fh.write(f"{date_label}\t{_fmt(float(val.item() if hasattr(val, 'item') else val))}\n")


# ---------------------------------------------------------------------------
# Intraseasonal multi-init writer (the "lags" format)
# ---------------------------------------------------------------------------

def write_cpt_multi_init(
    datasets: dict[str, xr.Dataset],
    init_dates: list[date],
    request: Request,
    out_path: Path,
) -> None:
    """
    Write a CPT v11 multi-init (lags) file for intraseasonal data.

    This is the format used by CPT for sub-seasonal hindcasts where multiple
    initialisations are bundled into one file.  Follows the structure of
    Write_cpt_grid_lags_v11 in CPT_formatV11.f90:

      For each year (k), for each lag/init (l):
        cpt:field=<var>, cpt:S=<init_date>, cpt:T=<tgt_start>/<tgt_end>, ...
        <lon header>
        <lat> <data row>
        ...

    Args:
        datasets:   dict keyed by init date (ISO string) → xr.Dataset
                    Each Dataset has dims (lat, lon) for a single year.
                    OR a single Dataset with a "time" dim covering all years.
        init_dates: list of init dates in chronological order.
        request:    the Request object (for variable, lead, etc.).
        out_path:   output file path.
    """
    import numpy as np
    import pandas as pd

    if request.intra_lead is None:
        raise ValueError("write_cpt_multi_init requires an intraseasonal lead.")

    field_tag = VARIABLE_CPT_FIELD.get(request.variable, request.variable)
    units_tag = VARIABLE_CPT_UNITS.get(request.variable, "unknown")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="ascii") as fh:
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")

        for init_d in init_dates:
            init_key = init_d.isoformat()

            if init_key not in datasets:
                log.warning("No data for init %s; skipping in multi-init file", init_key)
                continue

            ds = datasets[init_key]
            pvar = _first_data_var(ds)
            data_arr = ds[pvar]

            # Squeeze singleton dims (time, member, etc.)
            for dim in list(data_arr.dims):
                if dim not in ("lat", "lon") and data_arr.sizes[dim] == 1:
                    data_arr = data_arr.squeeze(dim, drop=True)

            # If there's still a time dim with multiple steps, reduce it
            for dim in list(data_arr.dims):
                if dim not in ("lat", "lon"):
                    if request.variable in ("prcp", "rfreq") and request.aggreg == "total":
                        data_arr = data_arr.sum(dim=dim, skipna=True)
                    else:
                        data_arr = data_arr.mean(dim=dim, skipna=True)

            arr_2d = _mask_missing(np.array(data_arr, dtype=float))
            lats = ds.lat.values
            lons = ds.lon.values
            nrow = len(lats)
            ncol = len(lons)

            # Target period from the init date + lead
            lead = request.intra_lead
            # Recompute target for this specific init date
            tgt_start = init_d + __import__("datetime").timedelta(
                days=(lead.tgt_start - request.init).days
            )
            tgt_end = init_d + __import__("datetime").timedelta(
                days=(lead.tgt_end - request.init).days
            )

            s_tag = init_d.isoformat()
            t_tag = f"{tgt_start.isoformat()}/{tgt_end.isoformat()}"

            fh.write(
                f"cpt:field={field_tag},"
                f" cpt:S={s_tag},"
                f" cpt:T={t_tag},"
                f" cpt:nrow={nrow},"
                f" cpt:ncol={ncol},"
                f" cpt:row=Y,"
                f" cpt:col=X,"
                f" cpt:units={units_tag},"
                f" cpt:missing={int(MISSING)}\n"
            )
            fh.write("\t" + "\t".join(_fmt(lon) for lon in lons) + "\n")
            for ri in range(nrow):
                row = "\t".join(_fmt(arr_2d[ri, ci]) for ci in range(ncol))
                fh.write(f"{_fmt(lats[ri])}\t{row}\n")

    log.info("Wrote multi-init CPT file: %s (%d inits)", out_path.name, len(init_dates))


# ---------------------------------------------------------------------------
# Time label helpers
# ---------------------------------------------------------------------------

def _build_target_time_labels(times, request: "Request") -> list:
    """
    Convert init/reference timestamps to CPT target-season date labels.

    CPT v10 cpt:T must be the TARGET date, not the initialisation date.
    For seasonal lead 1-3 from a March init: target = April YYYY-04.
    For cross-year seasons (e.g. NDJ from Oct: Nov=same year, Dec=same,
    Jan=next year), cpt:T is the FIRST target month regardless.

    Uses actual calendar arithmetic — never the season_year_offset, which
    is a filename/naming convention, not a date-shift for the data.
    """
    import pandas as pd

    labels = []
    for t in times:
        try:
            dt = pd.Timestamp(t)
        except Exception:
            labels.append(str(t))
            continue

        if request.timescale == "seasonal" and request.lead:
            init_m = request.init.month
            lead_start = request.lead.lead_start
            # First month of target season
            target_m = ((init_m - 1 + lead_start) % 12) + 1
            # Year: same as init year unless the target month wrapped past Dec
            # (e.g. init=Dec, lead_start=1 → target=Jan → next year)
            target_y = dt.year + (1 if target_m < init_m and lead_start > 0 else 0)
            labels.append(f"{target_y}-{target_m:02d}")
        elif request.timescale == "intraseasonal" and request.intra_lead:
            labels.append(request.intra_lead.tgt_start.strftime("%Y-%m-%d"))
        else:
            labels.append(dt.strftime("%Y-%m"))

    return labels


def _format_time_label(t_val, request: "Request") -> str:
    """
    Format a single time value as a CPT target-season label.
    Kept for backward compatibility; delegates to _build_target_time_labels.
    """
    return _build_target_time_labels([t_val], request)[0]


def _forecast_time_label(request: Request) -> str:
    """Label for the single forecast time step."""
    if request.timescale == "seasonal" and request.season_label:
        # e.g. 2026-04  (first month of target season)
        init_m = request.init.month
        if request.lead:
            target_m = ((init_m - 1 + request.lead.lead_start) % 12) + 1
            target_y = request.init.year + request.season_year_offset
            return f"{target_y}-{target_m:02d}"
        return f"{request.init.year}-{request.init.month:02d}"
    elif request.intra_lead:
        return request.intra_lead.tgt_start.strftime("%Y-%m-%d")
    return request.init.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Full daily obs writer (--full-obs mode)
# ---------------------------------------------------------------------------

def write_cpt_full_obs(
    ds: xr.Dataset,
    variable: str,
    out_path: Path,
) -> None:
    """
    Write a CPT TSV with one field per daily timestep — no aggregation.

    Used by --full-obs to deliver raw daily observations for CPT's own
    internal aggregation (Simon Mason's approach).

    Format per field:
      cpt:field=<field>, cpt:T=YYYY-MM-DD, cpt:nrow=N, cpt:ncol=M, ...
      \\t<lon1>\\t<lon2>...
      <lat1>\\t<val>\\t<val>...

    Time coordinate: daily dates (no season, no S= init date).
    """
    import pandas as pd

    pvar = _first_data_var(ds)
    time_coord = _find_time_coord(ds)

    if time_coord is None or time_coord not in ds.dims:
        raise ValueError(
            f"Cannot write full-obs CPT TSV: no time dimension. "
            f"Dataset dims: {dict(ds.sizes)}"
        )

    data_arr = ds[pvar]
    # Squeeze out any size-1 dims that aren't time/lat/lon
    for dim in list(data_arr.dims):
        if dim not in (time_coord, "lat", "lon") and data_arr.sizes[dim] == 1:
            data_arr = data_arr.squeeze(dim, drop=True)

    data_arr = data_arr.transpose(time_coord, "lat", "lon")
    data_np = np.array(data_arr, dtype=float)

    times = pd.DatetimeIndex(ds[time_coord].values)
    lats  = ds.lat.values
    lons  = ds.lon.values
    nrow  = len(lats)
    ncol  = len(lons)

    field_tag = VARIABLE_CPT_FIELD.get(variable, variable)
    units_tag = VARIABLE_CPT_UNITS.get(variable, "unknown")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("Writing full-obs CPT TSV: %s (%d days, %d×%d grid)",
             out_path.name, len(times), nrow, ncol)

    with out_path.open("w", encoding="ascii") as fh:
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")

        for ti, t in enumerate(times):
            day_data = _mask_missing(data_np[ti])
            t_tag = t.strftime("%Y-%m-%d")

            fh.write(
                f"cpt:field={field_tag},"
                f" cpt:T={t_tag},"
                f" cpt:nrow={nrow},"
                f" cpt:ncol={ncol},"
                f" cpt:row=Y,"
                f" cpt:col=X,"
                f" cpt:units={units_tag},"
                f" cpt:missing={int(MISSING)}\n"
            )
            # Lon header row
            fh.write("\t" + "\t".join(_fmt(lon) for lon in lons) + "\n")
            # Data rows
            for ri in range(nrow):
                row = "\t".join(_fmt(day_data[ri, ci]) for ci in range(ncol))
                fh.write(f"{_fmt(lats[ri])}\t{row}\n")

    log.info("Full-obs TSV written: %d fields (%s – %s)",
             len(times), times[0].strftime("%Y-%m-%d"),
             times[-1].strftime("%Y-%m-%d"))


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _mask_missing(arr: np.ndarray) -> np.ndarray:
    """Replace NaN and fill values with MISSING sentinel."""
    arr = np.where(np.isnan(arr), MISSING, arr)
    arr = np.where(arr < -900, MISSING, arr)
    return arr


def _fmt(val) -> str:
    """Format a float to 6 decimal places, right-padded for alignment.
    Explicitly casts numpy scalars to Python float to avoid format errors.
    """
    return f"{float(val):14.6f}"
