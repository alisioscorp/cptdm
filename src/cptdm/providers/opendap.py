"""Stub — OPeNDAP/THREDDS provider."""
from __future__ import annotations
from pathlib import Path
import xarray as xr
from cptdm.registry.loader import DatasetEntry
from cptdm.request import Request

class OPeNDAPProvider:
    def __init__(self, entry: DatasetEntry) -> None:
        self.entry = entry
    def fetch(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        raise NotImplementedError("OPeNDAP provider not yet implemented.")
