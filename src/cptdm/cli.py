# =============================================================================
# cli.py — Command-line interface — defines all CLI options and dispatches to the orchestrator.
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
cptdm.cli
~~~~~~~~~
CLI entrypoint.  Maps command-line flags to a validated Request,
then hands off to the Orchestrator.

Entry point registered in pyproject.toml:
    cptdm = "cptdm.cli:main"
"""
from __future__ import annotations

import sys

# Windows UTF-8 fix — must run before any output, before _Tee is set up
if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

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

_DEFAULT_OUTDIR   = "./CPTFiles"
_DEFAULT_CACHEDIR = "./CPTFiles/cache"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_init(init_str: str, timescale: str) -> date | tuple[date, int, int]:
    """
    Parse --init.

    Seasonal:
      YYYY-MM  (e.g. 2026-02)  → first day of that month

    Intraseasonal — single init:
      YYYY-MM-DD  → that specific date

    Intraseasonal — multi-init (hindcast batch):
      YYYY1/YYYY2-MM-DD  → the specific date in year YYYY2 (used as the
        "current" init), plus year range YYYY1..YYYY2 for the hindcast.
        Returns (init_date, hcast_start_year, hcast_end_year).
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
        # Check for multi-year format: YYYY1/YYYY2-MM-DD
        import re as _re
        m = _re.fullmatch(r"(\d{4})/(\d{4})-(\d{2})-(\d{2})", init_str)
        if m:
            y1, y2 = int(m.group(1)), int(m.group(2))
            mn, dy = int(m.group(3)), int(m.group(4))
            if y1 > y2:
                raise click.BadParameter(
                    f"Multi-init start year ({y1}) must be <= end year ({y2})"
                )
            try:
                init_date = date(y2, mn, dy)
            except ValueError:
                raise click.BadParameter(
                    f"Invalid date components in '{init_str}'"
                )
            return (init_date, y1, y2)

        # Single date
        try:
            return date.fromisoformat(init_str)
        except ValueError:
            raise click.BadParameter(
                f"Intraseasonal --init must be YYYY-MM-DD or YYYY1/YYYY2-MM-DD, "
                f"got '{init_str}'"
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
    """
    Parse climatology period string.

    Accepted formats:
      '1991-2020'  → (1991, 2020)
      '1991:2020'  → (1991, 2020)
      '1993:'      → (1993, current_year)   # open-ended
    """
    import re
    from datetime import date as _date

    s = clim_str.strip()

    # Open-ended: YYYY: (no end year, means "up to current year")
    m = re.fullmatch(r"(\d{4}):", s)
    if m:
        start = int(m.group(1))
        end = _date.today().year
        if start >= end:
            raise click.BadParameter(f"--clim {s} start year must be < current year ({end})")
        return start, end

    # Standard: YYYY-YYYY or YYYY:YYYY
    m = re.fullmatch(r"(\d{4})[-:](\d{4})", s)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if start >= end:
            raise click.BadParameter("--clim start year must be < end year")
        return start, end

    raise click.BadParameter(
        "--clim must be YYYY-YYYY, YYYY:YYYY, or YYYY: (open-ended). "
        "Examples: 1991-2020, 1993:2025, 1993:"
    )


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
# --full-obs standalone path
# ---------------------------------------------------------------------------

def _run_full_obs(
    *,
    full_obs_str: str,
    obs_dataset: str | None,
    variable: str | None,
    bbox_str: str | None,
    outdir: str,
    cache_dir: str,
    no_cache: bool,
    log_level: str,
    registry_path: str | None,
    dry_run: bool,
) -> None:
    """
    Download raw daily obs for every day in a year range and write a
    CPT TSV with one field per day (no temporal aggregation).

    This is Simon Mason's approach: CPT itself handles the seasonal
    aggregation of obs, so CPT-DM just delivers the full daily time
    series for the specified domain and period.
    """
    import logging
    import time

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    _log = logging.getLogger("cptdm.full_obs")

    # ── Parse year range ──────────────────────────────────────────────
    m = __import__("re").fullmatch(r"(\d{4})-(\d{4})", full_obs_str.strip())
    if not m:
        click.echo("ERROR: --full-obs must be YYYY-YYYY (e.g. 1991-2020)", err=True)
        sys.exit(2)
    year_start, year_end = int(m.group(1)), int(m.group(2))
    if year_start >= year_end:
        click.echo("ERROR: --full-obs start year must be < end year", err=True)
        sys.exit(2)

    # ── Validate required companions ──────────────────────────────────
    if not variable:
        click.echo("ERROR: --full-obs requires --variable (e.g. prcp, tmean)", err=True)
        sys.exit(2)

    # Infer obs dataset from variable if not explicitly given
    _FULL_OBS_DEFAULTS = {
        "prcp":  "chirps3",
        "tmean": "era5land",
        "tmax":  "era5land",
        "tmin":  "era5land",
    }
    if not obs_dataset:
        obs_dataset = _FULL_OBS_DEFAULTS.get(variable)
        if not obs_dataset:
            click.echo(
                f"ERROR: Cannot infer obs dataset for variable '{variable}'. "
                f"Use --obs-dataset to specify explicitly. "
                f"Supported defaults: {', '.join(f'{k}→{v}' for k, v in _FULL_OBS_DEFAULTS.items())}",
                err=True,
            )
            sys.exit(2)
        click.echo(f"INFO: --full-obs: using {obs_dataset} for {variable}.", err=True)

    # ── Load registry ─────────────────────────────────────────────────
    reg_path = Path(registry_path) if registry_path else None
    try:
        reg = Registry.load(reg_path)
    except Exception as exc:
        click.echo(f"ERROR: Registry load failed: {exc}", err=True)
        sys.exit(2)

    if obs_dataset not in reg:
        click.echo(f"ERROR: obs-dataset '{obs_dataset}' not found in registry.", err=True)
        sys.exit(2)

    entry = reg[obs_dataset]
    if variable not in entry.variables:
        # For --full-obs prcp we use the prcp variable even though the
        # dataset might also list rfreq; allow prcp on rfreq-capable datasets
        if variable == "prcp" and "rfreq" in entry.variables:
            pass  # prcp is the underlying variable for rfreq datasets
        else:
            click.echo(
                f"ERROR: Variable '{variable}' not supported by '{obs_dataset}'.\n"
                f"Supported: {', '.join(entry.variables)}",
                err=True,
            )
            sys.exit(2)

    # ── Parse bbox ────────────────────────────────────────────────────
    if bbox_str:
        try:
            bbox = _parse_bbox(bbox_str)
        except click.BadParameter as exc:
            click.echo(f"ERROR: --bbox: {exc}", err=True)
            sys.exit(2)
    else:
        bbox = (-180.0, -90.0, 180.0, 90.0)

    # Cap to dataset coverage (e.g. CHIRPS 60°S–60°N)
    _DATASET_BBOX_LIMITS = {"chirps3": (-180.0, -60.0, 180.0, 60.0)}
    if obs_dataset in _DATASET_BBOX_LIMITS:
        lim = _DATASET_BBOX_LIMITS[obs_dataset]
        capped = (
            max(bbox[0], lim[0]), max(bbox[1], lim[1]),
            min(bbox[2], lim[2]), min(bbox[3], lim[3]),
        )
        if capped != bbox:
            click.echo(
                f"INFO: bbox capped to {obs_dataset} coverage: "
                f"{capped[0]},{capped[1]},{capped[2]},{capped[3]}",
                err=True,
            )
            bbox = capped

    # ── Resolve paths ─────────────────────────────────────────────────
    out_path = Path(outdir).expanduser().resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    raw_cache = Path(cache_dir).expanduser()
    if not raw_cache.is_absolute():
        cache_path = (out_path / raw_cache).resolve()
    else:
        cache_path = raw_cache.resolve()
    cache_path.mkdir(parents=True, exist_ok=True)

    # ── Filename ──────────────────────────────────────────────────────
    short = getattr(entry, "short_label", None) or obs_dataset
    fname = f"fullobs_{short}_{variable}_{year_start}-{year_end}.tsv"

    if dry_run:
        click.echo(f"\n--full-obs dry-run:")
        click.echo(f"  obs-dataset : {obs_dataset} ({entry.label})")
        click.echo(f"  variable    : {variable}")
        click.echo(f"  years       : {year_start}–{year_end}")
        click.echo(f"  bbox        : {bbox}")
        click.echo(f"  output      : {out_path / fname}")
        sys.exit(0)

    _log.info("--full-obs mode: %s %s %d–%d", obs_dataset, variable,
              year_start, year_end)

    t0 = time.monotonic()

    # ── Dispatch to the appropriate full-obs fetcher ──────────────────
    from cptdm.orchestrator import run_full_obs
    exit_code = run_full_obs(
        entry=entry,
        variable=variable,
        year_start=year_start,
        year_end=year_end,
        bbox=bbox,
        outdir=out_path,
        cache_dir=cache_path,
        no_cache=no_cache,
        output_filename=fname,
    )

    elapsed = time.monotonic() - t0
    _log.info("Done in %.1fs", elapsed)
    sys.exit(exit_code)


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
              help="Reserved. Provider is auto-detected from the dataset registry. "
                   "Not required in normal usage.")
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
                  "Lead convention: lead 1 = initialisation month (CDS convention), "
                  "lead 2 = first month after init, etc. "
                  "For a March init, --lead 1-3 = MAM. "
                  "Applies to all datasets (models, obs, reanalysis, RONI). "
                  "Intraseasonal: 'week1'–'week4', 'week34', '1-4'."
              ))
