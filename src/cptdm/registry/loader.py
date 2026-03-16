"""
cptdm.registry.loader
~~~~~~~~~~~~~~~~~~~~~
Loads, validates, and exposes the dataset registry (datasets.yml).

All access to dataset metadata goes through the Registry singleton.
No eval(); no mutable globals.
"""
from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Schema models
# ---------------------------------------------------------------------------

VALID_PROVIDERS = {"cds", "http", "opendap", "thredds", "s2sdb", "subc", "http_index"}
VALID_TIMESCALES = {"seasonal", "intraseasonal"}
VALID_FILE_TYPES = {"forecast", "hindcast", "obs", "reanalysis", "index"}
VALID_VARIABLES = {"prcp", "tmax", "tmin", "tmean", "sst", "enso_index", "rfreq"}


class DatasetEntry(BaseModel):
    """One entry in datasets.yml — the source of truth for a single dataset."""

    label: str
    short_label: str = ""
    provider: str
    timescales: List[str]
    file_type: str
    variables: List[str]
    clim_years: Optional[List[int]] = None
    valid_pairs: Optional[Dict[str, List[str]]] = None
    hindcast_only: bool = False          # True = no realtime forecast available
    cds_params: Optional[Dict[str, Any]] = None
    http_params: Optional[Dict[str, Any]] = None
    s2sdb_params: Optional[Dict[str, Any]] = None
    subc_params: Optional[Dict[str, Any]] = None
    opendap_params: Optional[Dict[str, Any]] = None

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        if v not in VALID_PROVIDERS:
            raise ValueError(f"Unknown provider '{v}'. Valid: {VALID_PROVIDERS}")
        return v

    @field_validator("timescales")
    @classmethod
    def _check_timescales(cls, v: List[str]) -> List[str]:
        bad = set(v) - VALID_TIMESCALES
        if bad:
            raise ValueError(f"Unknown timescale(s) {bad}. Valid: {VALID_TIMESCALES}")
        return v

    @field_validator("file_type")
    @classmethod
    def _check_file_type(cls, v: str) -> str:
        if v not in VALID_FILE_TYPES:
            raise ValueError(f"Unknown file_type '{v}'. Valid: {VALID_FILE_TYPES}")
        return v

    @field_validator("variables")
    @classmethod
    def _check_variables(cls, v: List[str]) -> List[str]:
        bad = set(v) - VALID_VARIABLES
        if bad:
            raise ValueError(f"Unknown variable(s) {bad}. Valid: {VALID_VARIABLES}")
        return v

    @model_validator(mode="after")
    def _check_clim_years(self) -> "DatasetEntry":
        if self.clim_years is not None:
            if len(self.clim_years) != 2:
                raise ValueError("clim_years must be [start, end]")
            if self.clim_years[0] >= self.clim_years[1]:
                raise ValueError("clim_years start must be < end")
        return self

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_forecast(self) -> bool:
        return self.file_type == "forecast"

    @property
    def is_obs_or_rean(self) -> bool:
        return self.file_type in {"obs", "reanalysis"}

    @property
    def output_prefix(self) -> str:
        """Return the CPT-DM filename prefix for this dataset type."""
        return {
            "forecast": "fcast",
            "hindcast": "hcast",
            "obs": "obs",
            "reanalysis": "rean",
            "index": "idx",
        }[self.file_type]

    def get_variable_params(self, variable: str) -> dict[str, Any]:
        """Return the per-variable sub-dict from whatever params block is set."""
        for block_name in ("cds_params", "http_params", "s2sdb_params",
                           "subc_params", "opendap_params"):
            block = getattr(self, block_name, None)
            if block and "variables" in block and variable in block["variables"]:
                return block["variables"][variable]
        raise KeyError(f"Variable '{variable}' not found in params for dataset")


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------

