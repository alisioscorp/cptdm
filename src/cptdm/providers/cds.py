# =============================================================================
# cds.py — CDS provider — downloads seasonal forecast and hindcast data from the Copernicus Climate Data Store (CDS/ECDS).
# -----------------------------------------------------------------------------
# Copyright (C) 2026 Alisios Corporation — https://alisioscorporation.com
# Author       : Alisios Corporation
# Project lead : Ángel G. Muñoz (Alisios) — angel.g.munoz@alisioscorporation.com
# SEI lead     : Simon J. Mason — simon.mason@sei.org
# UKMO lead    : Nicholas Savage — nicholas.savage@metoffice.gov.uk
# Funding      : WISER Programme, UK International Development, Met Office UK
# Repository   : https://github.com/alisioscorp/cptdm
# Contract     : SEI/25-173
#
# Licensed under the Alisios Open Non-Commercial License (AONCL) v1.0.
# Free for non-commercial research, education, and public-sector use.
# Commercial use requires prior written authorisation from Alisios Corporation
# and SEI per contract SEI/25-173. Unauthorised commercial use will be subject
# to legal action. See LICENSE for full terms.
# Attribution to Alisios Corporation must be preserved in all copies,
# modifications, forks, or derivative works (contract SEI/25-173).
# =============================================================================
"""
cptdm.providers.cds
~~~~~~~~~~~~~~~~~~~
Provider for Copernicus Climate Data Store (CDS) via cdsapi.

Handles:
  - CDS seasonal forecast/hindcast (ECMWF SEAS5, NCEP CFSv2, ECCC CanSIPSv2)
  - ERA5-Land reanalysis (monthly means)

No eval() anywhere.  All request parameters come from the registry entry
and the canonical Request object.

Fetch contract:
    CDSProvider.fetch(request, cache_dir) -> dict[str, xr.Dataset]

    Returns a dict with keys:
      "hindcast"  → xr.Dataset (years × members × lat × lon)
      "forecast"  → xr.Dataset (1 member × lat × lon)
      "obs"       → xr.Dataset (years × lat × lon)   if obs_dataset_id is set
                    and obs is also a CDS source (ERA5-Land)
"""
from __future__ import annotations

import calendar
import logging
from pathlib import Path

import numpy as np
import xarray as xr

from cptdm.registry.loader import DatasetEntry
from cptdm.request import Request

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel for "not yet fetched"
# ---------------------------------------------------------------------------
_MISSING = -999.0


