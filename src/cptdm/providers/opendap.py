# =============================================================================
# opendap.py — OPeNDAP provider — stub for future OPeNDAP/THREDDS data access.
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
