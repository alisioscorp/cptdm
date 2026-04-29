"""
cptdm.request
~~~~~~~~~~~~~
Canonical Request object.  All CLI inputs are normalised into a Request
before any provider is called.  No network I/O here — pure logic.

Key responsibilities:
  - Parse and validate --init, --lead / --tgt, --aggreg, --clim
  - Resolve lead specs (months, season abbrevs, week numbers) to concrete
    target date ranges
  - Derive output filenames following the agreed naming convention
  - Expose everything the orchestrator and providers need
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_ABBR_UPPER = [m.upper() for m in MONTH_ABBR]

SEASON_3M = {  # 3-letter season to (lead_start, lead_end) relative to init month
    "DJF": None,  # computed dynamically
    "MAM": None,
    "JJA": None,
    "SON": None,
}

VARIABLE_CPT_FIELD = {
    "prcp": "precip",
    "tmean": "t2m",
    "tmax": "tmax",
    "tmin": "tmin",
    "sst": "sst",
    "enso_index": "index",
    "rfreq": "rfreq",
}

VARIABLE_CPT_UNITS = {
    "prcp": "mm",
    "tmean": "degree_Celsius",
    "tmax": "degree_Celsius",
    "tmin": "degree_Celsius",
    "sst": "degree_Celsius",
    "enso_index": "dimensionless",
}

DEFAULT_AGGREG = {
    "prcp": "total",
    "rfreq": "total",   # rainfall frequency = sum of wet days over season
    "tmean": "mean",
    "tmax": "mean",
    "tmin": "mean",
    "sst": "mean",
    "enso_index": "mean",
}

# ---------------------------------------------------------------------------
# Lead spec parsing
# ---------------------------------------------------------------------------

@dataclass
class SeasonalLead:
    """A resolved seasonal lead: start and end month offsets from init month."""
    lead_start: int   # e.g. 1  (month after init)
    lead_end: int     # e.g. 3  (3 months after init)

    @property
    def n_months(self) -> int:
        return self.lead_end - self.lead_start + 1


@dataclass
class IntraseasonalLead:
    """A resolved intraseasonal lead: concrete start and end dates."""
    tgt_start: date
    tgt_end: date
    label: str   # e.g. "week1", "week34"


def parse_seasonal_lead(lead_str: str, init_month: int) -> SeasonalLead:
    """
    Parse --lead for seasonal timescale.

    Accepted forms:
      "1"      → single month, lead 1
      "3"      → single month, lead 3
      "1-3"    → months 1 through 3
      "MAM"    → resolved to lead offsets for that season given init_month
      "DJF"    → same
      "JJA"    → same
      "SON"    → same

    Lead 0 = init month itself; lead 1 = first month after init.
    """
    lead_str = lead_str.strip().upper()

    # Season abbreviation: 3 consecutive month letters
    if re.fullmatch(r"[A-Z]{3}", lead_str) and all(c in "JFMAMJJASOND" for c in lead_str):
        return _season_abbr_to_lead(lead_str, init_month)

    # "N-M" range
    m = re.fullmatch(r"(\d+)-(\d+)", lead_str)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise ValueError(f"Lead range {lo}-{hi}: start must be <= end")
        return SeasonalLead(lo, hi)

    # Single integer
    if re.fullmatch(r"\d+", lead_str):
        n = int(lead_str)
        return SeasonalLead(n, n)

    raise ValueError(
        f"Cannot parse seasonal lead '{lead_str}'. "
        "Expected forms: '1', '3', '1-3', 'MAM', 'DJF', etc."
    )


def _season_abbr_to_lead(abbr: str, init_month: int) -> SeasonalLead:
    """
    Convert a 3-month season abbreviation (e.g. 'MAM') to lead offsets
    relative to init_month.  Raises if the abbreviation is not a valid
    3-consecutive-month sequence.
    """
    months_upper = [m.upper()[:3] for m in MONTH_ABBR]
    # Build 3-char abbreviation for each starting month
    for start_offset in range(12):
        m0 = (init_month - 1 + start_offset) % 12
        m1 = (m0 + 1) % 12
        m2 = (m0 + 2) % 12
        candidate = months_upper[m0][:1] + months_upper[m1][:1] + months_upper[m2][:1]
        if candidate == abbr:
            # +1 so that lead_start=1 means the init month, consistent with
            # the CDS API convention (leadtime_month=1 = init month itself).
            return SeasonalLead(start_offset + 1, start_offset + 3)

    raise ValueError(
        f"Season '{abbr}' cannot be reached from init month "
        f"{MONTH_ABBR[init_month-1]}. "
        "Check that the season is within the model's lead range."
    )


def parse_intraseasonal_lead(
    lead_str: str,
    init_date: date,
) -> IntraseasonalLead:
    """
    Parse --lead for intraseasonal timescale.

    Accepted forms:
      "1"        → week1 (days 1-7 after init)
      "2"        → week2 (days 8-14 after init)
      "3"        → week3 (days 15-21 after init)
      "4"        → week4 (days 22-28 after init)
      "1-4"      → all four weeks (days 1-28); produces 4 separate files
      "week1"    → same as "1"
      "week2"    → same as "2"
      "week3"    → same as "3"
      "week4"    → same as "4"
      "week34"   → combined weeks 3+4 (days 15-28)

    Week 1 starts the day AFTER init_date (per email agreement).
    Only week1, week2, week3, week4, and week34 are valid single-lead outputs.
    "1-4" is a shorthand that the orchestrator expands to 4 calls.
    """
    lead_str = lead_str.strip().lower()

    week_map = {
        "week1": (1, 7),
        "week2": (8, 14),
        "week3": (15, 21),
        "week4": (22, 28),
        "week34": (15, 28),
    }

    # Normalise numeric forms
    single_digit_map = {"1": "week1", "2": "week2", "3": "week3", "4": "week4"}

    if lead_str in single_digit_map:
        lead_str = single_digit_map[lead_str]

    if lead_str in week_map:
        day_start, day_end = week_map[lead_str]
        tgt_start = init_date + timedelta(days=day_start)
        tgt_end = init_date + timedelta(days=day_end)
        return IntraseasonalLead(tgt_start, tgt_end, label=lead_str)

    # "1-4" shorthand → caller must handle expansion
    if lead_str == "1-4":
        # Return a sentinel; orchestrator expands this
        return IntraseasonalLead(
            tgt_start=init_date + timedelta(days=1),
            tgt_end=init_date + timedelta(days=28),
            label="weeks1-4",
        )

    # Day-range: "15-28", "8-14", etc.
    import re
    m = re.match(r"^(\d+)-(\d+)$", lead_str)
    if m:
        day_start = int(m.group(1))
        day_end = int(m.group(2))
        if 1 <= day_start < day_end <= 60:
            tgt_start = init_date + timedelta(days=day_start)
            tgt_end = init_date + timedelta(days=day_end)
            return IntraseasonalLead(tgt_start, tgt_end, label=f"d{day_start}-{day_end}")

    raise ValueError(
        f"Cannot parse intraseasonal lead '{lead_str}'. "
        "Valid: 1, 2, 3, 4, 1-4, week1, week2, week3, week4, week34, "
        "or day ranges like 15-28"
    )


# ---------------------------------------------------------------------------
# Target season label (for filename)
# ---------------------------------------------------------------------------

def seasonal_target_label(init_month: int, lead: SeasonalLead) -> tuple[str, int]:
    """
    Return (season_label, landing_year_offset) for a seasonal target.

    Convention: lead_start=1 means the init month itself, matching the CDS API
    definition of leadtime_month (confirmed from GRIB verifyingMonth metadata:
    fcmonth=1 with April init → verifyingMonth=202604).

    landing_year_offset: 0 if all target months are in the same year as init,
                         1 if any target month wraps into the next year.

    Examples (init_month=4, i.e. April):
      lead 1-3  → AMJ, offset 0   (Apr, May, Jun)
      lead 1-6  → AMJJAS, offset 0

    Examples (init_month=11, i.e. November):
      lead 1-3  → NDJ, offset 1   (Nov, Dec, Jan — Jan is next year)

    Examples (init_month=1, i.e. January):
      lead 1-3  → JFM, offset 0
    """
    months = []
    year_offset = 0
    for offset in range(lead.lead_start, lead.lead_end + 1):
        # offset-1 because lead_start=1 corresponds to the init month (offset 0
        # from init), lead_start=2 is one month after init, etc.
        m = (init_month - 1 + (offset - 1)) % 12
        if (init_month - 1 + (offset - 1)) >= 12:
            year_offset = 1   # at least one month wraps into the next year
        months.append(MONTH_ABBR[m][0].upper())

    label = "".join(months)
    return label, year_offset


# ---------------------------------------------------------------------------
# Filename builders
# ---------------------------------------------------------------------------

_DDMMYYYY = "%d%m%Y"   # e.g. 01032026 for 2026-03-01


def _fmt_init_seasonal(init: date) -> str:
    """Format init date as DDMMYYYY for seasonal filenames."""
    return init.strftime(_DDMMYYYY)


def _fmt_init_intraseasonal(init: date) -> str:
    """Format init date as DDMMYYYY for intraseasonal filenames."""
    return init.strftime(_DDMMYYYY)


def _fmt_tgt_intraseasonal(tgt_start: date, tgt_end: date) -> str:
    """Format intraseasonal target as DDMMYYYY-DDMMYYYY."""
    return f"{tgt_start.strftime(_DDMMYYYY)}-{tgt_end.strftime(_DDMMYYYY)}"


def build_filename(
    file_role: Literal["fcast", "hcast", "obs", "rean", "idx"],
    dataset_label: str,
    variable: str,
    init: date,
    timescale: Literal["seasonal", "intraseasonal"],
    # Seasonal-only
    season_label: str | None = None,
    fcast_year: int | None = None,
    clim_start: int | None = None,
    clim_end: int | None = None,
    # Intraseasonal-only
    tgt_start: date | None = None,
    tgt_end: date | None = None,
    # Override
    output_override: str | None = None,
) -> str:
    """
    Build the CPT-DM output filename following the agreed naming convention.

    Seasonal examples:
      fcast_CFSv2_precip_init01032026_tgtAMJ_2026.tsv
      hcast_CFSv2_precip_init01032026_tgtAMJ_1991-2020.tsv
      obs_CHIRPSv3_precip_init01032026_tgtAMJ_1991-2020.tsv

    Intraseasonal examples:
      fcast_ecmwf_t2m_init09032026_tgt10032026-16032026.tsv
      hcast_ecmwf_t2m_init09032026_tgt10032026-16032026_2006-2025.tsv
      rean_era5land_t2m_init09032026_tgt10032026-16032026_2006-2025.tsv
    """
    if output_override:
        # Ensure .tsv suffix
        p = output_override if output_override.endswith(".tsv") else output_override + ".tsv"
        return p

    var_label = VARIABLE_CPT_FIELD.get(variable, variable)
    init_str = _fmt_init_seasonal(init) if timescale == "seasonal" else _fmt_init_intraseasonal(init)

    if timescale == "seasonal":
        if file_role == "fcast":
            return f"fcast_{dataset_label}_{var_label}_init{init_str}_tgt{season_label}_{fcast_year}.tsv"
        else:
            clim_str = f"{clim_start}-{clim_end}"
            return f"{file_role}_{dataset_label}_{var_label}_init{init_str}_tgt{season_label}_{clim_str}.tsv"
    else:
        # Intraseasonal
        tgt_str = _fmt_tgt_intraseasonal(tgt_start, tgt_end)
        if file_role == "fcast":
            return f"fcast_{dataset_label}_{var_label}_init{init_str}_tgt{tgt_str}.tsv"
        else:
            clim_str = f"{clim_start}-{clim_end}"
            return f"{file_role}_{dataset_label}_{var_label}_init{init_str}_tgt{tgt_str}_{clim_str}.tsv"


# ---------------------------------------------------------------------------
# Main Request dataclass
# ---------------------------------------------------------------------------

@dataclass
class Request:
    """
    Fully-resolved, validated request.  Created by the CLI layer and
    consumed by the Orchestrator.  Immutable after construction.
    """
    # Core
    timescale: Literal["seasonal", "intraseasonal"]
    dataset_id: str           # registry key for the forecast/hindcast dataset
    obs_dataset_id: str | None  # registry key for obs/rean (optional for --dry-run)
    variable: str
    init: date
    aggreg: Literal["total", "mean"]

    # Spatial
    bbox: tuple[float, float, float, float]  # (lonW, latS, lonE, latN)

    # Temporal – seasonal
    lead: SeasonalLead | None = None
    season_label: str | None = None          # e.g. "AMJ"
    season_year_offset: int = 0             # 0 or 1 (cross-year seasons)
    fcast_year: int | None = None

    # Temporal – intraseasonal
    intra_lead: IntraseasonalLead | None = None
    multi_init_years: tuple[int, int] | None = None  # (start_year, end_year) for multi-init batch

    # Climatology
    clim_start: int | None = None
    clim_end: int | None = None

    # Ensemble
    ensemble_stat: Literal["median", "mean"] = "median"
    wet_day_threshold: float = 1.0   # mm/day; WMO standard for rfreq computation

    # Anomaly computation
    compute_anomalies: bool = False   # False = full field (default, CPT-recommended)
    clim_was_capped: bool = False     # True if --clim was capped to dataset availability

    # Output
    outdir: str = "./CPTFiles"
    cache_dir: str = "~/.cptdm/cache"
    no_cache: bool = False
    write_manifest: bool = False
    log_level: str = "INFO"
    dry_run: bool = False
    output_override: str | None = None
    report_dir: str | None = None
    no_report: bool = False

    # Derived (set post-init via resolve())
    fcast_filename: str = field(default="", init=False)
    hcast_filename: str = field(default="", init=False)
    obs_filename: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._validate()
        self._resolve_filenames()

    def _validate(self) -> None:
        if self.timescale == "seasonal":
            if self.lead is None:
                raise ValueError("Seasonal request requires a resolved lead.")
            if self.season_label is None:
                raise ValueError("Seasonal request requires season_label.")
        else:
            if self.intra_lead is None:
                raise ValueError("Intraseasonal request requires intra_lead.")

        if len(self.bbox) != 4:
            raise ValueError("bbox must be (lonW, latS, lonE, latN)")

        if self.aggreg not in ("total", "mean"):
            raise ValueError("aggreg must be 'total' or 'mean'")

        # Warn about unusual aggregation choices
        if self.variable in ("prcp", "rfreq") and self.aggreg == "mean":
            import warnings
            warnings.warn(
                f"Aggregation 'mean' selected for {self.variable}. "
                "Are you sure? The default for rainfall/rfreq is 'total'.",
                UserWarning,
                stacklevel=2,
            )

        if self.variable not in ("prcp", "rfreq") and self.aggreg == "total":
            raise ValueError(
                f"Aggregation 'total' is only valid for prcp/rfreq, "
                f"not for variable '{self.variable}'."
            )

    def _resolve_filenames(self) -> None:
        """Populate fcast/hcast/obs filename fields."""
        # Use short_label from registry if available, else derive from dataset_id
        # (Registry not available here; orchestrator/CLI can override post-construction)
        ds_label = getattr(self, "_ds_short_label", None) or (
            self.dataset_id
            .replace("cds_", "").replace("nmme_", "").replace("s2sdb_", "")
            .replace("subc_", "").replace("_seasonal", "").replace("_", "")
        )
        obs_label = getattr(self, "_obs_short_label", None) or (
            (self.obs_dataset_id or "").replace("_", "")
        )

        common = dict(
            variable=self.variable,
            init=self.init,
            timescale=self.timescale,
            clim_start=self.clim_start,
            clim_end=self.clim_end,
            output_override=self.output_override,
        )

        if self.timescale == "seasonal":
            landing_year = (self.init.year + self.season_year_offset)
            self.fcast_filename = build_filename(
                "fcast", ds_label, season_label=self.season_label,
                fcast_year=landing_year, **common,
            )
            self.hcast_filename = build_filename(
                "hcast", ds_label, season_label=self.season_label, **common,
            )
            if self.obs_dataset_id:
                self.obs_filename = build_filename(
                    "obs", obs_label, season_label=self.season_label, **common,
                )
            else:
                # Primary dataset is obs/index (ERSST, RONI) used standalone —
                # build obs_filename from the dataset's own label
                self.obs_filename = build_filename(
                    "obs", ds_label, season_label=self.season_label, **common,
                )
        else:
            self.fcast_filename = build_filename(
                "fcast", ds_label,
                tgt_start=self.intra_lead.tgt_start,
                tgt_end=self.intra_lead.tgt_end,
                **common,
            )
            # Multi-init mode: filename year range = actual init years, not clim_years
            hcast_common = dict(common)
            if self.multi_init_years:
                hcast_common["clim_start"] = self.multi_init_years[0]
                hcast_common["clim_end"]   = self.multi_init_years[1]
            self.hcast_filename = build_filename(
                "hcast", ds_label,
                tgt_start=self.intra_lead.tgt_start,
                tgt_end=self.intra_lead.tgt_end,
                **hcast_common,
            )
            if self.obs_dataset_id:
                file_role = "rean"
                self.obs_filename = build_filename(
                    file_role, obs_label,
                    tgt_start=self.intra_lead.tgt_start,
                    tgt_end=self.intra_lead.tgt_end,
                    **hcast_common,
                )

    def summary(self) -> str:
        """Human-readable summary for --dry-run."""
        lonW, latS, lonE, latN = self.bbox
        lines = [
            "CPT-DM Request (dry-run)",
            f"  timescale  : {self.timescale}",
            f"  dataset    : {self.dataset_id}",
            f"  obs/rean   : {self.obs_dataset_id or '(not specified)'}",
            f"  variable   : {self.variable}",
            f"  init       : {self.init.isoformat()}",
            f"  aggreg     : {self.aggreg}",
            f"  domain     : W={lonW}° E={lonE}° S={latS}° N={latN}°",
        ]
        if self.timescale == "seasonal":
            lines += [
                f"  lead       : months {self.lead.lead_start}–{self.lead.lead_end}",
                f"  season     : {self.season_label}",
                f"  clim       : {self.clim_start}–{self.clim_end}",
                f"  fcast file : {self.fcast_filename}",
                f"  hcast file : {self.hcast_filename}",
                f"  obs file   : {self.obs_filename or '(none)'}",
            ]
        else:
            lines += [
                f"  lead       : {self.intra_lead.label}",
                f"  tgt_start  : {self.intra_lead.tgt_start.isoformat()}",
                f"  tgt_end    : {self.intra_lead.tgt_end.isoformat()}",
                f"  clim       : {self.clim_start}–{self.clim_end}",
            ]
            if self.multi_init_years:
                y1, y2 = self.multi_init_years
                lines.append(f"  multi-init : {y1}–{y2} (all inits written to single CPT file)")
            lines += [
                f"  fcast file : {self.fcast_filename}",
                f"  hcast file : {self.hcast_filename}",
                f"  rean file  : {self.obs_filename or '(none)'}",
            ]
        lines.append(f"  ensemble   : {self.ensemble_stat}")
        lines.append(f"  anomalies  : {'YES (--anomalies set)' if self.compute_anomalies else 'NO (full field, default)'}")
        if self.clim_was_capped:
            lines.append(f"  clim note  : period was capped to dataset availability")
        lines.append(f"  outdir     : {self.outdir}")
        return "\n".join(lines)
