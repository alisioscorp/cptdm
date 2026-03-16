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
        tgt_m_start = ((init_m - 1 + request.lead.lead_start) % 12) + 1
        tgt_m_end   = ((init_m - 1 + request.lead.lead_end)   % 12) + 1
    else:
        tgt_m_start = tgt_m_end = init_m

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="ascii") as fh:
        # ── File header ────────────────────────────────────────────────
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")

        # ── One block per year ─────────────────────────────────────────
        for ti, t in enumerate(times):
            year_data = _mask_missing(data_np[ti])

            # Init (S) and target (T) dates for this year
            init_year = t.year
            # Target year: same as init unless season crosses Dec→Jan
            tgt_year = init_year + (1 if tgt_m_start < init_m and request.lead and request.lead.lead_start > 0 else 0)

            s_tag = f"{init_year}-{init_m:02d}-01"
            t_tag = f"{tgt_year}-{tgt_m_start:02d}/{tgt_m_end:02d}"

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

    init_m = request.init.month
    if request.lead:
        tgt_m_start = ((init_m - 1 + request.lead.lead_start) % 12) + 1
        tgt_m_end   = ((init_m - 1 + request.lead.lead_end)   % 12) + 1
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

    Format (one block per year, each with a single row/col):
      xmlns:cpt=http://iri.columbia.edu/CPT/v10/
      cpt:nfields=1
      cpt:field=index, cpt:T=YYYY-MM/MM, cpt:S=YYYY-MM-DD,
          cpt:nrow=1, cpt:ncol=1, cpt:row=T, cpt:col=index,
          cpt:units=dimensionless, cpt:missing=-999
      \tindex
      <date>\t<value>
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

    init_m = request.init.month
    if request.lead:
        tgt_m_start = ((init_m - 1 + request.lead.lead_start) % 12) + 1
        tgt_m_end   = ((init_m - 1 + request.lead.lead_end)   % 12) + 1
    else:
        tgt_m_start = tgt_m_end = init_m

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="ascii") as fh:
        fh.write(CPT_XMLNS + "\n")
        fh.write("cpt:nfields=1\n")

        for ti, (t, val) in enumerate(zip(times, values)):
            init_year = t.year
            tgt_year  = init_year + (1 if tgt_m_start < init_m and request.lead
                                     and request.lead.lead_start > 0 else 0)
            s_tag = f"{init_year}-{init_m:02d}-01"
            t_tag = f"{tgt_year}-{tgt_m_start:02d}/{tgt_m_end:02d}"

            fh.write(
                f"cpt:field=index,"
                f" cpt:T={t_tag},"
                f" cpt:S={s_tag},"
                f" cpt:nrow=1,"
                f" cpt:ncol=1,"
                f" cpt:row=T,"
                f" cpt:col=index,"
                f" cpt:units=dimensionless,"
                f" cpt:missing={int(MISSING)}\n"
            )
            fh.write("\tindex\n")
            fh.write(f"{s_tag}\t{_fmt(float(val.item() if hasattr(val, 'item') else val))}\n")


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
