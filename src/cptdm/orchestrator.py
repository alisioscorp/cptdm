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

        report = RunReport(outdir=outdir)

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
        available = tuple(entry.clim_years) if entry.clim_years else None
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
