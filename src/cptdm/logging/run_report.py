# =============================================================================
# run_report.py — Execution report writer — produces the .cptdm report file summarising each run.
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
cptdm.logging.run_report
~~~~~~~~~~~~~~~~~~~~~~~~
Execution reporting for CPT-DM.

Produces two outputs on every run:
  1. Console output  — banner + live progress messages
  2. report_execution.cptdm — plain-text structured report written next to
     the TSV outputs; always written, never optional

The report records everything needed to reproduce or audit a run:
  version, request parameters, datasets used, actual years fetched,
  any year-range warnings, output filenames, SHA-256 checksums, and
  whether anomalies were computed.
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
# Alisios logo and product banner
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
  Part-funded by the Weather and Climate Information Services
  (WISER) Programme, funded with UK International Development
  from the UK government and led by the Met Office in the UK.
  ──────────────────────────────────────────────────────────────
"""

# ---------------------------------------------------------------------------
# ANSI colour helpers (degraded gracefully when not a TTY or on older Windows)
# ---------------------------------------------------------------------------

def _supports_ansi() -> bool:
    """
    Return True if stdout appears to support ANSI/VT100 escape sequences.

    Checks:
      - NO_COLOR env var (https://no-color.org/) → always disable
      - Not a TTY → disable
      - On Windows: attempt to enable VT100 processing via SetConsoleMode.
        If the call fails (older CMD, ConHost without VT support) → disable.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Windows: ctypes.windll triggers Nuitka hard-import failures in
        # onefile builds. ANSI colour is cosmetic; default to plain text.
        return False
    return True


_ANSI_OK: bool | None = None   # lazily cached


def _c(text: str, code: str) -> str:
    global _ANSI_OK
    if _ANSI_OK is None:
        _ANSI_OK = _supports_ansi()
    if _ANSI_OK:
        return f"\033[{code}m{text}\033[0m"
    return text

DARK_BLUE = "38;5;24"
GRAY   = "38;5;247"
YELLOW = "33"
RED    = "31"


def print_banner() -> None:
    """Print the CPT-DM startup banner to stdout."""
    header_text = _PRODUCT_HEADER.format(
        version=__version__,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # On Windows with legacy console (cp1252), Unicode block characters
    # in the logo and box-drawing characters in the header will fail.
    # Reconfigure stdout to UTF-8 if possible; otherwise fall back to
    # a plain-text banner.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass   # not available on all Python builds or stdout types

    try:
        # Full banner with Unicode block-art logo
        print("\n" + _c(_ALISIOS_LOGO, DARK_BLUE))
        for line in _ALISIOS_SUBTITLE:
            print(_c(line, GRAY))
        print(_c("\n" + header_text, GRAY))
    except (UnicodeEncodeError, UnicodeDecodeError, OSError):
        # Windows cp1252 / legacy console / piped output: skip the logo
        print("")
        for line in _ALISIOS_SUBTITLE:
            print(line)
        # Strip box-drawing characters from header for safe output
        safe_header = header_text.replace("\u2500", "-").replace("\u2014", "-")
        print("\n" + safe_header)



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
    report_dir: Path | None = None     # If set, write report here instead of outdir
    no_report: bool = False            # If True, skip writing the report file
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

    def write(self) -> Path | None:
        """
        Write report_execution.cptdm.
        Always called — even on failure — so the user always has a record.
        Returns the path of the written report, or None if --no-report.
        """
        if self.end_time is None:
            self.finalise()

        # Always print summary to console
        _print_run_summary(self)

        if self.no_report:
            return None

        ts = self.start_time.strftime("%Y%m%dT%H%M%S")
        target_dir = self.report_dir if self.report_dir else self.outdir
        report_path = target_dir / f"report_execution_{ts}.cptdm"
        target_dir.mkdir(parents=True, exist_ok=True)

        lines = _build_report(self)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

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

    # ── Alisios logo ──────────────────────────────────────────────────
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
        f"  Funding       : Part-funded by the Weather and Climate Information Services",
        f"                  (WISER) Programme, funded with UK International Development",
        f"                  from the UK government and led by the Met Office in the UK.",
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
        lines.append("                 or, preferably, within CPT directly.")

    # ── Datasets used ─────────────────────────────────────────────────
    h("DATASETS USED")
    if r.datasets_used:
        for ds in r.datasets_used:
            lines.append(f"    • {ds}")
    else:
        lines.append("    (none recorded)")

    # ── Outputs ───────────────────────────────────────────────────────
    h("OUTPUT FILES")
    ts = r.start_time.strftime("%Y%m%dT%H%M%S")
    target_dir = r.report_dir if r.report_dir else r.outdir
    report_filename = f"report_execution_{ts}.cptdm"
    lines.append(f"    {report_filename}")
    lines.append(f"      Path    : {target_dir / report_filename}")
    lines.append(f"      Note    : This execution report")
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
    if r.no_report:
        pass  # Don't show report line
    else:
        target_dir = r.report_dir if r.report_dir else r.outdir
        print(f"  Report  : {target_dir / f'report_execution_{ts}.cptdm'}")
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
