"""
cptdm.logging.run_report
~~~~~~~~~~~~~~~~~~~~~~~~
Alisios-branded execution reporting for CPT-DM.

Produces two outputs on every run:
  1. Console output  — banner + live progress messages
  2. report_execution.cptdm — plain-text structured report written next to
     the TSV outputs; always written, never optional

The report records everything needed to reproduce or debug a run:
  version, request parameters, datasets used, actual years fetched,
  any year-range warnings, output filenames, SHA-256 checksums, and
  whether anomalies were computed.

Inspired by Alisios internal tooling style; written from scratch for
CPT-DM public/open-source use.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cptdm import __version__

# ---------------------------------------------------------------------------
# Alisios ASCII banner (original, CPT-DM-specific; not derived from any
# internal Alisios tool)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Alisios logo — exact ASCII art from Alisios internal tooling
# ---------------------------------------------------------------------------

_ALISIOS_LOGO = (
    "           ▒████▓▓▒░      \n"
    "        ▒▓▓▓▓▒▒▓▓███▓░    \n"
    "   ░▒▓▓▒▒░   ░▒   ░▒▓▓▓   \n"
    " ░███▓░     ▒██▒      ░░  \n"
    " ███▓       ██▓▓▒      ░▒ \n"
    "░███░      ▓█▒ ▒█░     ░█░\n"
    "░███      ▒█▒   ██░     ▒ \n"
    "  ▓█▒    ░▓▒    ░██░    ░ \n"
    "  ░██▒   ▓▓      ▒██░     \n"
    "   ░▒▓▓░         ░██▒     \n"
    "      ▒██▓▒░░░░░▓██▓░     \n"
    "        ░▒▓█████▓░░       "
)

_ALISIOS_SUBTITLE = (
    "    ALISIOS CORPORATION",
    "    ENVIRONMENTAL AND SOCIO-ECONOMIC INTEL",
)

_PRODUCT_HEADER = """\
  Climate Predictability Tool
  Data Acquisition and Download Module (CPT-DM)
  ──────────────────────────────────────────────────────────────
  Produced by   : Alisios Corporation (https://alisioscorporation.com)
  Project lead  : Ángel G. Muñoz   <angel.g.munoz@alisioscorporation.com>
  SEI lead      : Simon J. Mason   <simon.mason@sei.org>
  UKMO lead     : Nicholas Savage  <nicholas.savage@metoffice.gov.uk>
  CPT-DM version: {version}
  Execution     : {timestamp}
  ──────────────────────────────────────────────────────────────
"""

# ---------------------------------------------------------------------------
# ANSI colour helpers (degraded gracefully when not a TTY)
# ---------------------------------------------------------------------------

def _c(text: str, code: str) -> str:
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
        return f"\033[{code}m{text}\033[0m"
    return text

DARK_BLUE = "38;5;24"
GRAY   = "38;5;247"
YELLOW = "33"
RED    = "31"


def print_banner() -> None:
    """Print the Alisios / CPT-DM startup banner to stdout."""
    # Logo in maroon, exactly as in Alisios internal tooling
    print("\n" + _c(_ALISIOS_LOGO, DARK_BLUE))
    # Two subtitle lines in gray
    for line in _ALISIOS_SUBTITLE:
        print(_c(line, GRAY))
    # CPT-DM product header
    print(_c("\n" + _PRODUCT_HEADER.format(
        version=__version__,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ), GRAY))



# ---------------------------------------------------------------------------
# RunReport dataclass — accumulates everything during a run
# ---------------------------------------------------------------------------

@dataclass
class RunReport:
    """
    Collects all metadata about a CPT-DM run.
    Call .write() at the end to persist report_execution.cptdm.
    """
    outdir: Path
    start_time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Populated during the run
    request_summary: dict[str, Any] = field(default_factory=dict)
    datasets_used: list[str] = field(default_factory=list)

    # Year-range tracking
    years_requested: tuple[int, int] | None = None
    years_available: tuple[int, int] | None = None
    years_actually_used: tuple[int, int] | None = None
    year_warnings: list[str] = field(default_factory=list)

    # Anomaly flag
    anomalies_computed: bool = False
    anomaly_reference_period: tuple[int, int] | None = None

    # Outputs
    output_files: list[Path] = field(default_factory=list)
    checksums: dict[str, str] = field(default_factory=dict)

    # Warnings and errors collected during the run
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # Timing
    end_time: datetime | None = None
    elapsed_seconds: float | None = None

    # ── Convenience methods called during the run ──────────────────────

    def warn(self, message: str) -> None:
        """Record a warning (also logs it at WARNING level)."""
        self.warnings.append(message)
        logging.getLogger("cptdm").warning(message)

    def error(self, message: str) -> None:
        """Record an error."""
        self.errors.append(message)
        logging.getLogger("cptdm").error(message)

    def set_year_range(
        self,
        requested: tuple[int, int] | None,
        available: tuple[int, int],
        used: tuple[int, int],
    ) -> None:
        """
        Record year-range resolution and emit a warning if the requested
        range was capped by dataset availability.
        """
        self.years_requested = requested
        self.years_available = available
        self.years_actually_used = used

        if requested and (requested[0] < available[0] or requested[1] > available[1]):
            msg = (
                f"Requested climatology period {requested[0]}–{requested[1]} "
                f"extends beyond the available range for this dataset "
                f"({available[0]}–{available[1]}). "
                f"Using available period: {used[0]}–{used[1]}. "
                "Output filenames reflect the actual years used."
            )
            self.year_warnings.append(msg)
            self.warn(msg)

    def add_output(self, path: Path) -> None:
        """Register an output file and compute its checksum."""
        self.output_files.append(path)
        if path.exists():
            self.checksums[path.name] = _sha256(path)

    def finalise(self) -> None:
        """Call once the run is complete (success or failure)."""
        self.end_time = datetime.now(tz=timezone.utc)
        self.elapsed_seconds = (self.end_time - self.start_time).total_seconds()

    # ── Report file writer ─────────────────────────────────────────────

    def write(self) -> Path:
        """
        Write report_execution.cptdm to outdir.
        Always called — even on failure — so the user always has a record.
        Returns the path of the written report.
        """
        if self.end_time is None:
            self.finalise()

        ts = self.start_time.strftime("%Y%m%dT%H%M%S")
        report_path = self.outdir / f"report_execution_{ts}.cptdm"
        self.outdir.mkdir(parents=True, exist_ok=True)

        lines = _build_report(self)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Also print a compact summary to console
        _print_run_summary(self)

        return report_path


# ---------------------------------------------------------------------------
# Report body builder
# ---------------------------------------------------------------------------

def _build_report(r: RunReport) -> list[str]:
    sep = "─" * 70
    lines: list[str] = []

    def h(title: str) -> None:
        lines.append("")
        lines.append(f"  {title}")
        lines.append("  " + "─" * len(title))

    # ── Alisios logo (plain text for report file) ─────────────────────
    lines += [
        "           ▒████▓▓▒░      ",
        "        ▒▓▓▓▓▒▒▓▓███▓░    ",
        "   ░▒▓▓▒▒░   ░▒   ░▒▓▓▓   ",
        " ░███▓░     ▒██▒      ░░  ",
        " ███▓       ██▓▓▒      ░▒ ",
        "░███░      ▓█▒ ▒█░     ░█░",
        "░███      ▒█▒   ██░     ▒ ",
        "  ▓█▒    ░▓▒    ░██░    ░ ",
        "  ░██▒   ▓▓      ▒██░     ",
        "   ░▒▓▓░         ░██▒     ",
        "      ▒██▓▒░░░░░▓██▓░     ",
        "        ░▒▓█████▓░░       ",
        "    ALISIOS CORPORATION",
        "    ENVIRONMENTAL AND SOCIO-ECONOMIC INTEL",
        "",
    ]

    # ── Header ────────────────────────────────────────────────────────
    lines += [
        "═" * 70,
        "  CPT-DM  —  Execution Report",
        "  Climate Predictability Tool",
        "  Data Acquisition and Download Module (CPT-DM)",
        "═" * 70,
        f"  Produced by   : Alisios Corporation (https://alisioscorporation.com)",
        f"  Project lead  : Ángel G. Muñoz   <angel.g.munoz@alisioscorporation.com>",
        f"  SEI lead      : Simon J. Mason   <simon.mason@sei.org>",
        f"  UKMO lead     : Nicholas Savage  <nicholas.savage@metoffice.gov.uk>",
        "═" * 70,
        f"  Contract      : SEI/25-173  (Stockholm Environment Institute U.S.)",
        f"  Funder        : UK Meteorological Office / WISER programme (UK Gov / FCDO)",
        "═" * 70,
        f"  CPT-DM version: {__version__}",
        f"  Started       : {r.start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"  Finished      : {r.end_time.strftime('%Y-%m-%d %H:%M:%S UTC') if r.end_time else 'N/A'}",
        f"  Elapsed       : {r.elapsed_seconds:.1f}s" if r.elapsed_seconds else "  Elapsed       : N/A",
        "═" * 70,
    ]

    # ── Request ───────────────────────────────────────────────────────
    h("REQUEST")
    skip = {"outdir", "cache_dir"}
    for k, v in r.request_summary.items():
        if k in skip:
            continue
        lines.append(f"    {k:<22}: {v}")

    h("PATHS")
    lines.append(f"    Output directory : {r.request_summary.get('outdir', 'N/A')}")
    lines.append(f"    Cache directory  : {r.request_summary.get('cache_dir', 'N/A')}")
    lines.append(f"    Execution report : {r.outdir / 'report_execution.cptdm'}")

    # ── Year-range resolution ─────────────────────────────────────────
    h("CLIMATOLOGY YEARS")
    if r.years_requested:
        lines.append(f"    Requested  : {r.years_requested[0]}–{r.years_requested[1]}")
    else:
        lines.append(f"    Requested  : (registry default)")
    if r.years_available:
        lines.append(f"    Available  : {r.years_available[0]}–{r.years_available[1]}")
    if r.years_actually_used:
        lines.append(f"    Used       : {r.years_actually_used[0]}–{r.years_actually_used[1]}")
    if r.year_warnings:
        lines.append("")
        lines.append("    WARNINGS:")
        for w in r.year_warnings:
            lines.append(f"    ⚠  {w}")

    # ── Anomalies ─────────────────────────────────────────────────────
    h("ANOMALY COMPUTATION")
    if r.anomalies_computed:
        ref = (f"{r.anomaly_reference_period[0]}–{r.anomaly_reference_period[1]}"
               if r.anomaly_reference_period else "as above")
        lines.append(f"    Computed   : YES  (reference period: {ref})")
        lines.append("    Method     : departure from climatological mean over reference period")
    else:
        lines.append("    Computed   : NO  (full field output — default)")
        lines.append("    Note       : Anomalies can be computed by CPT-DM (--anomalies flag)")
        lines.append("                 or directly within CPT if preferred.")

    # ── Datasets used ─────────────────────────────────────────────────
    h("DATASETS USED")
    if r.datasets_used:
        for ds in r.datasets_used:
            lines.append(f"    • {ds}")
    else:
        lines.append("    (none recorded)")

    # ── Outputs ───────────────────────────────────────────────────────
    h("OUTPUT FILES")
    if r.output_files:
        for p in r.output_files:
            chk = r.checksums.get(p.name, "N/A")
            lines.append(f"    {p.name}")
            lines.append(f"      SHA-256 : {chk}")
            lines.append(f"      Path    : {p}")
    else:
        lines.append("    (no output files recorded)")

    # ── Warnings ──────────────────────────────────────────────────────
    if r.warnings:
        h("WARNINGS")
        for w in r.warnings:
            lines.append(f"    ⚠  {w}")

    # ── Errors ────────────────────────────────────────────────────────
    if r.errors:
        h("ERRORS")
        for e in r.errors:
            lines.append(f"    ✗  {e}")

    # ── Status ────────────────────────────────────────────────────────
    lines += [
        "",
        "═" * 70,
    ]
    if r.errors:
        lines.append("  STATUS: FAILED")
    else:
        lines.append("  STATUS: SUCCESS")
    lines += [
        "═" * 70,
        "",
    ]

    return lines


# ---------------------------------------------------------------------------
# Console run summary (compact)
# ---------------------------------------------------------------------------

def _print_run_summary(r: RunReport) -> None:
    if r.errors:
        status = _c("FAILED", RED)
    else:
        status = _c("SUCCESS", DARK_BLUE)

    print()
    print(_c("─" * 60, GRAY))
    print(f"  CPT-DM run {status}")
    if r.elapsed_seconds is not None:
        print(f"  Elapsed : {r.elapsed_seconds:.1f}s")
    if r.years_actually_used:
        print(f"  Years   : {r.years_actually_used[0]}–{r.years_actually_used[1]}")
    if r.year_warnings:
        for w in r.year_warnings:
            print(_c(f"  ⚠  {w}", YELLOW))
    if r.output_files:
        print(f"  Outputs : {len(r.output_files)} file(s) in {r.outdir}")
        for p in r.output_files:
            print(f"    → {p}")
    ts = r.start_time.strftime("%Y%m%dT%H%M%S")
    print(f"  Report  : {r.outdir / f'report_execution_{ts}.cptdm'}")
    print(_c("─" * 60, GRAY))
    print()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
