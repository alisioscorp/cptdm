"""
cptdm.cli
~~~~~~~~~
CLI entrypoint.  Maps flags from Slide 8 directly to a validated Request,
then hands off to the Orchestrator.

Entry point registered in pyproject.toml:
    cptdm = "cptdm.cli:main"
"""
from __future__ import annotations

import sys
import warnings
from datetime import date
from pathlib import Path
from typing import Optional

import click
import yaml

from cptdm import __version__
from cptdm.auth.credentials import CredentialError, validate_cds_credentials
from cptdm.logging.run_report import print_banner
from cptdm.registry.loader import Registry
from cptdm.request import (
    DEFAULT_AGGREG,
    Request,
    SeasonalLead,
    parse_intraseasonal_lead,
    parse_seasonal_lead,
    seasonal_target_label,
)

# ---------------------------------------------------------------------------
# Project root resolution — defaults live inside the cptdm install directory
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """
    Return the root of the cptdm installation (the folder containing src/).
    Works whether running from source or as an installed entry point.
    cli.py lives at <root>/src/cptdm/cli.py → go up 3 levels.
    """
    return Path(__file__).resolve().parent.parent.parent

_DEFAULT_OUTDIR   = str(_project_root() / "CPTFiles")
_DEFAULT_CACHEDIR = str(_project_root() / "CPTFiles" / "cache")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_init(init_str: str, timescale: str) -> date:
    """
    Parse --init.
    Seasonal:      YYYY-MM  (e.g. 2026-02)  → first day of that month
    Intraseasonal: YYYY-MM-DD
    """
    init_str = init_str.strip()
    if timescale == "seasonal":
        # Accept both "2026-02" and "2026-02-01"
        if len(init_str) == 7:
            init_str += "-01"
        try:
            return date.fromisoformat(init_str)
        except ValueError:
            raise click.BadParameter(
                f"Seasonal --init must be YYYY-MM or YYYY-MM-DD, got '{init_str}'"
            )
    else:
        try:
            return date.fromisoformat(init_str)
        except ValueError:
            raise click.BadParameter(
                f"Intraseasonal --init must be YYYY-MM-DD, got '{init_str}'"
            )


def _parse_bbox(bbox_str: str) -> tuple[float, float, float, float]:
    """Parse 'lonW,latS,lonE,latN' string."""
    parts = bbox_str.split(",")
    if len(parts) != 4:
        raise click.BadParameter("--bbox must be 'lonW,latS,lonE,latN'")
    try:
        lonW, latS, lonE, latN = [float(p.strip()) for p in parts]
    except ValueError:
        raise click.BadParameter("--bbox values must be numbers")
    if lonW >= lonE:
        raise click.BadParameter("bbox lonW must be < lonE")
    if latS >= latN:
        raise click.BadParameter("bbox latS must be < latN")
    return lonW, latS, lonE, latN


def _parse_clim(clim_str: str) -> tuple[int, int]:
    """Parse '1991-2020' → (1991, 2020)."""
    m = __import__("re").fullmatch(r"(\d{4})-(\d{4})", clim_str.strip())
    if not m:
        raise click.BadParameter("--clim must be YYYY-YYYY, e.g. 1991-2020")
    start, end = int(m.group(1)), int(m.group(2))
    if start >= end:
        raise click.BadParameter("--clim start year must be < end year")
    return start, end


def _load_config(config_path: Path) -> dict:
    """Load a job.yml config file and return its contents as a dict."""
    if not config_path.exists():
        raise click.BadParameter(f"Config file not found: {config_path}")
    with config_path.open("r") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise click.BadParameter("Config file must be a YAML mapping")
    return data


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
# ── Core ──────────────────────────────────────────────────────────────────
@click.option("--timescale", "-t",
              type=click.Choice(["seasonal", "intraseasonal"], case_sensitive=False),
              help="Timescale of the forecast system.")
@click.option("--source", "-s",
              type=click.Choice(["cds", "http", "opendap", "thredds", "s2sdb", "subc"],
                                case_sensitive=False),
              help="Provider type (overrides registry default if needed).")
@click.option("--dataset", "-d",
              help="Dataset registry ID (see datasets.yml).")
@click.option("--obs-dataset", "--obs",
              help="Obs/reanalysis registry ID (optional; can run forecast only).")