class Registry:
    """
    Validated, immutable view of datasets.yml.

    Usage:
        registry = Registry.load()               # from bundled default
        registry = Registry.load(path)           # from custom path
        entry = registry["cds_ecmwf_seasonal"]   # DatasetEntry
    """

    def __init__(self, entries: dict[str, DatasetEntry], schema_version: str) -> None:
        self._entries = entries
        self.schema_version = schema_version

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        """
        Load and validate datasets.yml.

        Args:
            path: Optional explicit path to a datasets.yml file.
                  If None, uses the bundled config/datasets.yml.

        Raises:
            FileNotFoundError: if path given but not found.
            ValueError: if YAML is malformed or schema validation fails.
        """
        if path is None:
            # Bundled default: shipped alongside the binary
            path = _default_registry_path()

        if not path.exists():
            raise FileNotFoundError(f"Registry file not found: {path}")

        with path.open("r", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh)

        schema_version = raw.pop("schema_version", "unknown")
        # Remove comment-only sections that are not dataset entries
        raw.pop("valid_pairing_matrix", None)

        entries: dict[str, DatasetEntry] = {}
        errors: list[str] = []

        for dataset_id, entry_dict in raw.items():
            if not isinstance(entry_dict, dict):
                continue  # skip non-entry top-level keys
            try:
                entries[dataset_id] = DatasetEntry.model_validate(entry_dict)
            except Exception as exc:
                errors.append(f"  [{dataset_id}]: {exc}")

        if errors:
            raise ValueError(
                "Registry validation failed:\n" + "\n".join(errors)
            )

        return cls(entries, schema_version)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def __getitem__(self, dataset_id: str) -> DatasetEntry:
        try:
            return self._entries[dataset_id]
        except KeyError:
            available = ", ".join(sorted(self._entries.keys()))
            raise KeyError(
                f"Dataset '{dataset_id}' not found in registry.\n"
                f"Available datasets: {available}"
            )

    def __contains__(self, dataset_id: str) -> bool:
        return dataset_id in self._entries

    def all_ids(self) -> list[str]:
        return sorted(self._entries.keys())

    def by_timescale(self, timescale: str) -> dict[str, DatasetEntry]:
        return {
            k: v for k, v in self._entries.items()
            if timescale in v.timescales
        }

    def by_file_type(self, file_type: str) -> dict[str, DatasetEntry]:
        return {
            k: v for k, v in self._entries.items()
            if v.file_type == file_type
        }

    # ------------------------------------------------------------------
    # Pairing validation
    # ------------------------------------------------------------------

    def validate_pair(
        self,
        forecast_id: str,
        obs_id: str,
        variable: str,
    ) -> None:
        """
        Raise ValueError if the forecast/obs combination is not valid
        for the given variable, according to valid_pairs in the registry.

        ERSST and RONI are predictor datasets (SST / ENSO index) used alongside
        any forecast variable (prcp, tmean, etc.).  They are valid obs for any
        forecast dataset that lists them in valid_pairs, regardless of whether
        they carry the same variable as the forecast.
        """
        fc_entry = self[forecast_id]
        if fc_entry.valid_pairs is None:
            raise ValueError(
                f"Dataset '{forecast_id}' has no valid_pairs defined."
            )

        obs_entry = self[obs_id]
        # Predictor datasets (sst, index) are valid with any forecast variable
        # as long as they appear in the forecast's valid_pairs for that variable.
        allowed = fc_entry.valid_pairs.get(variable, [])
        if obs_id not in allowed:
            raise ValueError(
                f"Invalid pairing: '{forecast_id}' + '{obs_id}' for variable '{variable}'.\n"
                f"Allowed obs/rean datasets for {forecast_id}/{variable}: {allowed}"
            )


# ---------------------------------------------------------------------------
# Default path resolution
# ---------------------------------------------------------------------------

def _default_registry_path() -> Path:
    """
    Resolve the path to the bundled datasets.yml.
    Works both in development (src layout) and when compiled with Nuitka.
    """
    # Development: look relative to this file  (src/cptdm/registry/ → config/)
    here = Path(__file__).parent
    candidate = here.parent.parent.parent / "config" / "datasets.yml"
    if candidate.exists():
        return candidate

    # Installed package: look in package data
    try:
        ref = importlib.resources.files("cptdm") / "data" / "datasets.yml"
        return Path(str(ref))
    except Exception:
        pass

    raise FileNotFoundError(
        "Could not locate datasets.yml. "
        "Pass an explicit path to Registry.load(path=...)."
    )