@click.option("--aggreg", "-a",
              type=click.Choice(["total", "mean"], case_sensitive=False),
              default=None,
              help="Temporal aggregation. Default: total for prcp, mean for temperature.")
@click.option("--bbox", "-b",
              default=None,
              help="Bounding box: lonW,latS,lonE,latN (e.g. -10,34,5,45). "
                   "Default: global (-180,-90,180,90), capped to dataset "
                   "coverage (e.g. CHIRPS: -180,-60,180,60). "
                   "Omit for index/station datasets (e.g. RONI, ENSO indices) "
                   "which have no spatial dimension. "
                   "NOTE: cached raw downloads do not encode the bbox — if you "
                   "change the domain between runs, use --no-cache to force "
                   "re-download.")
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
              help="Force re-download even if cached file exists. "
                   "Required when changing --bbox between runs, since "
                   "cache keys do not include the bounding box.")
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
              help="Override climatology period: YYYY-YYYY, YYYY:YYYY, or YYYY: "
                   "(open-ended to current year). "
                   "Examples: 1993-2025, 1993:2025, 1993:. "
                   "If the range spans a gap in the archive, unavailable years "
                   "are silently skipped.")
@click.option("--ensemble-stat",
              type=click.Choice(["median", "mean"], case_sensitive=False),
              default="median",
              show_default=True,
              help="Ensemble reduction statistic.")