@click.option("--variable", "-v",
              type=click.Choice(["prcp", "tmax", "tmin", "tmean", "sst", "enso_index", "rfreq"],
                                case_sensitive=False),
              help="Variable to download.")
@click.option("--init", "-i",
              help="Initialisation date. YYYY-MM or YYYY-MM-DD (seasonal: YYYY-MM).")
@click.option("--lead", "-l",
              help=(
                  "Lead specification. "
                  "Seasonal: '1', '3', '1-3', 'MAM', 'DJF'. "
                  "Intraseasonal: 'week1'–'week4', 'week34', '1-4'."
              ))
@click.option("--aggreg", "-a",
              type=click.Choice(["total", "mean"], case_sensitive=False),
              default=None,
              help="Temporal aggregation. Default: total for prcp, mean for temperature.")
@click.option("--bbox", "-b",
              help="Bounding box: lonW,latS,lonE,latN (e.g. -10,34,5,45).")
@click.option("--outdir", "-o",
              default=_DEFAULT_OUTDIR,
              show_default=True,
              help="Output directory for CPT TSV files.")
# ── Ops / reproducibility ─────────────────────────────────────────────────
@click.option("--config", "-c",
              type=click.Path(exists=False),
              default=None,
              help="Optional YAML job file; flags override file values.")
@click.option("--cache-dir",
              default=_DEFAULT_CACHEDIR,
              show_default=True,
              help="Directory for cached NetCDF downloads.")
@click.option("--no-cache",
              is_flag=True,
              default=False,
              help="Force re-download even if cached file exists.")
@click.option("--manifest",
              is_flag=True,
              default=False,
              help="Write manifest.json alongside outputs.")
@click.option("--log-level",
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"],
                                case_sensitive=False),
              default="INFO",
              show_default=True)
@click.option("--dry-run",
              is_flag=True,
              default=False,
              help="Print resolved request without downloading anything.")
@click.option("--clim",
              default=None,
              help="Override climatology period: YYYY-YYYY (e.g. 1991-2020).")
@click.option("--ensemble-stat",
              type=click.Choice(["median", "mean"], case_sensitive=False),
              default="median",
              show_default=True,
              help="Ensemble reduction statistic.")
@click.option("--output",
              default=None,
              help="Override auto-generated output filename (without directory).")
@click.option("--registry",
              default=None,
              type=click.Path(exists=False),
              help="Path to a custom datasets.yml registry file.")
@click.option("--wetdthresh",
              type=float,
              default=1.0,
              show_default=True,
              help="Wet-day threshold in mm/day for rfreq computation. "
                   "Days with precip strictly greater than this value are counted as wet. "
                   "WMO standard is 1.0 mm/day.")
@click.option("--anomalies",
              is_flag=True,
              default=False,
              help=(
                  "Compute anomalies (subtract climatological mean over --clim period). "
                  "Default: off (full field output, recommended for CPT)."
              ))
@click.option("--credentials",
              default=None,
              type=click.Path(exists=False),
              help="Path to CDS credentials file (default: ~/.cdsapirc).")
