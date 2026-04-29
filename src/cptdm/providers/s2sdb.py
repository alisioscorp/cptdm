"""
cptdm.providers.s2sdb
~~~~~~~~~~~~~~~~~~~~~
S2S Database provider (intraseasonal forecasts via ECMWF archive).

The S2S (Sub-seasonal to Seasonal) prediction database is hosted by ECMWF
and provides ensemble forecasts and reforecasts from 11+ operational centres.

Data access (in priority order):
  1. ECDS (https://ecds.ecmwf.int) via cdsapi — the new ECMWF Climate Data
     Store, which replaced the legacy Web API for S2S access as of 2026-04.
     Credentials: ~/.ecdsapirc (url + key) or ECDS_API_KEY env var.
  2. MARS via ECMWFDataServer (~/.ecmwfapirc) — legacy fallback, scheduled
     for retirement by ECMWF. Migrate to ECDS when possible.

ECDS credentials file (~/.ecdsapirc) format:
  url: https://ecds.ecmwf.int/api
  key: <your-uid>:<your-api-key>

S2S archive structure:
  - All model outputs on a common 1.5° × 1.5° lat/lon grid
  - dataset="s2s", stream="enfo" (both forecasts and reforecasts)
  - type="cf" (control forecast)
  - tp (param 228228): accumulated total precipitation, kg m**-2 (= mm)
  - 2t (param 167): 2m temperature, K
  - Steps: 6-hourly accumulations up to 1080h (45 days)
  - Init frequency: twice-weekly (Mon/Thu) for ECMWF; varies by centre
  - Embargo: ~2-3 days for recent inits (centre-dependent)

Usage in CPT-DM:
  --timescale intraseasonal --dataset s2sdb_ecmwf
  --init YYYY-MM-DD  (fetch all available inits in that month)
  --lead week1 | week2 | week3 | week4 | week34

GRIB output structure (confirmed from live tests):
  dims: (time, step, latitude, longitude)
  time: datetime64 init dates
  step: timedelta64 nanoseconds (86400000000000 = 1 day)
  tp:   accumulated precipitation from init, kg m**-2

Auth:
  ECDS (preferred): ~/.ecdsapirc  with url + key  OR  ECDS_API_KEY env var
  MARS (fallback):  ~/.ecmwfapirc with url + key + email

References:
  https://confluence.ecmwf.int/display/S2S/Models
  https://confluence.ecmwf.int/display/S2S/Parameters
  https://confluence.ecmwf.int/display/UDOC/MARS+access+restrictions#s2s
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from cptdm.registry.loader import DatasetEntry
from cptdm.request import Request

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# S2S centre codes  (MARS "origin" keyword)
# ---------------------------------------------------------------------------
_CENTRE_CODES = {
    "ecmwf":   "ecmf",
    "ncep":    "kwbc",
    "eccc":    "cwao",
    "ukmo":    "egrr",
    "jma":     "rjtd",
    "cma":     "babj",
    "bom":     "ammc",
    "cnrm":    "lfpw",
    "isac":    "isac",
    "cptec":   "sbsj",
    "hmcr":    "rums",
    "kma":     "rksl",
    "iap_cas": "anso",
}

# MARS parameter codes
_MARS_PARAM = {
    "tp":  "228228",   # total precipitation (accumulated), kg m**-2
    "2t":  "167",      # 2m temperature, K
}

# CPT-DM variable → MARS shortName
_VAR_MAP = {
    "prcp":  "tp",
    "tmean": "2t",
}

# Embargo buffer — skip inits this many days before today
_EMBARGO_DAYS = 3


class S2SDBProvider:
    """Download sub-seasonal forecasts/hindcasts from the S2S MARS archive."""

    def __init__(self, entry: DatasetEntry) -> None:
        self.entry = entry
        self.params: dict[str, Any] = entry.s2sdb_params or {}
        self.model = self.params.get("model", "ecmwf").lower()
        self.origin = _CENTRE_CODES.get(self.model, self.model)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self,
        request: Request,
        cache_dir: Path,
    ) -> dict[str, xr.Dataset]:
        """
        Fetch S2S forecast data for the requested init month and lead window.

        All available Mon/Thu init dates in the init month are requested in
        a single MARS call, returning dims (time, step, lat, lon).
        The lead window is applied by differencing accumulated steps.

        Returns dict with key "forecast" -> xr.Dataset with dims
        (time, lat, lon) after step aggregation, where time = init dates.
        """
        variable = request.variable
        if variable not in _VAR_MAP:
            raise ValueError(
                f"S2SDB provider does not support variable '{variable}'. "
                f"Supported: {', '.join(_VAR_MAP.keys())}"
            )

        mars_var   = _VAR_MAP[variable]
        mars_param = _MARS_PARAM[mars_var]

        if request.intra_lead is None:
            raise ValueError("S2SDB provider requires an intraseasonal lead.")

        # ── Check archive availability ──────────────────────────────
        avail_bounds = self.entry.get_available_bounds()
        if avail_bounds and request.init.year > avail_bounds[1]:
            raise ValueError(
                f"{self.entry.label} real-time archive ends {avail_bounds[1]}. "
                f"Requested init year {request.init.year} is beyond the archive. "
                f"Use --init with a year <= {avail_bounds[1]}."
            )

        # ── Resolve init dates for the requested month ─────────────
        init_dates = self._get_init_dates(request.init)

        if not init_dates:
            embargo = self.params.get("embargo_days", _EMBARGO_DAYS)
            raise ValueError(
                f"No available S2S init dates in "
                f"{request.init.year}-{request.init.month:02d} "
                f"(all dates within {embargo}-day embargo or "
                f"outside archive)."
            )

        log.info("S2S %s: %d init dates in %d-%02d: %s … %s",
                 self.model.upper(), len(init_dates),
                 request.init.year, request.init.month,
                 init_dates[0].isoformat(), init_dates[-1].isoformat())

        # ── Lead window in days ─────────────────────────────────────
        lead_start_day = (request.intra_lead.tgt_start - request.init).days
        lead_end_day   = (request.intra_lead.tgt_end   - request.init).days

        # ── Cache ───────────────────────────────────────────────────
        cache_path = self._cache_path(
            cache_dir, request.init, mars_var, lead_start_day, lead_end_day
        )

        if cache_path.exists() and not request.no_cache:
            log.info("Loading S2S data from cache: %s", cache_path)
        else:
            self._retrieve_via_mars(
                init_dates=init_dates,
                param=mars_param,
                step_end_day=lead_end_day,
                target_path=cache_path,
            )

        # ── Open GRIB and aggregate over step ───────────────────────
        ds = _open_s2s_grib(cache_path)
        ds = _aggregate_lead_window(ds, variable, lead_start_day, lead_end_day)

        return {"forecast": ds}

    # ------------------------------------------------------------------
    # Init date resolution
    # ------------------------------------------------------------------

    def _get_init_dates(self, ref_date: date) -> list[date]:
        """
        Return all valid S2S init dates in the same year-month as ref_date,
        excluding dates within the embargo window.
        """
        freq = self.params.get("init_frequency", "twice_weekly")
        year, month = ref_date.year, ref_date.month
        embargo = self.params.get("embargo_days", _EMBARGO_DAYS)
        cutoff = date.today() - timedelta(days=embargo)

        _, n_days = calendar.monthrange(year, month)
        all_days = [date(year, month, d) for d in range(1, n_days + 1)]

        date_type = self.params.get("date_type", "list")
        if date_type == "range_weekly":
            # Weekly from day 1 of month (every 7 days)
            candidates = [date(year, month, 1) + timedelta(days=7*i)
                          for i in range(5)
                          if (date(year, month, 1) + timedelta(days=7*i)).month == month]
        elif freq == "twice_weekly":
            candidates = [d for d in all_days if d.weekday() in (0, 3)]
        elif freq == "weekly":
            candidates = [d for d in all_days if d.weekday() == 3]
        elif freq == "daily":
            candidates = all_days
        else:
            candidates = [d for d in all_days if d.weekday() in (0, 3)]

        valid = [d for d in candidates if d <= cutoff]
        if not valid:
            log.info(
                "S2S %s: no init dates in %d-%02d within embargo window "                "(embargo=%d days, cutoff=%s)",
                self.model.upper(), year, month, embargo, cutoff.isoformat()
            )
        return valid

    # ------------------------------------------------------------------
    # MARS retrieval
    # ------------------------------------------------------------------

    def _retrieve_via_mars(
        self,
        init_dates: list[date],
        param: str,
        step_end_day: int,
        target_path: Path,
    ) -> None:
        """
        Retrieve S2S data from ECDS (preferred) or MARS (fallback).

        ECDS (https://ecds.ecmwf.int) is the new ECMWF Climate Data Store,
        which replaced the legacy ECMWF Web API for S2S access as of 2026-04.
        ECDS uses the same cdsapi client as CDS but with a separate endpoint
        and credentials file (~/.ecdsapirc).

        If ECDS credentials are available (via ECDS_API_KEY env var or
        ~/.ecdsapirc), the request is routed through ECDS.  Otherwise, the
        legacy ECMWFDataServer (MARS / ~/.ecmwfapirc) is used as a fallback
        for as long as it remains operational.

        Requests steps 0 through step_end_day at 6h intervals so that
        accumulated differencing can be applied downstream.
        """
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Centre-specific request format from registry
        step_type  = self.params.get("step_type",  "point")   # "point" or "range"
        step_hours = self.params.get("step_hours", 6)          # resolution in hours
        date_type  = self.params.get("date_type",  "list")    # "list" or "range"

        # Build date string
        if date_type == "range":
            first = init_dates[0]
            last  = init_dates[-1]
            date_str = f"{first.strftime('%Y-%m-%d')}/to/{last.strftime('%Y-%m-%d')}"
        elif date_type == "range_weekly":
            first = date(init_dates[0].year, init_dates[0].month, 1)
            last  = init_dates[-1]
            date_str = f"{first.strftime('%Y-%m-%d')}/to/{last.strftime('%Y-%m-%d')}/by/7"
        else:
            date_str = "/".join(d.strftime("%Y-%m-%d") for d in init_dates)

        # Build step string
        if step_type == "range":
            step_ranges = [
                f"{s}-{s + step_hours}"
                for s in range(0, step_end_day * 24, step_hours)
            ]
            step_str = "/".join(step_ranges)
        else:
            steps    = list(range(0, step_end_day * 24 + 1, step_hours))
            step_str = "/".join(str(s) for s in steps)

        log.info("S2S: origin=%s  date=%s  step_type=%s  steps 0–%dh",
                 self.origin, date_str[:20] + ("..." if len(date_str) > 20 else ""),
                 step_type, step_end_day * 24)

        # ── Route: ECDS first, MARS fallback ───────────────────────
        from cptdm.auth.credentials import validate_ecds_credentials

        if validate_ecds_credentials():
            self._retrieve_via_ecds(
                date_str=date_str,
                param=param,
                step_str=step_str,
                target_path=target_path,
            )
        else:
            log.info(
                "No ECDS credentials found (~/.ecdsapirc or ECDS_API_KEY). "
                "Falling back to MARS (ECMWFDataServer). "
                "Note: ECMWF Web API is scheduled for retirement. "
                "See README for ECDS migration instructions."
            )
            self._retrieve_via_ecmwf_legacy(
                date_str=date_str,
                param=param,
                step_str=step_str,
                target_path=target_path,
            )

        if target_path.stat().st_size == 0:
            target_path.unlink(missing_ok=True)
            raise ValueError(
                f"S2S retrieval returned empty GRIB for {self.entry.label} "
                f"({date_str[:30]}). This likely means the requested dates "
                f"are within the centre embargo window. Try an earlier --init month."
            )
        log.info("Saved S2S GRIB: %s", target_path.name)

    def _retrieve_via_ecds(
        self,
        date_str: str,
        param: str,
        step_str: str,
        target_path: Path,
    ) -> None:
        """
        Retrieve S2S data via ECDS using cdsapi.Client.

        ECDS endpoint: https://ecds.ecmwf.int/api
        Credentials:   ~/.cdsapirc  with url=https://ecds.ecmwf.int/api + key
                       OR  CDSAPI_URL + CDSAPI_KEY env vars.

        The ECDS S2S dataset is 's2s-forecasts'. Its request schema differs
        from MARS: init dates are specified as year/month/day lists, and
        steps are specified as leadtime_hour string lists (zero-padded to 3
        digits, e.g. '006', '024').

        Variable mapping (MARS param → ECDS variable name):
          228228 (tp)  → 'total_precipitation'
          167    (2t)  → '2m_temperature'
        """
        try:
            import cdsapi
        except ImportError:
            raise ImportError(
                "The 'cdsapi' package is required for ECDS access. "
                "Install with: pip install cdsapi"
            )

        # MARS param code → ECDS long variable name
        _ECDS_VAR = {
            "228228": "total_precipitation",
            "167":    "2m_temperature",
        }
        ecds_var = _ECDS_VAR.get(param)
        if ecds_var is None:
            raise ValueError(
                f"ECDS variable mapping not defined for MARS param '{param}'. "
                f"Known: {list(_ECDS_VAR.keys())}"
            )

        # Parse date_str (MARS format) back to individual init dates
        # Accepted formats: 'YYYY-MM-DD/YYYY-MM-DD/...' or 'YYYY-MM-DD/to/YYYY-MM-DD[/by/N]'
        init_dates_for_ecds = _parse_mars_date_str(date_str)

        years  = sorted(set(str(d.year)         for d in init_dates_for_ecds))
        months = sorted(set(f"{d.month:02d}"    for d in init_dates_for_ecds))
        days   = sorted(set(f"{d.day:02d}"      for d in init_dates_for_ecds))

        # Parse step_str back to hours and build zero-padded leadtime_hour list
        # Accepted formats: 'H/H/H/...' (point steps) or 'H-H/H-H/...' (ranges)
        leadtime_hours = _mars_step_str_to_ecds_leadtime(step_str)

        # cdsapi.Client() reads ~/.cdsapirc automatically;
        # passing url/key explicitly only if env vars are set to override.
        import os
        # Build cdsapi client. Resolution order:
        #   1. CDSAPI_URL + CDSAPI_KEY env vars (explicit override)
        #   2. ~/.ecdsapirc (dedicated ECDS file — parse url/key explicitly
        #      since cdsapi only auto-reads ~/.cdsapirc)
        #   3. ~/.cdsapirc already pointing at ECDS — cdsapi auto-reads it
        from cptdm.auth.credentials import resolve_ecds_credentials
        import yaml as _yaml

        url = os.environ.get("CDSAPI_URL")
        key = os.environ.get("CDSAPI_KEY")
        if url and key:
            client = cdsapi.Client(url=url, key=key, quiet=True, progress=False)
        else:
            ecds_path = resolve_ecds_credentials()
            if ecds_path is not None and ecds_path.name == ".ecdsapirc":
                # Parse url and key from ~/.ecdsapirc explicitly
                rc_text = ecds_path.read_text(encoding="utf-8")
                rc = {}
                for line in rc_text.splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        rc[k.strip()] = v.strip()
                client = cdsapi.Client(
                    url=rc.get("url", "https://ecds.ecmwf.int/api"),
                    key=rc.get("key", ""),
                    quiet=True, progress=False,
                )
            else:
                # ~/.cdsapirc already points at ECDS — cdsapi auto-reads it
                client = cdsapi.Client(quiet=True, progress=False)

        ecds_request = {
            "origin":        self.origin,
            "forecast_type": "control_forecast",
            "level_type":    "single_level",
            "variable":      [ecds_var],
            "year":          years,
            "month":         months,
            "day":           days,
            "leadtime_hour": leadtime_hours,
            "time":          ["00:00"],
            "data_format":   "grib",
        }

        log.info("ECDS S2S retrieval: dataset=s2s-forecasts  origin=%s  var=%s  "
                 "%d init dates  %d steps",
                 self.origin, ecds_var, len(init_dates_for_ecds), len(leadtime_hours))
        log.debug("ECDS request: %s", ecds_request)

        client.retrieve("s2s-forecasts", ecds_request, str(target_path))

    def _retrieve_via_ecmwf_legacy(
        self,
        date_str: str,
        param: str,
        step_str: str,
        target_path: Path,
    ) -> None:
        """
        Retrieve S2S data via legacy ECMWF Web API (ECMWFDataServer / MARS).

        Requires ~/.ecmwfapirc with url, key, and email.
        This API is scheduled for retirement by ECMWF; migrate to ECDS when
        possible (create ~/.ecdsapirc — see README).
        """
        mars_request = {
            "class":   "s2",
            "dataset": "s2s",
            "date":    date_str,
            "expver":  "prod",
            "levtype": "sfc",
            "model":   "glob",
            "origin":  self.origin,
            "param":   param,
            "step":    step_str,
            "stream":  "enfo",
            "time":    "00:00:00",
            "type":    "cf",
            "expect":  "any",
            "target":  str(target_path),
        }

        log.debug("MARS request: %s", mars_request)

        try:
            from ecmwfapi import ECMWFDataServer
        except ImportError:
            raise ImportError(
                "The 'ecmwfapi' package is required for MARS access. "
                "Install with: pip install ecmwf-api-client\n"
                "Credentials: ~/.ecmwfapirc (url, key, email)\n"
                "Alternatively, migrate to ECDS: create ~/.ecdsapirc — see README."
            )

        log.info("MARS (legacy) S2S retrieval: origin=%s", self.origin)
        server = ECMWFDataServer()
        server.retrieve(mars_request)

    # ------------------------------------------------------------------
    # Cache path
    # ------------------------------------------------------------------

    def _cache_path(
        self,
        cache_dir: Path,
        ref_date: date,
        param: str,
        lead_start: int,
        lead_end: int,
    ) -> Path:
        month_str = ref_date.strftime("%Y%m")
        filename  = (
            f"s2s_{self.origin}_{month_str}_{param}"
            f"_d{lead_start}-{lead_end}.grib"
        )
        return cache_dir / "s2sdb" / filename

    # ------------------------------------------------------------------
    # Utilities (public, for CLI use)
    # ------------------------------------------------------------------

    def get_init_frequency(self) -> str:
        return self.params.get("init_frequency", "twice_weekly")

    def suggest_nearest_init(
        self, target: date
    ) -> tuple[date | None, date | None]:
        freq = self.get_init_frequency()
        if freq == "twice_weekly":
            return (
                _prev_weekday(target, (0, 3)),
                _next_weekday(target, (0, 3)),
            )
        elif freq == "weekly":
            return (
                _prev_weekday(target, (3,)),
                _next_weekday(target, (3,)),
            )
        elif freq == "daily":
            return target, target
        return None, None


# ---------------------------------------------------------------------------
# ECDS request translation helpers
# ---------------------------------------------------------------------------

def _parse_mars_date_str(date_str: str) -> list[date]:
    """
    Parse a MARS-format date string back to a list of date objects.

    Accepts:
      'YYYY-MM-DD/YYYY-MM-DD/...'             (explicit list)
      'YYYY-MM-DD/to/YYYY-MM-DD'              (contiguous range)
      'YYYY-MM-DD/to/YYYY-MM-DD/by/N'         (range with step N days)
    """
    from datetime import date as _date

    parts = date_str.split("/")

    if "to" in parts:
        to_idx = parts.index("to")
        start  = _date.fromisoformat(parts[to_idx - 1])
        end    = _date.fromisoformat(parts[to_idx + 1])
        step   = 1
        if "by" in parts:
            by_idx = parts.index("by")
            step   = int(parts[by_idx + 1])
        out = []
        d = start
        while d <= end:
            out.append(d)
            d += timedelta(days=step)
        return out

    # Explicit slash-separated list
    return [_date.fromisoformat(p) for p in parts if p]


def _mars_step_str_to_ecds_leadtime(step_str: str) -> list[str]:
    """
    Convert a MARS step string to a list of zero-padded ECDS leadtime_hour strings.

    Accepts:
      Point steps:  '0/6/12/18/...'        → ['000', '006', '012', '018', ...]
      Range steps:  '0-6/6-12/12-18/...'   → end-hours of each range
                    ['006', '012', '018', ...]
    """
    parts = step_str.split("/")
    hours: list[int] = []

    for p in parts:
        if "-" in p:
            # Range accumulation: take the end hour
            end_h = int(p.split("-")[1])
            hours.append(end_h)
        else:
            hours.append(int(p))

    # Deduplicate, sort, zero-pad to 3 digits
    return [f"{h:03d}" for h in sorted(set(hours))]


# ---------------------------------------------------------------------------
# GRIB helpers
# ---------------------------------------------------------------------------

def _open_s2s_grib(path: Path) -> xr.Dataset:
    """
    Open a S2S GRIB file as an xarray Dataset via cfgrib.
    Returns dataset with latitude/longitude renamed to lat/lon.

    cfgrib writes a .idx index file alongside the GRIB. Inside a Nuitka
    onefile binary the working directory at index-creation time is a temp
    extraction path; the index stores that path and cfgrib fails to reopen
    it on the next run. Fix: delete the stale .idx before every open so
    cfgrib always rebuilds it with the current working directory, and pass
    the GRIB path as an absolute string so the new index is valid.
    """
    try:
        import cfgrib
    except ImportError:
        raise ImportError(
            "cfgrib is required to read S2S GRIB files. "
            "Install with: pip install cfgrib eccodes"
        )

    # cfgrib calls os.getcwd() internally via posixpath.abspath() to resolve
    # the GRIB path. Inside a Nuitka onefile binary the temp extraction dir
    # is not a real filesystem path and getcwd() fails with FileNotFoundError.
    # Fix: chdir to the GRIB's parent before calling cfgrib, then restore.
    # Also delete stale .idx files first — cfgrib appends a hash suffix
    # (e.g. .5b7b6.idx) and the stored path becomes invalid across runs.
    import os
    abs_path = path.resolve()

    for stale in abs_path.parent.glob(abs_path.name + "*.idx"):
        try:
            stale.unlink()
        except OSError:
            pass

    orig_dir = None
    try:
        orig_dir = os.getcwd()
    except OSError:
        pass

    try:
        os.chdir(abs_path.parent)
        datasets = cfgrib.open_datasets(str(abs_path))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to open S2S GRIB file {path.name}: {exc}"
        ) from exc
    finally:
        if orig_dir is not None:
            try:
                os.chdir(orig_dir)
            except OSError:
                pass

    if not datasets:
        raise ValueError(f"cfgrib found no datasets in {path}")

    # Pick dataset with spatial dims
    ds = next(
        (c for c in datasets if "latitude" in c.dims or "longitude" in c.dims),
        datasets[0],
    )

    # Rename spatial coords
    renames = {}
    if "latitude" in ds.dims:
        renames["latitude"] = "lat"
    if "longitude" in ds.dims:
        renames["longitude"] = "lon"
    if renames:
        ds = ds.rename(renames)

    log.debug("Opened S2S GRIB: %s  dims=%s  vars=%s",
              path.name, dict(ds.sizes), list(ds.data_vars))
    return ds


def _aggregate_lead_window(
    ds: xr.Dataset,
    variable: str,
    lead_start_day: int,
    lead_end_day: int,
) -> xr.Dataset:
    """
    Reduce the step dimension to produce one spatial field per init date.

    Precipitation (accumulated):
      window_total = accum[day_end] - accum[day_start - 1]

    Temperature (instantaneous):
      window_mean = mean of steps within [day_start, day_end]

    Steps in GRIB are timedelta64[ns]; 1 day = 86_400_000_000_000 ns.
    """
    pvar = list(ds.data_vars)[0]
    da   = ds[pvar]

    step_coord = "step"
    if step_coord not in da.dims:
        log.debug("_aggregate_lead_window: no step dim, returning as-is")
        return ds

    ns_per_day = 86_400 * 1_000_000_000
    step_vals  = ds[step_coord].values          # timedelta64[ns]
    step_days  = np.array([int(s) / ns_per_day for s in step_vals])

    if variable == "prcp":
        # Accumulated field — difference end accum minus start-1 accum
        idx_end   = int(np.argmin(np.abs(step_days - lead_end_day)))
        idx_start = int(np.argmin(np.abs(step_days - max(lead_start_day - 1, 0))))

        result = da.isel({step_coord: idx_end}) - da.isel({step_coord: idx_start})
        result.attrs         = da.attrs.copy()
        result.attrs["units"] = "mm"
        result.attrs["aggregation"] = (
            f"accumulated prcp days {lead_start_day}–{lead_end_day}"
        )
    else:
        # Instantaneous field — mean over window
        mask = (step_days >= lead_start_day) & (step_days <= lead_end_day)
        if not mask.any():
            log.warning("No steps in window %d–%d; using all", lead_start_day, lead_end_day)
            mask = np.ones(len(step_days), dtype=bool)
        result = da.isel({step_coord: np.where(mask)[0]}).mean(
            dim=step_coord, skipna=True
        )
        result.attrs = da.attrs.copy()
        result.attrs["aggregation"] = (
            f"mean {variable} days {lead_start_day}–{lead_end_day}"
        )

    # Rebuild dataset
    out = xr.Dataset({pvar: result}, attrs=ds.attrs)
    for coord in ("lat", "lon"):
        if coord in ds.coords:
            out = out.assign_coords({coord: ds.coords[coord]})

    # lon → -180/180
    if "lon" in out.coords and float(out.lon.max()) > 180:
        out = out.assign_coords(lon=((out.lon + 180) % 360) - 180)
        out = out.sortby("lon")

    # lat → N→S
    if "lat" in out.coords and out.lat.values[0] < out.lat.values[-1]:
        out = out.sortby("lat", ascending=False)

    log.info("S2S aggregated: var=%s  window=days%d-%d  dims=%s",
             pvar, lead_start_day, lead_end_day, dict(out.sizes))
    return out


# ---------------------------------------------------------------------------
# Weekday helpers
# ---------------------------------------------------------------------------

def _prev_weekday(d: date, weekdays: tuple[int, ...]) -> date:
    for delta in range(7):
        candidate = d - timedelta(days=delta)
        if candidate.weekday() in weekdays:
            return candidate
    return d


def _next_weekday(d: date, weekdays: tuple[int, ...]) -> date:
    for delta in range(7):
        candidate = d + timedelta(days=delta)
        if candidate.weekday() in weekdays:
            return candidate
    return d