class CDSProvider:
    """
    Provider for CDS-sourced datasets (seasonal forecasts + ERA5-Land).
    """

    def __init__(self, entry: DatasetEntry) -> None:
        self.entry = entry
        self._cds_client = None   # lazy init; avoids import if not needed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self,
        request: Request,
        cache_dir: Path,
    ) -> dict[str, xr.Dataset]:
        """
        Download (or load from cache) and return raw xarray Datasets.
        Dispatches to fetch_reanalysis() for obs/rean datasets (ERA5-Land).
        """
        # ERA5-Land and other reanalysis datasets use a different API call.
        # Check both file_type AND absence of originating_centre (forecast-specific param).
        p_check = self.entry.cds_params or {}
        is_rean = (
            self.entry.file_type in ("reanalysis", "obs")
            or "originating_centre" not in p_check
        )
        if is_rean:
            ds = self.fetch_reanalysis(request, cache_dir)
            return {"obs": ds}

        results: dict[str, xr.Dataset] = {}

        p = self.entry.cds_params
        if p is None:
            raise ValueError(f"Dataset '{self.entry.label}' has no cds_params.")

        var_cfg = self.entry.get_variable_params(request.variable)

        # ── rfreq: requires daily data on CDS (all C3S models) ────────
        if request.variable == "rfreq":
            daily_ds = var_cfg.get("dataset_daily")
            if not daily_ds:
                raise ValueError(
                    f"rfreq (rainfall frequency) is not available for "
                    f"'{self.entry.label}'. All C3S models provide daily "
                    f"precipitation via seasonal-original-single-levels, but "
                    f"this dataset's registry entry is missing dataset_daily. "
                    f"For NMME models, use --variable prcp and pair with "
                    f"CHIRPS or CRU rfreq obs independently."
                )
            return self._fetch_rfreq_daily(request, p, var_cfg, cache_dir)

        # ── hindcast ──────────────────────────────────────────────────
        hcast_path = self._hcast_cache_path(request, cache_dir)
        if hcast_path.exists() and not request.no_cache:
            log.info("Loading hindcast from cache: %s", hcast_path)
            results["hindcast"] = xr.open_dataset(hcast_path)
        else:
            log.info("Downloading hindcast from CDS …")
            results["hindcast"] = self._fetch_hindcast(request, p, var_cfg, hcast_path)

        # ── forecast ──────────────────────────────────────────────────
        if getattr(self.entry, "hindcast_only", False):
            log.info("  Skipping forecast (%s is hindcast-only)", self.entry.label)
        else:
            fcast_path = self._fcast_cache_path(request, cache_dir)
            if fcast_path.exists() and not request.no_cache:
                log.info("Loading forecast from cache: %s", fcast_path)
                results["forecast"] = xr.open_dataset(fcast_path)
            else:
                log.info("Downloading forecast from CDS …")
                try:
                    results["forecast"] = self._fetch_forecast(
                        request, p, var_cfg, fcast_path)
                except Exception as exc:
                    err = str(exc)
                    if "MarsNoData" in err or "no data" in err.lower():
                        log.warning(
                            "No realtime forecast available on CDS for %s "
                            "init=%s.  This can happen when:\n"
                            "  • The forecast has not been posted yet (most "
                            "CDS seasonal models publish around the 13th of "
                            "each month for the current init).\n"
                            "  • The model is lagged or discontinued.\n"
                            "Producing hindcast + obs only.",
                            self.entry.label,
                            request.init.isoformat(),
                        )
                    else:
                        raise

        return results

    # ------------------------------------------------------------------
    # Hindcast download
    # ------------------------------------------------------------------

    def _fetch_hindcast(
        self,
        request: Request,
        p: dict,
        var_cfg: dict,
        out_path: Path,
    ) -> xr.Dataset:
        """
        Download the multi-year hindcast for the given init month and lead(s).
        Returns raw xr.Dataset (no unit conversion yet).
        """
        clim_start = request.clim_start
        clim_end = request.clim_end
        if clim_start is None or clim_end is None:
            # Fallback to registry default
            if self.entry.clim_years:
                clim_start, clim_end = self.entry.clim_years
            else:
                raise ValueError("No climatology years specified and no default in registry.")

        # Use the registry's available_years to expand the year list,
        # skipping any gaps in the archive (v0.7.0 multi-range support).
        years_all = self.entry.expand_available_years(clim_start, clim_end)

        # Resolve lead months
        if request.timescale == "seasonal" and request.lead:
            leadtime_months = list(range(request.lead.lead_start, request.lead.lead_end + 1))
        else:
            leadtime_months = [1]  # fallback

        api_request = {
            "originating_centre": p["originating_centre"],
            "system": p["system"],
            "variable": [var_cfg["hindcast"]],
            "product_type": [p["product_type_hindcast"]],
            "year": [str(y) for y in years_all],
            "month": [f"{request.init.month:02d}"],
            "leadtime_month": [str(m) for m in leadtime_months],
            "data_format": "netcdf",
            "area": self._bbox_to_area(request.bbox),
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        log.debug("CDS hindcast request: %s", api_request)

        client = self._get_client()
        client.retrieve(
            p["dataset_hindcast"],
            api_request,
            target=str(out_path),
        )

        out_path = _ensure_netcdf(out_path)
        ds = xr.open_dataset(out_path)
        return self._normalise_coords(ds)

    # ------------------------------------------------------------------
    # Forecast download
    # ------------------------------------------------------------------

    def _fetch_forecast(
        self,
        request: Request,
        p: dict,
        var_cfg: dict,
        out_path: Path,
    ) -> xr.Dataset:
        """
        Download the real-time forecast for the given init month/year.

        Uses the SAME raw dataset (seasonal-monthly-single-levels) and
        variable as the hindcast — giving a full field in m/s or K,
        consistent with the hindcast.  We do NOT use the postprocessed
        anomaly product: anomaly computation is optional in CPT-DM
        (--anomalies flag) and CPT itself can also compute anomalies.
        """
        if request.timescale == "seasonal" and request.lead:
            leadtime_months = list(range(request.lead.lead_start, request.lead.lead_end + 1))
        else:
            leadtime_months = [1]

        api_request = {
            "originating_centre": p["originating_centre"],
            "system": p["system"],
            "variable": [var_cfg["hindcast"]],         # same variable as hindcast
            "product_type": [p["product_type_hindcast"]],  # monthly_mean
            "year": [str(request.init.year)],
            "month": [f"{request.init.month:02d}"],
            "leadtime_month": [str(m) for m in leadtime_months],
            "data_format": "netcdf",
            "area": self._bbox_to_area(request.bbox),
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        log.debug("CDS forecast request: %s", api_request)

        client = self._get_client()
        client.retrieve(
            p["dataset_hindcast"],                     # same dataset as hindcast
            api_request,
            target=str(out_path),
        )

        out_path = _ensure_netcdf(out_path)
        ds = xr.open_dataset(out_path)
        return self._normalise_coords(ds)

    # ------------------------------------------------------------------
    # ERA5-Land reanalysis fetch (for obs)
    # ------------------------------------------------------------------

    def fetch_reanalysis(
        self,
        request: Request,
        cache_dir: Path,
    ) -> xr.Dataset:
        """
        Fetch ERA5-Land monthly means for the target period.
        Called by the orchestrator when obs_dataset_id == 'era5land'.
        """
        p = self.entry.cds_params
        var_cfg = self.entry.get_variable_params(request.variable)

        clim_start = request.clim_start or self.entry.clim_years[0]
        clim_end = request.clim_end or self.entry.clim_years[1]
        years_all = list(range(clim_start, clim_end + 1))

        if request.timescale == "seasonal" and request.lead:
            # Target months = init_month + lead offsets
            init_m = request.init.month
            target_months = sorted(set(
                ((init_m - 1 + offset) % 12) + 1
                for offset in range(request.lead.lead_start, request.lead.lead_end + 1)
            ))
        else:
            target_months = list(range(1, 13))

        rean_path = cache_dir / self._rean_filename(request)

        if rean_path.exists() and not request.no_cache:
            log.info("Loading ERA5-Land from cache: %s", rean_path)
            rean_path = _ensure_netcdf(rean_path)
            ds = xr.open_dataset(rean_path)
            return self._normalise_coords(ds)

        log.info("Downloading ERA5-Land from CDS …")

        api_request = {
            "variable": [var_cfg["cds_name"]],
            "product_type": p["product_type"],
            "year": [str(y) for y in years_all],
            "month": [f"{m:02d}" for m in target_months],
            "time": "00:00",
            "area": self._bbox_to_area(request.bbox),
            "data_format": "netcdf",
        }

        log.debug("CDS ERA5-Land request: %s", api_request)
        client = self._get_client()
        client.retrieve(p["dataset"], api_request, target=str(rean_path))

        # CDS Beta API sometimes returns a zip archive instead of a raw NetCDF
        # when multiple months/years are requested.  Detect and unpack it.
        rean_path = _ensure_netcdf(rean_path)

        ds = xr.open_dataset(rean_path)
        return self._normalise_coords(ds)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        """Lazy-initialise cdsapi.Client (reads ~/.cdsapirc automatically)."""
        if self._cds_client is None:
            try:
                import cdsapi
            except ImportError:
                raise ImportError(
                    "cdsapi is required for CDS downloads. "
                    "Install with: pip install cdsapi"
                )
            # quiet=True suppresses cdsapi's own progress output;
            # CPT-DM manages its own logging
            self._cds_client = cdsapi.Client(quiet=True)
        return self._cds_client

    @staticmethod
    def _bbox_to_area(bbox: tuple[float, float, float, float]) -> list[float]:
        """Convert (lonW, latS, lonE, latN) to CDS area [N, W, S, E]."""
        lonW, latS, lonE, latN = bbox
        return [latN, lonW, latS, lonE]

    @staticmethod
    def _normalise_coords(ds: xr.Dataset) -> xr.Dataset:
        """
        Normalise coordinate names and conventions:
          - Rename 'longitude'/'latitude' → 'lon'/'lat' if needed
          - Convert lon from 0–360 to -180–180 if needed
          - Ensure lat is descending (N→S) for CPT output
        """
        renames = {}
        if "longitude" in ds.coords:
            renames["longitude"] = "lon"
        if "latitude" in ds.coords:
            renames["latitude"] = "lat"
        if renames:
            ds = ds.rename(renames)

        # Lon: force -180 to 180
        if "lon" in ds.coords and ds.lon.max() > 180:
            ds = ds.assign_coords(lon=(ds.lon + 180) % 360 - 180)
            ds = ds.sortby("lon")

        # Lat: ensure descending (N→S) as CPT expects
        if "lat" in ds.coords and ds.lat.values[0] < ds.lat.values[-1]:
            ds = ds.sortby("lat", ascending=False)

        return ds

    def _fetch_rfreq_daily(
        self,
        request: Request,
        p: dict,
        var_cfg: dict,
        cache_dir: Path,
    ) -> dict[str, xr.Dataset]:
        """
        Download daily precipitation from seasonal-original-single-levels,
        count wet days per lead month, and return as hindcast (and forecast) Dataset.
        Only supported for ECMWF SEAS5 which provides daily data on CDS.
        """
        import numpy as np
        import pandas as pd

        threshold_mm = float(getattr(request, "wet_day_threshold", 1.0))
        # CDS daily tp is in metres; convert threshold to metres
        threshold_m = threshold_mm / 1000.0

        clim_start = request.clim_start or self.entry.clim_years[0]
        clim_end   = request.clim_end   or self.entry.clim_years[1]

        if request.timescale == "seasonal" and request.lead:
            leadtime_months = list(range(request.lead.lead_start, request.lead.lead_end + 1))
        else:
            leadtime_months = [1]

        dataset_daily = var_cfg["dataset_daily"]
        log.info("CDS rfreq: downloading daily data from %s (%d–%d, threshold=%.1f mm/day)",
                 dataset_daily, clim_start, clim_end, threshold_mm)

        years_all = list(range(clim_start, clim_end + 1))
        hcast_by_year = []   # one DataArray per year, dims (leadtime_month, lat, lon)

        for year in years_all:
            lead_slices = []
            for lead in leadtime_months:
                cache_fname = (
                    f"rfreq_daily_{p['originating_centre']}_{year}"
                    f"_init{request.init.month:02d}_lead{lead}.nc"
                )
                local = cache_dir / "cds_rfreq" / cache_fname
                local.parent.mkdir(parents=True, exist_ok=True)

                if not local.exists() or request.no_cache:
                    api_req = {
                        "originating_centre": p["originating_centre"],
                        "system": p["system"],
                        "variable": [var_cfg["hindcast"]],
                        "year":  [str(year)],
                        "month": [f"{request.init.month:02d}"],
                        "day":   ["01"],
                        "leadtime_hour": self._month_leadtime_hours(
                            request.init.month, lead),
                        "data_format": "netcdf",
                        "area": self._bbox_to_area(request.bbox),
                    }
                    log.debug("CDS rfreq daily request year=%d lead=%d: %s",
                              year, lead, api_req)
                    client = self._get_client()
                    client.retrieve(dataset_daily, api_req, target=str(local))
                    local = _ensure_netcdf(local)
                else:
                    log.debug("  rfreq cached: %s", cache_fname)

                ds_day = xr.open_dataset(local)
                ds_day = self._normalise_coords(ds_day)
                nc_var = var_cfg.get("nc_var", "tp")
                if nc_var not in ds_day.data_vars:
                    nc_var = list(ds_day.data_vars)[0]

                # Sum days with tp > threshold; average over ensemble members
                time_like = [d for d in ds_day[nc_var].dims
                             if d in ("time", "step") or "step" in d]
                wet = (ds_day[nc_var] > threshold_m).sum(
                    dim=time_like, skipna=True)
                if "number" in wet.dims:
                    wet = wet.mean(dim="number", skipna=True)

                # Keep only lat/lon dims — drop everything else
                keep_dims = {"lat", "lon", "latitude", "longitude"}
                for d in list(wet.dims):
                    if d not in keep_dims:
                        if wet.sizes[d] == 1:
                            wet = wet.squeeze(d, drop=True)
                        else:
                            wet = wet.isel({d: 0}, drop=True)
                # Drop all non-spatial coordinates
                drop_coords = [c for c in wet.coords
                               if c not in keep_dims]
                if drop_coords:
                    wet = wet.drop_vars(drop_coords)

                wet = wet.expand_dims({"leadtime_month": [lead]})
                lead_slices.append(wet)

            # Stack leads → (leadtime_month, lat, lon)
            year_da = xr.concat(lead_slices, dim="leadtime_month",
                                join="override", coords="minimal")
            # Add forecast_reference_time as a new scalar dimension
            frt = np.datetime64(f"{year}-{request.init.month:02d}-01", "ns")
            year_da = year_da.expand_dims(
                {"forecast_reference_time": [frt]}
            )
            hcast_by_year.append(year_da)

        # Stack years → (forecast_reference_time, leadtime_month, lat, lon)
        hcast_da = xr.concat(hcast_by_year, dim="forecast_reference_time")
        hcast_ds = hcast_da.to_dataset(name="rfreq")
        hcast_ds.attrs["units"] = "days"
        hcast_ds.attrs["wet_day_threshold_mm"] = threshold_mm

        results = {"hindcast": hcast_ds}

        # Realtime forecast rfreq
        if not getattr(self.entry, "hindcast_only", False):
            try:
                fcast_cubes = []
                for lead in leadtime_months:
                    cache_fname = (
                        f"rfreq_daily_{p['originating_centre']}_{request.init.year}"
                        f"_init{request.init.month:02d}_lead{lead}_fcast.nc"
                    )
                    local = cache_dir / "cds_rfreq" / cache_fname
                    if not local.exists() or request.no_cache:
                        api_req = {
                            "originating_centre": p["originating_centre"],
                            "system": p["system"],
                            "variable": [var_cfg["forecast"]],
                            "year":  [str(request.init.year)],
                            "month": [f"{request.init.month:02d}"],
                            "day":   ["01"],
                            "leadtime_hour": self._month_leadtime_hours(
                                request.init.month, lead),
                            "data_format": "netcdf",
                            "area": self._bbox_to_area(request.bbox),
                        }
                        client = self._get_client()
                        client.retrieve(dataset_daily, api_req, target=str(local))
                        local = _ensure_netcdf(local)
                    ds_day = xr.open_dataset(local)
                    ds_day = self._normalise_coords(ds_day)
                    nc_var = var_cfg.get("nc_var", "tp")
                    if nc_var not in ds_day.data_vars:
                        nc_var = list(ds_day.data_vars)[0]
                    time_like = [d for d in ds_day[nc_var].dims
                                 if d in ("time", "step") or "step" in d]
                    wet = (ds_day[nc_var] > threshold_m).sum(
                        dim=time_like, skipna=True)
                    if "number" in wet.dims:
                        wet = wet.mean(dim="number", skipna=True)
                    keep_dims = {"lat", "lon", "latitude", "longitude"}
                    for d in list(wet.dims):
                        if d not in keep_dims:
                            wet = wet.squeeze(d, drop=True) if wet.sizes[d] == 1 else wet.isel({d: 0}, drop=True)
                    drop_coords = [c for c in wet.coords if c not in keep_dims]
                    if drop_coords:
                        wet = wet.drop_vars(drop_coords)
                    wet = wet.expand_dims({"leadtime_month": [lead]})
                    fcast_cubes.append(wet)
                fcast_da = xr.concat(fcast_cubes, dim="leadtime_month",
                                     join="override", coords="minimal")
                fcast_ds = fcast_da.to_dataset(name="rfreq")
                fcast_ds.attrs["units"] = "days"
                results["forecast"] = fcast_ds
            except Exception as exc:
                log.warning("CDS rfreq forecast not available: %s", exc)

        return results

    @staticmethod
    def _month_leadtime_hours(init_month: int, lead_month_offset: int) -> list[str]:
        """
        Return the list of hourly leadtime steps that fall within the target
        calendar month, given an init month and a lead offset (1-based).
        Used for daily seasonal-original-single-levels requests.
        """
        import calendar
        target_month = (init_month - 1 + lead_month_offset) % 12 + 1
        target_year  = 2000 + (1 if init_month - 1 + lead_month_offset >= 12 else 0)
        days_in_month = calendar.monthrange(target_year, target_month)[1]
        # Lead hours are cumulative from init date (assumed 1st of init month)
        # Start hour of target month
        hours_before = sum(
            calendar.monthrange(target_year, (init_month - 1 + lm) % 12 + 1)[1]
            for lm in range(1, lead_month_offset)
        )
        start_h = hours_before * 24 + 24   # +24 because leadtime_hour starts at 24
        return [str(start_h + d * 24) for d in range(days_in_month)]

    def _hcast_cache_path(self, request: Request, cache_dir: Path) -> Path:
        p = self.entry.cds_params
        centre = p["originating_centre"]
        init_str = request.init.strftime("%Y%m")
        lead_str = (f"{request.lead.lead_start}-{request.lead.lead_end}"
                    if request.lead else "l1")
        # Include clim years in filename so different --clim ranges don't
        # collide in the cache (e.g. 1993-2016 vs 2001-2002).
        cs = request.clim_start or (self.entry.clim_years[0] if self.entry.clim_years else 0)
        ce = request.clim_end or (self.entry.clim_years[1] if self.entry.clim_years else 0)
        return cache_dir / f"hcast_{centre}_{request.variable}_{init_str}_lead{lead_str}_{cs}-{ce}.nc"

    def _fcast_cache_path(self, request: Request, cache_dir: Path) -> Path:
        p = self.entry.cds_params
        centre = p["originating_centre"]
        init_str = request.init.strftime("%Y%m%d")
        lead_str = (f"{request.lead.lead_start}-{request.lead.lead_end}"
                    if request.lead else "l1")
        return cache_dir / f"fcast_{centre}_{request.variable}_{init_str}_lead{lead_str}.nc"

    def _rean_filename(self, request: Request) -> str:
        init_str = request.init.strftime("%Y%m")
        clim_str = f"{request.clim_start}-{request.clim_end}"
        return f"rean_era5land_{request.variable}_{init_str}_{clim_str}.nc"

def _ensure_netcdf(path: Path) -> Path:
    """
    CDS Beta API may return a .zip containing one or more NetCDF files
    instead of a bare .nc, especially for ERA5-Land multi-month requests.
    If path is a zip, extract the first .nc it contains alongside the zip
    and return the path to that .nc.  The original .zip is kept as-is so
    the cache check still works on subsequent runs (we rename the .nc to
    match what was requested).
    """
    import zipfile

    if not path.exists():
        return path  # let the caller raise a sensible error

    # Peek at the magic bytes: PK = zip
    with path.open("rb") as fh:
        magic = fh.read(4)
    if magic[:2] != b"PK":
        return path  # already a NetCDF or other binary — leave alone

    log.info("  CDS returned a zip archive: %s — extracting NetCDF …", path.name)

    nc_path = path.with_suffix(".nc")
    with zipfile.ZipFile(path) as zf:
        nc_members = [m for m in zf.namelist() if m.endswith(".nc")]
        if not nc_members:
            raise ValueError(
                f"CDS returned a zip ({path.name}) but it contains no .nc files: "                f"{zf.namelist()}"            )
        if len(nc_members) > 1:
            log.warning(
                "  zip contains %d .nc files; merging with xarray …", len(nc_members)
            )
            # Extract all, open_mfdataset, write merged nc
            import xarray as xr, tempfile, shutil
            tmp = Path(tempfile.mkdtemp())
            try:
                zf.extractall(tmp)
                parts = [xr.open_dataset(tmp / m) for m in nc_members]
                merged = xr.concat(parts, dim="time") if "time" in parts[0].dims else parts[0]
                merged.to_netcdf(nc_path)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        else:
            data = zf.read(nc_members[0])
            nc_path.write_bytes(data)
            log.info("  Extracted: %s  (%.1f MB)", nc_path.name, nc_path.stat().st_size / 1e6)

    return nc_path

