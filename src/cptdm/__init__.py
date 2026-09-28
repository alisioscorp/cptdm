# =============================================================================
# __init__.py — Package initialisation — exports the package version string.
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
# cptdm – CPT Data Acquisition and Download Module
# Copyright Alisios Corporation. All rights reserved.

__version__ = "1.0.4"
__author__ = "Ángel G. Muñoz"

# ---------------------------------------------------------------------------
# Global warning suppression — must run before any provider imports xarray.
# cfgrib: the ecCodes C library is not bundled in the Nuitka onefile binary;
#         CPT-DM uses netCDF4 for all downloads so cfgrib is never needed.
# numpy.ma: numpy ≥2.2 has a broken docstring assertion; we pin <2.2 at
#           build time but suppress just in case.
# ---------------------------------------------------------------------------
import warnings as _warnings
_warnings.filterwarnings("ignore", message=".*Engine.*cfgrib.*loading failed.*")
_warnings.filterwarnings("ignore", message=".*Failed to replace.*fromfunction.*")