@click.version_option(__version__, "--version", "-V")
def main(
    timescale, source, dataset, obs_dataset, variable, init, lead,
    aggreg, bbox, outdir, config, cache_dir, no_cache, manifest,
    log_level, dry_run, clim, ensemble_stat, output, registry,
    wetdthresh, anomalies, credentials,
) -> None:
    """
    CPT-DM — CPT Data Manager

    Downloads and converts climate datasets to CPT-compatible TSV files.

    \b
    Examples:
      # Seasonal precipitation: ECMWF hindcast + CHIRPS obs, AMJ target
      cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \\
            --obs-dataset chirps3 --variable prcp \\
            --init 2026-03 --lead 1-3 --bbox -10,34,5,45 \\
            --outdir ./CPTFiles --manifest

      # Intraseasonal temperature: S2SDB ECMWF week 2
      cptdm --timescale intraseasonal --dataset s2sdb_ecmwf \\
            --obs-dataset era5land --variable tmean \\
            --init 2026-02-16 --lead week2 \\
            --bbox -10,34,5,45 --outdir ./CPTFiles

      # Dry-run with job file
      cptdm --config job.yml --dry-run
    """
    # ── 1. Merge config file (if any) with CLI flags ───────────────────
    cfg: dict = {}
    if config:
        cfg = _load_config(Path(config))

    def _get(flag_val, key: str, default=None):
        """CLI flag takes precedence over config file."""
        if flag_val is not None:
            return flag_val
        return cfg.get(key, default)

    timescale = _get(timescale, "timescale")
    dataset = _get(dataset, "dataset")
    obs_dataset = _get(obs_dataset, "obs_dataset")
    variable = _get(variable, "variable")
    init_str = _get(init, "init")
    lead_str = _get(lead, "lead")
    aggreg = _get(aggreg, "aggreg")
    bbox_str = _get(bbox, "bbox")
    outdir = _get(outdir, "outdir", _DEFAULT_OUTDIR)
    cache_dir = _get(cache_dir, "cache_dir", _DEFAULT_CACHEDIR)
    no_cache = _get(no_cache, "no_cache", False)
    manifest = _get(manifest, "manifest", False)
    log_level = _get(log_level, "log_level", "INFO")
    clim_str = _get(clim, "clim")
    ensemble_stat = _get(ensemble_stat, "ensemble_stat", "median")
    output = _get(output, "output")
    registry_path = _get(registry, "registry")
    anomalies = _get(anomalies, "anomalies", False)
    credentials = _get(credentials, "credentials")
    dry_run_resolved = _get(dry_run, "dry_run", False)

    # ── 1b. Print banner (always, before any validation) ──────────────
    print_banner()

    # ── 1c. Validate credentials early — skip in dry-run ──────────────
    if not dry_run_resolved:
        try:
            validate_cds_credentials(credentials)
        except CredentialError as exc:
            click.echo(f"\nERROR: {exc}", err=True)
            sys.exit(2)

    # ── 2. Validate required flags ─────────────────────────────────────
    required = {
        "timescale": timescale,
        "dataset": dataset,
        "variable": variable,
        "init": init_str,
        "lead": lead_str,
        "bbox": bbox_str,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise click.UsageError(
            f"Missing required argument(s): {', '.join('--' + m for m in missing)}"
        )

    # ── 3. Load registry ───────────────────────────────────────────────
    reg_path = Path(registry_path) if registry_path else None
    try:
        reg = Registry.load(reg_path)
    except Exception as exc:
        click.echo(f"ERROR: Registry load failed: {exc}", err=True)
        sys.exit(2)

    # ── 4. Validate dataset exists and variable is supported ───────────
    if dataset not in reg:
        click.echo(
            f"ERROR: Dataset '{dataset}' not found in registry.\n"
            f"Available: {', '.join(reg.all_ids())}",
            err=True,
        )
        sys.exit(2)

    entry = reg[dataset]
    if variable not in entry.variables:
        click.echo(
            f"ERROR: Variable '{variable}' not supported by dataset '{dataset}'.\n"
            f"Supported: {', '.join(entry.variables)}",
            err=True,
        )
        sys.exit(2)

    # Validate obs pairing if provided
    if obs_dataset:
        if obs_dataset not in reg:
            click.echo(f"ERROR: obs-dataset '{obs_dataset}' not found in registry.", err=True)
            sys.exit(2)
        try:
            reg.validate_pair(dataset, obs_dataset, variable)
        except ValueError as exc:
            click.echo(f"ERROR: {exc}", err=True)
            sys.exit(2)

    # ── 5. Parse init date ─────────────────────────────────────────────
    timescale_lower = timescale.lower()
    try:
        init_date = _parse_init(init_str, timescale_lower)
    except click.BadParameter as exc:
        click.echo(f"ERROR: --init: {exc}", err=True)
        sys.exit(2)

    # ── 6. Parse lead ──────────────────────────────────────────────────
    seasonal_lead = None
    intra_lead = None
    season_label = None
    season_year_offset = 0

    try:
        if timescale_lower == "seasonal":
            seasonal_lead = parse_seasonal_lead(lead_str, init_date.month)
            season_label, season_year_offset = seasonal_target_label(
                init_date.month, seasonal_lead
            )
        else:
            intra_lead = parse_intraseasonal_lead(lead_str, init_date)
    except ValueError as exc:
        click.echo(f"ERROR: --lead: {exc}", err=True)
        sys.exit(2)

    # ── 7. Aggregation ─────────────────────────────────────────────────
    if aggreg is None:
        aggreg = DEFAULT_AGGREG[variable]

    if variable not in ("prcp", "rfreq") and aggreg == "total":
        click.echo(
            f"ERROR: --aggreg total is only valid for prcp/rfreq, not '{variable}'.",
            err=True,
        )
        sys.exit(2)

    if variable in ("prcp", "rfreq") and aggreg == "mean":
        click.echo(
            f"WARNING: --aggreg mean selected for {variable}. "
            "The default is 'total'. Proceeding as requested.",
            err=True,
        )

    # ── 8. Bounding box ────────────────────────────────────────────────
    try:
        bbox_tuple = _parse_bbox(bbox_str)
    except click.BadParameter as exc:
        click.echo(f"ERROR: --bbox: {exc}", err=True)
        sys.exit(2)

    # ── 9. Climatology — validate against registry available period ────
    clim_start: int | None = None
    clim_end: int | None = None
    clim_was_capped = False

    registry_clim = entry.clim_years  # authoritative available period

    if clim_str:
        try:
            req_start, req_end = _parse_clim(clim_str)
        except click.BadParameter as exc:
            click.echo(f"ERROR: --clim: {exc}", err=True)
            sys.exit(2)

        if registry_clim:
            avail_start, avail_end = registry_clim

            # Error if no overlap at all (e.g. --clim 2023-2025 vs available 1993-2016)
            if req_start > avail_end or req_end < avail_start:
                click.echo(
                    f"ERROR: --clim {req_start}-{req_end} has no overlap with the "
                    f"available period for '{dataset}' ({avail_start}-{avail_end}). "
                    f"Omit --clim to use the full available period, "
                    f"or use --clim {avail_start}-{avail_end}.",
                    err=True,
                )
                sys.exit(2)

            used_start = max(req_start, avail_start)
            used_end   = min(req_end,   avail_end)

            if used_start != req_start or used_end != req_end:
                clim_was_capped = True
                click.echo(
                    f"WARNING: --clim {req_start}-{req_end} partially outside available "
                    f"period for '{dataset}' ({avail_start}-{avail_end}). "
                    f"Using overlap: {used_start}-{used_end}.",
                    err=True,
                )
            clim_start, clim_end = used_start, used_end
        else:
            clim_start, clim_end = req_start, req_end
    else:
        # Use registry default (actual available period)
        if registry_clim:
            clim_start, clim_end = registry_clim
        # For intraseasonal with no registry default: None → providers use all years

    # ── 10. Build Request ──────────────────────────────────────────────
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            req = Request.__new__(Request)
            # Inject short labels before __post_init__ resolves filenames
            req._ds_short_label = getattr(entry, "short_label", None) or dataset
            if obs_dataset:
                obs_entry = reg[obs_dataset]
                req._obs_short_label = getattr(obs_entry, "short_label", None) or obs_dataset
            else:
                req._obs_short_label = None
            # Now initialise normally
            req.__init__(
                timescale=timescale_lower,
                dataset_id=dataset,
                obs_dataset_id=obs_dataset,
                variable=variable,
                init=init_date,
                lead=seasonal_lead,
                season_label=season_label,
                season_year_offset=season_year_offset,
                intra_lead=intra_lead,
                aggreg=aggreg,
                bbox=bbox_tuple,
                clim_start=clim_start,
                clim_end=clim_end,
                ensemble_stat=ensemble_stat.lower(),
                outdir=outdir,
                cache_dir=cache_dir,
                no_cache=no_cache,
                write_manifest=manifest,
                log_level=log_level.upper(),
                dry_run=dry_run,
                output_override=output,
                fcast_year=init_date.year + season_year_offset
                           if timescale_lower == "seasonal" else None,
                compute_anomalies=anomalies,
                clim_was_capped=clim_was_capped,
                wet_day_threshold=wetdthresh,
            )
            request = req
        except (ValueError, TypeError) as exc:
            click.echo(f"ERROR: {exc}", err=True)
            sys.exit(2)

    for w in caught:
        click.echo(f"WARNING: {w.message}", err=True)

    # ── 11. Dry-run output or dispatch ─────────────────────────────────
    if dry_run:
        click.echo(request.summary())
        sys.exit(0)

    # ── 12. Dispatch to orchestrator ───────────────────────────────────
    from cptdm.orchestrator import Orchestrator
    orch = Orchestrator(registry=reg, log_level=log_level.upper())
    exit_code = orch.run(request)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