@click.option("--output",
              default=None,
              help="Override the auto-generated output basename (without directory). "
                   "Applies to the HINDCAST file only; forecast and obs filenames "
                   "are always auto-generated.  When integrating with CPT batch, "
                   "the three auto-named files (fcast_*, hcast_*, obs_*) are the "
                   "recommended approach — use --dry-run to preview filenames.")
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
@click.option("--full-obs",
              default=None,
              help=(
                  "Download raw daily obs for every day in a year range.  "
                  "Format: YYYY-YYYY (e.g. 1991-2020).  "
                  "Requires --variable (prcp→CHIRPS, tmean→ERA5-Land auto-selected; "
                  "override with --obs-dataset).  "
                  "Produces a CPT TSV with one field per day (no aggregation).  "
                  "Optional: --bbox, --outdir.  Ignores --dataset, --timescale, "
                  "--init, --lead."
              ))
@click.option("--credentials",
              default=None,
              type=click.Path(exists=False),
              help="Path to CDS credentials file (default: ~/.cdsapirc).")
@click.option("--report-dir",
              default=None,
              type=click.Path(exists=False),
              help="Directory for the execution report file. Default: same as --outdir.")
@click.option("--no-report",
              is_flag=True,
              default=False,
              help="Skip writing the execution report file.")
@click.option("--log-file",
              default=None,
              type=click.Path(exists=False),
              help="Write all stdout/stderr output to this file (in addition to console).")
@click.option("--quiet", "-q",
              is_flag=True,
              default=False,
              help="Suppress the ASCII banner and reduce startup output.")
