"""
cptdm.orchestrator
~~~~~~~~~~~~~~~~~~
Orchestrates a single Request end-to-end:
  1. Check cache / existing outputs
  2. Select provider(s) for forecast/hindcast and obs/rean
  3. Fetch raw data
  4. Run transform pipeline
  5. Write CPT TSV(s)
  6. Write manifest (if requested)
  7. Write report_execution.cptdm (always)

The orchestrator is timescale-agnostic; differences are handled inside
the providers and pipeline steps via the Request object.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import xarray as xr

from cptdm.logging.run_report import RunReport
from cptdm.registry.loader import Registry
from cptdm.request import Request

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, registry: Registry, log_level: str = "INFO") -> None:
        self.registry = registry
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def run(self, request: Request) -> int:
        """
        Execute the full pipeline for the given Request.

        Returns:
            0  on success
            1  on recoverable error (bad data, missing date, etc.)
            2  on fatal error (auth failure, unrecognised dataset, etc.)
        """
        t0 = time.monotonic()
        outdir = Path(request.outdir).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)

        # Resolve cache_dir: if relative (e.g. "./cache"), place it inside outdir
        raw_cache = Path(request.cache_dir).expanduser()
        if not raw_cache.is_absolute():
            cache_dir = (outdir / raw_cache).resolve()
        else:
            cache_dir = raw_cache.resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)

        report = RunReport(
            outdir=outdir,
            report_dir=Path(request.report_dir).expanduser().resolve() if request.report_dir else None,
            no_report=request.no_report,
        )

        # ── Build rich request summary for the report ──────────────────
        lonW, latS, lonE, latN = request.bbox
        # Build human-readable dataset labels for the report
        fc_entry   = self.registry[request.dataset_id]
        fc_label   = f"{request.dataset_id}  ({fc_entry.label})"
        if request.obs_dataset_id:
            obs_e  = self.registry[request.obs_dataset_id]
            obs_label = f"{request.obs_dataset_id}  ({obs_e.label})"
        else:
            obs_label = "(none)"

        ds_entry = self.registry[request.dataset_id]
        # Describe what the primary dataset produces
        fc_types = []
        if not getattr(ds_entry, "hindcast_only", False):
            fc_types.append("hindcasts, forecasts")
        else:
            fc_types.append("hindcasts")
        if ds_entry.file_type in ("obs", "reanalysis", "index"):
            fc_types = []  # obs/index datasets don't produce hindcasts/forecasts
        fc_desc = f"({', '.join(fc_types)})" if fc_types else ""
        ds_parts = [f"{request.dataset_id} {fc_desc}".strip()]
        if request.obs_dataset_id:
            ds_parts.append(request.obs_dataset_id)
        ds_summary = ", ".join(ds_parts)


        report.request_summary = {
            "timescale":      request.timescale,
            "datasets":       ds_summary,
            "variable":       request.variable,
            "init":           request.init.strftime("%Y-%m-%d"),
            "aggregation":    request.aggreg,
            "bbox W,S,E,N":   f"{lonW}°, {latS}°, {lonE}°, {latN}°",
            "ensemble_stat":  request.ensemble_stat,
            "anomalies":      "yes (--anomalies)" if request.compute_anomalies else "no (full field)",
            **({"wet-day threshold": f"{request.wet_day_threshold:.1f} mm/day"}
               if request.variable == "rfreq" else {}),
        }

        if request.timescale == "seasonal":
            report.request_summary["target season"] = request.season_label or ""
            report.request_summary["lead (months)"] = (
                f"{request.lead.lead_start}–{request.lead.lead_end}"
                if request.lead else ""
            )
            report.request_summary["clim period"] = (
                f"{request.clim_start}–{request.clim_end}"
                if request.clim_start else "registry default"
            )
        else:
            if request.intra_lead:
                report.request_summary["target start"] = request.intra_lead.tgt_start.isoformat()
                report.request_summary["target end"]   = request.intra_lead.tgt_end.isoformat()
                report.request_summary["lead (weeks)"] = request.intra_lead.label
                report.request_summary["clim period"] = (
                    f"{request.clim_start}–{request.clim_end}"
                    if request.clim_start else "all available years"
                )

        # Record year-range resolution
        entry = self.registry[request.dataset_id]
        # Use available_years bounds (open-ended, gap-aware) if set;
        # fall back to clim_years for the warning comparison.
        avail_bounds = entry.get_available_bounds()
        available = avail_bounds or (tuple(entry.clim_years) if entry.clim_years else None)
        if request.clim_start and request.clim_end:
            used = (request.clim_start, request.clim_end)
            if available:
                requested = used if not request.clim_was_capped else None
                report.set_year_range(
                    requested=requested,
                    available=available,
                    used=used,
                )
            else:
                report.years_actually_used = used

        report.request_summary["outdir"]    = str(outdir)
        report.request_summary["cache_dir"] = str(cache_dir)

        lonW, latS, lonE, latN = request.bbox

        log.info("Starting CPT-DM run")
        log.info("  datasets   : %s", ds_summary)
        if request.variable == "rfreq":
            log.info("  wet-day thr : %.1f mm/day", request.wet_day_threshold)

        # GEM5.2-NEMO: warn user if init month is outside available climatology
        if request.dataset_id == "nmme_gem52_nemo":
            avail_months = {5, 6, 7}
            if request.init.month not in avail_months:
                log.warning(
                    "GEM5.2-NEMO: climatology files are only available on the NOAA CPC FTP "
                    "for init months May, June, and July (05–07). "
                    "Init month %02d is not available — this run will likely fail at download. "
                    "Please select a supported init month or use a different NMME model.",
                    request.init.month
                )
            else:
                log.info(
                    "GEM5.2-NEMO note: hindcasts only (no realtime forecasts); "
                    "climatology available for init months 05–07 only."
                )
        log.info("  variable   : %s", request.variable)
        log.info("  init       : %s", request.init.isoformat())
        log.info("  timescale  : %s", request.timescale)
        if request.lead:
            log.info("  target     : %s (lead %d-%d)",
                     request.season_label, request.lead.lead_start, request.lead.lead_end)
        log.info("  bbox       : W=%g E=%g S=%g N=%g", lonW, lonE, latS, latN)
        log.info("  outdir     : %s", outdir)
        log.info("  cache_dir  : %s", cache_dir)

        try:
            # ── Forecast / hindcast provider ──────────────────────────
            provider = self._get_provider(request)
            report.datasets_used.append(
                f"{request.dataset_id}  ({entry.label})"
            )

            # Only skip if ALL expected output files already exist
            expected_files = [f for f in [
                outdir / request.hcast_filename  if request.hcast_filename else None,
                outdir / request.fcast_filename  if request.fcast_filename else None,
                outdir / request.obs_filename    if request.obs_filename and request.obs_dataset_id else None,
            ] if f is not None]

            if all(f.exists() for f in expected_files) and not request.no_cache:
                log.info("All output files exist, skipping run:")
                for f in expected_files:
                    log.info("  cached: %s", f)
                    report.add_output(f)
                report.write()
                return 0

            log.info("Fetching forecast/hindcast from: %s", provider.__class__.__name__)

            # ── Multi-init intraseasonal path ─────────────────────────
            if request.multi_init_years:
                written_files = self._run_multi_init(
                    request, provider, cache_dir, outdir, report
                )
                for f in written_files:
                    log.info("Wrote: %s", f)
                    report.add_output(f)

                elapsed = time.monotonic() - t0
                log.info("Done in %.1fs", elapsed)
                report.write()
                return 0

            # ── Single-init path (seasonal or single intraseasonal) ───
            raw_datasets = provider.fetch(request, cache_dir=cache_dir)

            # ── Obs / reanalysis provider (if requested) ──────────────
            if request.obs_dataset_id:
                obs_entry = self.registry[request.obs_dataset_id]
                obs_provider = self._get_provider_for(obs_entry)
                report.datasets_used.append(
                    f"{request.obs_dataset_id}  ({obs_entry.label})"
                )
                log.info("Fetching obs/rean from: %s  (%s)",
                         obs_provider.__class__.__name__, obs_entry.label)
                obs_raw = obs_provider.fetch(request, cache_dir=cache_dir)

                # Determine role prefix: obs vs rean
                role = "rean" if obs_entry.file_type == "reanalysis" else "obs"
                for k, v in obs_raw.items():
                    raw_datasets[role] = v   # typically a single 'obs' key

            from cptdm.pipeline.runner import run_pipeline
            processed = run_pipeline(raw_datasets, request)

            from cptdm.writers.cpt_tsv import write_cpt_tsv
            written_files = write_cpt_tsv(processed, request, outdir)
            for f in written_files:
                log.info("Wrote: %s", f)
                report.add_output(f)

            if request.write_manifest:
                from cptdm.cache.manifest import write_manifest
                write_manifest(request, written_files, outdir)

            elapsed = time.monotonic() - t0
            log.info("Done in %.1fs", elapsed)

            if request.compute_anomalies:
                report.anomalies_computed = True
                report.anomaly_reference_period = (request.clim_start, request.clim_end)

            report.write()
            return 0

        except KeyboardInterrupt:
            report.error("Interrupted by user")
            log.warning("Interrupted by user")
            report.write()
            return 1
        except Exception as exc:
            report.error(str(exc))
            log.error("Run failed: %s", exc, exc_info=True)
            report.write()
            return 1

    # ------------------------------------------------------------------
    # Multi-init intraseasonal pipeline
    # ------------------------------------------------------------------

    def _run_multi_init(
        self,
        request: Request,
        provider,
        cache_dir: Path,
        outdir: Path,
        report: RunReport,
    ) -> list[Path]:
        """
        Loop over years in multi_init_years, fetch one forecast per year,
        run the pipeline on each, and write a CPT multi-init (lags) file.

        For intraseasonal data, each year's forecast is a single spatial field
        (lat × lon) for a specific init date.  The multi-init writer bundles
        all years into one CPT TSV with per-init-date headers.

        Returns list of written file paths.
        """
        from copy import deepcopy
        from datetime import date as date_cls, timedelta

        from cptdm.pipeline.runner import run_pipeline
        from cptdm.writers.cpt_tsv import write_cpt_multi_init

        start_year, end_year = request.multi_init_years
        base_month = request.init.month
        base_day = request.init.day

        # Use available_years to skip gaps in the archive (v0.7.0)
        entry = self.registry[request.dataset_id]
        available_years = entry.expand_available_years(start_year, end_year)

        log.info("Multi-init mode: years %d–%d (%d available)",
                 start_year, end_year, len(available_years))

        # Collect per-init-date datasets
        init_dates: list[date_cls] = []
        per_init_datasets: dict[str, xr.Dataset] = {}

        for yr in available_years:
            init_d = date_cls(yr, base_month, base_day)
            log.info("  Multi-init: fetching year %d (init %s)", yr, init_d.isoformat())

            # Create a per-year request with the specific init date
            yr_request = deepcopy(request)
            yr_request.init = init_d
            # Recompute intraseasonal target dates relative to this year's init
            if request.intra_lead:
                from cptdm.request import IntraseasonalLead
                orig_lead = request.intra_lead
                delta_start = (orig_lead.tgt_start - request.init).days
                delta_end = (orig_lead.tgt_end - request.init).days
                yr_request.intra_lead = IntraseasonalLead(
                    tgt_start=init_d + timedelta(days=delta_start),
                    tgt_end=init_d + timedelta(days=delta_end),
                    label=orig_lead.label,
                )

            try:
                raw = provider.fetch(yr_request, cache_dir=cache_dir)
            except Exception as exc:
                log.warning("  Year %d failed: %s — skipping", yr, exc)
                continue

            # Run the pipeline on this year's data
            processed = run_pipeline(raw, yr_request)

            # Extract the forecast field (intraseasonal providers return "forecast")
            for role in ("forecast", "hindcast"):
                if role in processed:
                    init_dates.append(init_d)
                    per_init_datasets[init_d.isoformat()] = processed[role]
                    break

        if not init_dates:
            raise RuntimeError(
                f"No data retrieved for any year in {start_year}–{end_year}."
            )

        log.info("Multi-init: collected %d/%d years",
                 len(init_dates), end_year - start_year + 1)

        # Write the multi-init CPT TSV
        out_path = outdir / request.hcast_filename
        write_cpt_multi_init(per_init_datasets, init_dates, request, out_path)

        written: list[Path] = [out_path]

        # If there's an obs dataset, fetch and write multi-init obs too
        if request.obs_dataset_id:
            obs_entry = self.registry[request.obs_dataset_id]
            obs_provider = self._get_provider_for(obs_entry)
            report.datasets_used.append(
                f"{request.obs_dataset_id}  ({obs_entry.label})"
            )
            log.info("Fetching obs/rean from: %s  (%s)",
                     obs_provider.__class__.__name__, obs_entry.label)

            # Fetch the full obs dataset once (covers all years)
            obs_raw = obs_provider.fetch(request, cache_dir=cache_dir)
            obs_full_ds = list(obs_raw.values())[0]

            # Normalise coords once
            from cptdm.pipeline.runner import (
                step_normalise_coords, step_subset, step_normalise_units,
                _first_data_var, _find_time_coord,
            )
            obs_full_ds = step_normalise_coords(obs_full_ds)
            obs_full_ds = step_subset(obs_full_ds, request)
            obs_full_ds = step_normalise_units(obs_full_ds, request, "rean")

            import pandas as pd
            from cptdm.request import IntraseasonalLead

            pvar = _first_data_var(obs_full_ds)
            time_coord = _find_time_coord(obs_full_ds)

            obs_init_dates: list[date_cls] = []
            obs_per_init: dict[str, xr.Dataset] = {}

            if time_coord and time_coord in obs_full_ds.dims:
                times = pd.DatetimeIndex(obs_full_ds[time_coord].values)

                for init_d in init_dates:
                    # Recompute target window for this year's init
                    lead = request.intra_lead
                    delta_start = (lead.tgt_start - request.init).days
                    delta_end = (lead.tgt_end - request.init).days
                    yr_tgt_start = pd.Timestamp(init_d + timedelta(days=delta_start))
                    yr_tgt_end = pd.Timestamp(init_d + timedelta(days=delta_end))

                    mask = (times >= yr_tgt_start) & (times <= yr_tgt_end)
                    if not mask.any():
                        log.warning("  Obs: no data for init %s window %s–%s; skipping",
                                    init_d, yr_tgt_start.date(), yr_tgt_end.date())
                        continue

                    ds_sel = obs_full_ds.isel({time_coord: mask})
                    if request.variable in ("prcp", "rfreq") and request.aggreg == "total":
                        result = ds_sel[pvar].sum(dim=time_coord, skipna=True)
                    else:
                        result = ds_sel[pvar].mean(dim=time_coord, skipna=True)

                    yr_ds = xr.Dataset({pvar: result}, attrs=obs_full_ds.attrs)
                    for coord in ("lat", "lon"):
                        if coord in obs_full_ds.coords:
                            yr_ds = yr_ds.assign_coords({coord: obs_full_ds.coords[coord]})

                    obs_init_dates.append(init_d)
                    obs_per_init[init_d.isoformat()] = yr_ds

            if obs_init_dates:
                from cptdm.writers.cpt_tsv import write_cpt_multi_init
                obs_path = outdir / request.obs_filename
                log.info("Writing multi-init obs CPT TSV: %s (%d years)",
                         obs_path.name, len(obs_init_dates))
                write_cpt_multi_init(obs_per_init, obs_init_dates, request, obs_path)
                written.append(obs_path)
            else:
                log.warning("No obs data collected for any init year")

        return written

    def _get_provider(self, request: Request):
        """Instantiate provider for the forecast/hindcast dataset."""
        entry = self.registry[request.dataset_id]
        return self._get_provider_for(entry)

    def _get_provider_for(self, entry):
        """Instantiate the correct provider for a given DatasetEntry."""
        provider_type = entry.provider

        if provider_type == "cds":
            from cptdm.providers.cds import CDSProvider
            return CDSProvider(entry)
        elif provider_type == "http":
            from cptdm.providers.http import HTTPProvider
            return HTTPProvider(entry)
        elif provider_type == "s2sdb":
            from cptdm.providers.s2sdb import S2SDBProvider
            return S2SDBProvider(entry)
        elif provider_type == "subc":
            from cptdm.providers.subc import SubCProvider
            return SubCProvider(entry)
        elif provider_type in ("opendap", "thredds"):
            from cptdm.providers.opendap import OPeNDAPProvider
            return OPeNDAPProvider(entry)
        elif provider_type == "http_index":
            from cptdm.providers.http_index import HTTPIndexProvider
            return HTTPIndexProvider(entry)
        else:
            raise ValueError(f"Unknown provider type: '{provider_type}'")


# ---------------------------------------------------------------------------
# Standalone full-obs download (outside Orchestrator class)
# ---------------------------------------------------------------------------

def run_full_obs(
    *,
    entry,
    variable: str,
    year_start: int,
    year_end: int,
    bbox: tuple[float, float, float, float],
    outdir: Path,
    cache_dir: Path,
    no_cache: bool,
    output_filename: str,
) -> int:
    """
    Download raw daily obs for every day in year_start..year_end and write
    a single CPT TSV with one field per daily timestep.

    Supports:
      - chirps3 (HTTP provider): daily NetCDF files by month
      - era5land (CDS provider): daily reanalysis via cdsapi

    Returns 0 on success, 1 on error.
    """
    import numpy as np
    import pandas as pd

    provider_type = entry.provider
    ds_id = getattr(entry, "short_label", None) or "obs"

    log.info("Full-obs: fetching %s %s daily %d–%d",
             ds_id, variable, year_start, year_end)

    try:
        if provider_type == "http" and "chirps" in entry.label.lower():
            merged = _fetch_chirps_full_daily(
                entry, variable, year_start, year_end, bbox, cache_dir, no_cache
            )
        elif provider_type == "cds" and "era5" in entry.label.lower():
            merged = _fetch_era5land_full_daily(
                entry, variable, year_start, year_end, bbox, cache_dir, no_cache
            )
        else:
            raise ValueError(
                f"--full-obs is not yet supported for provider '{provider_type}' "
                f"/ dataset '{entry.label}'.  Supported: chirps3, era5land."
            )

        # ── Write CPT TSV with daily timesteps ─────────────────────────
        from cptdm.writers.cpt_tsv import write_cpt_full_obs
        out_file = outdir / output_filename
        write_cpt_full_obs(merged, variable, out_file)
        log.info("Wrote: %s", out_file)
        return 0

    except Exception as exc:
        log.error("Full-obs failed: %s", exc, exc_info=True)
        return 1


def _fetch_chirps_full_daily(
    entry,
    variable: str,
    year_start: int,
    year_end: int,
    bbox: tuple[float, float, float, float],
    cache_dir: Path,
    no_cache: bool,
) -> xr.Dataset:
    """Fetch CHIRPS v3 daily files for all months in year range."""
    from cptdm.providers.http import _download_file, _normalise_coords, _subset_bbox

    p = entry.http_params
    daily_url = p["daily_url"]
    pattern = p["file_pattern_daily"]
    nc_var = entry.get_variable_params("prcp")["nc_var"]

    datasets = []
    for year in range(year_start, year_end + 1):
        for month in range(1, 13):
            fname = (pattern
                     .replace("{YYYY}", str(year))
                     .replace("{MM}", f"{month:02d}"))
            local = cache_dir / "chirps_daily" / fname
            if not local.exists() or no_cache:
                _download_file(daily_url + fname, local)
            else:
                log.debug("  cached: %s", fname)

            ds = xr.open_dataset(local)
            ds = _normalise_coords(ds)
            ds = _subset_bbox(ds, bbox)
            datasets.append(ds)
        log.info("  CHIRPS daily: year %d — 12 months loaded", year)

    merged = xr.concat(datasets, dim="time")
    # Ensure clean variable name
    if nc_var in merged.data_vars:
        if variable != nc_var:
            merged = merged.rename({nc_var: variable})
    log.info("CHIRPS full daily: %d timesteps, %d–%d",
             merged.sizes.get("time", 0), year_start, year_end)
    return merged


def _fetch_era5land_full_daily(
    entry,
    variable: str,
    year_start: int,
    year_end: int,
    bbox: tuple[float, float, float, float],
    cache_dir: Path,
    no_cache: bool,
) -> xr.Dataset:
    """
    Fetch ERA5-Land daily reanalysis for all days in year range.
    Uses CDS reanalysis-era5-land (daily means computed from hourly).
    """
    var_cfg = entry.get_variable_params(variable)
    cds_name = var_cfg["cds_name"]
    p = entry.cds_params
    dataset_daily = p.get("dataset_daily", "reanalysis-era5-land")

    try:
        import cdsapi
    except ImportError:
        raise ImportError("cdsapi required for ERA5-Land. pip install cdsapi")

    client = cdsapi.Client(quiet=True)
    datasets = []

    for year in range(year_start, year_end + 1):
        cache_file = cache_dir / "era5land_daily" / f"era5land_{variable}_{year}.nc"
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        if cache_file.exists() and not no_cache:
            log.debug("  cached: %s", cache_file.name)
        else:
            log.info("  ERA5-Land daily: downloading %d ...", year)
            lonW, latS, lonE, latN = bbox
            api_req = {
                "variable": [cds_name],
                "year": str(year),
                "month": [f"{m:02d}" for m in range(1, 13)],
                "day": [f"{d:02d}" for d in range(1, 32)],
                "product_type": "reanalysis",
                "area": [latN, lonW, latS, lonE],
                "data_format": "netcdf",
            }
            client.retrieve(dataset_daily, api_req, target=str(cache_file))

        ds = xr.open_dataset(cache_file)
        # Normalise coords
        renames = {}
        if "longitude" in ds.coords: renames["longitude"] = "lon"
        if "latitude" in ds.coords: renames["latitude"] = "lat"
        if "valid_time" in ds.dims and "time" not in ds.dims:
            renames["valid_time"] = "time"
        if renames:
            ds = ds.rename(renames)
        if "lon" in ds.coords and float(ds.lon.max()) > 180:
            ds = ds.assign_coords(lon=((ds.lon + 180) % 360) - 180)
            ds = ds.sortby("lon")
        if "lat" in ds.coords and ds.lat.values[0] < ds.lat.values[-1]:
            ds = ds.sortby("lat", ascending=False)

        datasets.append(ds)
        log.info("  ERA5-Land daily: year %d loaded", year)

    merged = xr.concat(datasets, dim="time")
    log.info("ERA5-Land full daily: %d timesteps, %d–%d",
             merged.sizes.get("time", 0), year_start, year_end)
    return merged
