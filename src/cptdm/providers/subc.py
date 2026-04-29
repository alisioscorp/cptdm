"""
cptdm.providers.subc
~~~~~~~~~~~~~~~~~~~~
SubC (Subseasonal Consortium) provider.

Real-time forecasts:
  HTTP download of multi-model ensemble statistics from the SubC data server
  at weather.ou.edu.  Files are pre-processed anomaly fields on a regular
  lat/lon grid, issued weekly (typically Thursdays).

Hindcasts:
  Per-model hindcast data are available from the IRI Data Library (IRIDL)
  via OPeNDAP.  Note: IRIDL is scheduled for decommissioning in April 2026.
  CPT-DM will ship a local cache of the longest-common-period hindcast
  data for supported models once the IRIDL archive has been secured.

Data server layout:
  {base_url}/{YYYYMMDD}/fcst_{YYYYMMDD}.anom.{var}_{level}.{estat}.nc
  where:
    var   = pr (precip), tas (2m temp), zg, psl, rlut, ts, ua, va
    level = sfc, 2m, 500, 200, 850, msl, toa
    estat = emean, emin, emax

Init dates: weekly (roughly every Thursday), from 2023-05-04 to present.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
import xarray as xr

from cptdm.registry.loader import DatasetEntry
from cptdm.request import Request

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SubC variable mapping:  CPT-DM variable  →  (subc_var, subc_level)
# ---------------------------------------------------------------------------
_VAR_MAP = {
    "prcp":  ("pr",  "sfc"),
    "tmean": ("tas", "2m"),
}

# The ensemble statistic we use by default (multi-model ensemble mean)
_DEFAULT_ESTAT = "emean"

# Maximum number of days away from the requested init to search for an
# available init date on the server.
_MAX_SEARCH_DAYS = 21

# Recommended maximum distance (days) between requested and actual init.
# If exceeded, print a warning — but proceed anyway.
_DEKAD_DAYS = 10


class SubCProvider:
    """Download subseasonal forecast data from the SubC data server."""

    def __init__(self, entry: DatasetEntry) -> None:
        self.entry = entry
        self.params: dict[str, Any] = entry.subc_params or {}
        self.base_url = self.params.get(
            "base_url",
            "https://weather.ou.edu/~kpegion/subc/forecasts/data",
        )

    # ------------------------------------------------------------------
    # Public API  (matches CDS/HTTP provider interface)
    # ------------------------------------------------------------------

    def fetch(
        self,
        request: Request,
        cache_dir: Path,
    ) -> dict[str, xr.Dataset]:
        """
        Download (or load from cache) the SubC ensemble-mean forecast for
        the requested init date and variable.

        Returns dict with key "forecast" → xr.Dataset.
        Hindcast fetching is not yet implemented (pending IRIDL migration).
        """
        variable = request.variable
        if variable not in _VAR_MAP:
            raise ValueError(
                f"SubC provider does not support variable '{variable}'. "
                f"Supported: {', '.join(_VAR_MAP.keys())}"
            )

        subc_var, subc_level = _VAR_MAP[variable]

        # ── Resolve the closest available init date ───────────────────
        requested_init = request.init
        actual_init = self._find_closest_init(requested_init)

        if actual_init is None:
            raise FileNotFoundError(
                f"No SubC forecast found within {_MAX_SEARCH_DAYS} days of "
                f"requested init {requested_init.isoformat()}. "
                f"SubC forecasts are typically issued weekly on Thursdays."
            )

        delta_days = abs((actual_init - requested_init).days)
        if delta_days > 0:
            log.info(
                "Requested init %s not available on SubC server. "
                "Using closest available init: %s (%d day(s) away).",
                requested_init.isoformat(),
                actual_init.isoformat(),
                delta_days,
            )
        if delta_days > _DEKAD_DAYS:
            log.warning(
                "The closest available SubC init (%s) is %d days from the "
                "requested date (%s).  We recommend using initialisations "
                "no more than about a dekad (~10 days) apart for sub-seasonal "
                "forecasting.  Proceeding anyway.",
                actual_init.isoformat(),
                delta_days,
                requested_init.isoformat(),
            )

        # ── Download the NetCDF ───────────────────────────────────────
        init_str = actual_init.strftime("%Y%m%d")
        estat = self.params.get("estat", _DEFAULT_ESTAT)
        filename = f"fcst_{init_str}.anom.{subc_var}_{subc_level}.{estat}.nc"
        url = f"{self.base_url}/{init_str}/{filename}"

        nc_path = cache_dir / "subc" / filename
        nc_path.parent.mkdir(parents=True, exist_ok=True)

        if nc_path.exists() and not request.no_cache:
            log.info("Loading SubC forecast from cache: %s", nc_path)
        else:
            log.info("Downloading SubC forecast: %s", url)
            self._download(url, nc_path)

        ds = xr.open_dataset(nc_path)

        # ── Week selection: slice to requested weeks ──────────────
        # SubC NetCDFs have a time/lead dim with 4 steps (week1–4).
        # Select only the weeks matching the requested lead so that
        # downstream aggregation operates on the correct window.
        ds = self._select_weeks(ds, request)

        return {"forecast": ds}

    # ------------------------------------------------------------------
    # Week selection
    # ------------------------------------------------------------------

    @staticmethod
    def _select_weeks(ds: xr.Dataset, request: Request) -> xr.Dataset:
        """
        Select the time steps corresponding to the requested intraseasonal
        lead from a SubC 4-week forecast.

        SubC files store weeks 1–4 along a time-like dim (usually 'time'
        or 'lead').  Week indices: 0=week1, 1=week2, 2=week3, 3=week4.

        Lead labels and their week indices:
          week1  → [0]      week2  → [1]
          week3  → [2]      week4  → [3]
          week34 → [2, 3]   weeks1-4 → [0, 1, 2, 3] (all)
        """
        if request.intra_lead is None:
            return ds

        label = request.intra_lead.label
        week_idx_map = {
            "week1":    [0],
            "week2":    [1],
            "week3":    [2],
            "week4":    [3],
            "week34":   [2, 3],
            "weeks1-4": [0, 1, 2, 3],
        }

        indices = week_idx_map.get(label)
        if indices is None:
            # Day-range or unrecognised label — return full dataset,
            # let the pipeline handle the temporal subsetting
            log.debug("SubC week selection: label '%s' not in map; returning all weeks", label)
            return ds

        # Find the time-like dimension (time, lead, step)
        time_dim = None
        for candidate in ("time", "lead", "step", "valid_time"):
            if candidate in ds.dims:
                time_dim = candidate
                break

        if time_dim is None or ds.sizes.get(time_dim, 0) <= 1:
            log.debug("SubC week selection: no multi-step time dim; skipping")
            return ds

        n_steps = ds.sizes[time_dim]
        if n_steps < 4:
            log.warning(
                "SubC file has %d time steps (expected 4 for weeks 1–4). "
                "Selecting from available steps.", n_steps
            )
            indices = [i for i in indices if i < n_steps]

        if not indices:
            log.warning("No valid week indices after filtering; returning full dataset")
            return ds

        ds = ds.isel({time_dim: indices})
        log.info("SubC week selection: lead=%s → selected indices %s from dim '%s'",
                 label, indices, time_dim)
        return ds

    # ------------------------------------------------------------------
    # Init-date discovery
    # ------------------------------------------------------------------

    def _find_closest_init(self, target: date) -> date | None:
        """
        Search the SubC server for the closest available init date to
        *target*, checking the target date itself first, then alternating
        before/after up to _MAX_SEARCH_DAYS away.

        Uses HEAD requests to probe directory existence (lightweight).
        """
        # Check target first
        if self._init_exists(target):
            return target

        for delta in range(1, _MAX_SEARCH_DAYS + 1):
            # Check before
            before = target - timedelta(days=delta)
            if self._init_exists(before):
                return before
            # Check after
            after = target + timedelta(days=delta)
            if self._init_exists(after):
                return after

        return None

    def _init_exists(self, d: date) -> bool:
        """Check whether a directory for init date *d* exists on the server."""
        dir_url = f"{self.base_url}/{d.strftime('%Y%m%d')}/"
        try:
            resp = requests.head(dir_url, timeout=10, allow_redirects=True)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # Download helper
    # ------------------------------------------------------------------

    @staticmethod
    def _download(url: str, dest: Path) -> None:
        """Download a file from *url* to *dest* with progress logging."""
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):  # 1 MiB
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = 100 * downloaded / total
                    log.debug("  %.0f%% (%d / %d bytes)", pct, downloaded, total)

        log.info("Saved %s (%d bytes)", dest.name, downloaded)

    # ------------------------------------------------------------------
    # Init-date listing (utility — not required for fetch, but useful
    # for the CLI to display available dates when the user queries)
    # ------------------------------------------------------------------

    def list_available_inits(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> list[date]:
        """
        Scrape the top-level directory listing to enumerate all available
        init dates.  Optionally filter to [start, end] range.

        This is an expensive operation (one HTTP GET); use sparingly.
        """
        resp = requests.get(f"{self.base_url}/", timeout=30)
        resp.raise_for_status()

        import re as _re
        dates: list[date] = []
        for m in _re.finditer(r'href="(\d{8})/"', resp.text):
            d = _parse_yyyymmdd(m.group(1))
            if d is None:
                continue
            if start and d < start:
                continue
            if end and d > end:
                continue
            dates.append(d)

        dates.sort()
        return dates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_yyyymmdd(s: str) -> date | None:
    """Parse YYYYMMDD string to date, returning None on failure."""
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None