@click.version_option(__version__, "--version", "-V")
def main(
    timescale, source, dataset, obs_dataset, variable, init, lead,
    aggreg, bbox, outdir, config, cache_dir, no_cache, manifest,
    log_level, dry_run, clim, ensemble_stat, output, registry,
    wetdthresh, anomalies, full_obs, credentials,
    report_dir, no_report, log_file, quiet,
) -> None:
    """
    CPT-DM — CPT Data Acquisition and Download Module

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

      # Full daily obs: all CHIRPS daily precip 1991-2020, Iberian Peninsula
      cptdm --full-obs 1991-2020 --variable prcp \\
            --bbox -10,34,5,45 --outdir ./CPTFiles

      # Full daily obs: ERA5-Land tmean (auto-selected)
      cptdm --full-obs 1991-2020 --variable tmean \\
            --bbox -10,34,5,45
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

    # ── 1a. Normalize Windows backslash paths ─────────────────────────
    # Windows callers (CPT GUI) may pass paths with backslashes which
    # confuse Python/click on some shells.  Normalise to forward slashes.
    outdir = outdir.replace("\\", "/") if outdir else outdir
    cache_dir = cache_dir.replace("\\", "/") if cache_dir else cache_dir
    if registry_path:
        registry_path = registry_path.replace("\\", "/")
    if credentials:
        credentials = credentials.replace("\\", "/")
    if report_dir:
        report_dir = report_dir.replace("\\", "/")
    if log_file:
        log_file = log_file.replace("\\", "/")

    # ── 1b. Set up log-file tee (stdout + stderr → file) ─────────────
    _log_file_handle = None
    if log_file:
        import io
        log_file_path = Path(log_file).expanduser().resolve()
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        _log_file_handle = open(log_file_path, "w", encoding="utf-8")

        class _Tee:
            """Write to both a file and the original stream."""
            def __init__(self, stream, logfile):
                self._stream = stream
                self._logfile = logfile
            def write(self, data):
                self._stream.write(data)
                self._logfile.write(data)
                self._logfile.flush()
            def flush(self):
                self._stream.flush()
                self._logfile.flush()
            def fileno(self):
                return self._stream.fileno()
            def isatty(self):
                return False

        sys.stdout = _Tee(sys.stdout, _log_file_handle)
        sys.stderr = _Tee(sys.stderr, _log_file_handle)

    # ── 1c. Print banner (unless --quiet) ────────────────────────────
    if not quiet:
        print_banner()

    # ── 1c. Validate credentials early — skip in dry-run ──────────────
    if not dry_run_resolved:
        try:
            validate_cds_credentials(credentials)
        except CredentialError as exc:
            click.echo(f"\nERROR: {exc}", err=True)
            sys.exit(2)

    # ── 1d. --full-obs early path (bypass normal pipeline) ────────────
    full_obs_str = _get(full_obs, "full_obs")
    if full_obs_str:
        _run_full_obs(
            full_obs_str=full_obs_str,
            obs_dataset=obs_dataset,
            variable=variable,
            bbox_str=bbox_str,
            outdir=outdir,
            cache_dir=cache_dir,
            no_cache=no_cache,
            log_level=log_level,
            registry_path=registry_path,
            dry_run=dry_run_resolved,
        )
        return

    # ── 2. Validate required flags ─────────────────────────────────────
    required = {
        "timescale": timescale,
        "dataset": dataset,
        "variable": variable,
        "init": init_str,
        "lead": lead_str,
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
    multi_init_years: tuple[int, int] | None = None
    try:
        parsed_init = _parse_init(init_str, timescale_lower)
    except click.BadParameter as exc:
        click.echo(f"ERROR: --init: {exc}", err=True)
        sys.exit(2)

    if isinstance(parsed_init, tuple):
        # Multi-init intraseasonal: (init_date, start_year, end_year)
        init_date, mi_start, mi_end = parsed_init
        multi_init_years = (mi_start, mi_end)
        click.echo(
            f"Multi-init mode: hindcasts from {mi_start} to {mi_end}, "
            f"forecast init {init_date.isoformat()}",
            err=True,
        )
    else:
        init_date = parsed_init

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
    is_index = entry.file_type == "index" if hasattr(entry, 'file_type') else False
    if bbox_str is None:
        bbox_tuple = (-180.0, -90.0, 180.0, 90.0)
        if not is_index:
            click.echo(
                "INFO: --bbox not specified; using global domain (-180,-90,180,90).",
                err=True,
            )
    else:
        try:
            bbox_tuple = _parse_bbox(bbox_str)
        except click.BadParameter as exc:
            click.echo(f"ERROR: --bbox: {exc}", err=True)
            sys.exit(2)

    # Cap bbox to dataset spatial coverage.
    # CHIRPS covers 60°S–60°N.  Other obs/forecast datasets are global.
    _DATASET_BBOX_LIMITS = {
        "chirps3": (-180.0, -60.0, 180.0, 60.0),
    }
    for ds_id in (dataset, obs_dataset):
        if ds_id and ds_id in _DATASET_BBOX_LIMITS:
            lim = _DATASET_BBOX_LIMITS[ds_id]
            lonW, latS, lonE, latN = bbox_tuple
            capped = (
                max(lonW, lim[0]), max(latS, lim[1]),
                min(lonE, lim[2]), min(latN, lim[3]),
            )
            if capped != bbox_tuple:
                click.echo(
                    f"INFO: bbox capped to {ds_id} coverage: "
                    f"{capped[0]},{capped[1]},{capped[2]},{capped[3]} "
                    f"(was {lonW},{latS},{lonE},{latN}).",
                    err=True,
                )
                bbox_tuple = capped

    # ── 9. Climatology — validate against registry available period ────
    clim_start: int | None = None
    clim_end: int | None = None
    clim_was_capped = False

    registry_clim = entry.clim_years  # default climatology period

    if clim_str:
        try:
            req_start, req_end = _parse_clim(clim_str)
        except click.BadParameter as exc:
            click.echo(f"ERROR: --clim: {exc}", err=True)
            sys.exit(2)

        # Use the new multi-range available_years logic.
        # expand_available_years() returns only the years that actually exist
        # within the requested range, silently skipping gaps.
        avail_bounds = entry.get_available_bounds()
        if avail_bounds:
            avail_start, avail_end = avail_bounds

            # Error if no overlap at all
            if req_start > avail_end or req_end < avail_start:
                click.echo(
                    f"ERROR: --clim {req_start}-{req_end} has no overlap with the "
                    f"available period for '{dataset}' ({avail_start}-{avail_end}). "
                    f"Omit --clim to use the default period, "
                    f"or use --clim {avail_start}-{avail_end}.",
                    err=True,
                )
                sys.exit(2)

            # Expand to actual available years (skipping gaps)
            available_years = entry.expand_available_years(req_start, req_end)
            if not available_years:
                click.echo(
                    f"ERROR: No available years in --clim {req_start}-{req_end} "
                    f"for '{dataset}'.",
                    err=True,
                )
                sys.exit(2)

            used_start = available_years[0]
            used_end = available_years[-1]
            n_gaps = (used_end - used_start + 1) - len(available_years)

            if used_start != req_start or used_end != req_end:
                clim_was_capped = True
                click.echo(
                    f"WARNING: --clim {req_start}-{req_end} partially outside available "
                    f"period for '{dataset}'. Using: {used_start}-{used_end}.",
                    err=True,
                )

            if n_gaps > 0:
                click.echo(
                    f"INFO: {n_gaps} year(s) in {used_start}-{used_end} are not available "
                    f"for '{dataset}' and will be skipped (gap in archive).",
                    err=True,
                )

            clim_start, clim_end = used_start, used_end
        else:
            # No available_years constraint — also check legacy clim_years
            if registry_clim:
                avail_start, avail_end = registry_clim
                used_start = max(req_start, avail_start)
                used_end = min(req_end, avail_end)
                if used_start != req_start or used_end != req_end:
                    clim_was_capped = True
                    click.echo(
                        f"WARNING: --clim {req_start}-{req_end} partially outside "
                        f"default period for '{dataset}' ({avail_start}-{avail_end}). "
                        f"Using: {used_start}-{used_end}.",
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
                multi_init_years=multi_init_years,
                report_dir=report_dir,
                no_report=no_report,
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

    # Close log file if open
    if _log_file_handle:
        _log_file_handle.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
