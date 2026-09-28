# =============================================================================
# credentials.py — Credential resolution — locates CDS, ECDS, and ECMWF API keys from files or environment variables.
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
cptdm.auth.credentials
~~~~~~~~~~~~~~~~~~~~~~~
Credential resolution for CPT-DM data providers.

Resolution order for CDS credentials (Copernicus CDS):
  1. Explicit --credentials flag (path to a .cdsapirc-style file)
  2. Environment variable CDSAPI_KEY  (format: "uid:key")
  3. Standard ~/.cdsapirc  (cdsapi's own default)

Resolution order for ECDS credentials (ECMWF Climate Data Store, S2S):
  ECDS uses the same cdsapi client and the same ~/.cdsapirc file, but with
  url: https://ecds.ecmwf.int/api  (instead of the Copernicus CDS URL).
  Alternatively: CDSAPI_URL=https://ecds.ecmwf.int/api + CDSAPI_KEY env vars.

No credentials are hard-coded here or in the registry.
This module provides early validation so users get a clear, actionable
error message before any network call is attempted.
"""
from __future__ import annotations

import os
from pathlib import Path


class CredentialError(RuntimeError):
    """Raised when required credentials cannot be found or are malformed."""


def resolve_cds_credentials(explicit_path: str | None = None) -> Path | None:
    """
    Locate the CDS API credentials file and return its path.

    Args:
        explicit_path: Path passed via --credentials flag. If None, the
                       standard locations are checked.

    Returns:
        Path to the credentials file if found at an explicit or standard
        location, or None if cdsapi should handle resolution itself
        (e.g. via environment variable CDSAPI_KEY).

    Raises:
        CredentialError: if an explicit path was given but does not exist,
                         or if no credentials can be found anywhere.
    """
    # 1. Explicit path from CLI
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if not p.exists():
            raise CredentialError(
                f"Credentials file not found: {p}\n"
                "Please check the path passed to --credentials."
            )
        return p

    # 2. Environment variable (cdsapi also reads this; we just confirm it's set)
    if os.environ.get("CDSAPI_KEY"):
        # cdsapi will handle this; no file needed
        return None

    # 3. Standard ~/.cdsapirc
    default = Path.home() / ".cdsapirc"
    if default.exists():
        return default

    # Nothing found — raise a helpful error
    raise CredentialError(
        "CDS API credentials not found. CPT-DM needs credentials to download\n"
        "data from the Copernicus Climate Data Store.\n\n"
        "Please do ONE of the following:\n"
        "  a) Create ~/.cdsapirc with your CDS key:\n"
        "       url: https://cds.climate.copernicus.eu/api\n"
        "       key: <your-uid>:<your-api-key>\n\n"
        "  b) Set the CDSAPI_KEY environment variable:\n"
        "       export CDSAPI_KEY='<your-uid>:<your-api-key>'\n\n"
        "  c) Create the same .cdsapirc file as (a) but in a custom location,\n"
        "     then pass it explicitly:\n"
        "       cptdm ... --credentials /path/to/.cdsapirc\n\n"
        "Get your CDS API key at: https://cds.climate.copernicus.eu/user\n"
        "Note: you do NOT need Python or pip installed — only the credentials file."
    )


def validate_cds_credentials(explicit_path: str | None = None) -> None:
    """
    Run credential resolution and validate file format (if a file is used).
    Call this at startup before any downloads begin.

    Raises:
        CredentialError: with a clear, actionable message.
    """
    path = resolve_cds_credentials(explicit_path)

    if path is None:
        # Using environment variable — trust cdsapi to handle it
        return

    # Basic format check: file should contain 'url' and 'key' lines
    text = path.read_text(encoding="utf-8")
    has_url = any(line.strip().startswith("url") for line in text.splitlines())
    has_key = any(line.strip().startswith("key") for line in text.splitlines())

    if not (has_url and has_key):
        raise CredentialError(
            f"Credentials file at {path} appears to be malformed.\n"
            "Expected format:\n"
            "  url: https://cds.climate.copernicus.eu/api\n"
            "  key: <your-uid>:<your-api-key>\n\n"
            "Get your CDS API key at: https://cds.climate.copernicus.eu/user"
        )


def resolve_ecds_credentials() -> Path | None:
    """
    Locate credentials suitable for ECDS (ECMWF Climate Data Store) access.

    ECDS uses the same cdsapi client as Copernicus CDS. Resolution order:

      1. CDSAPI_URL env var set to an ecds.ecmwf.int endpoint — cdsapi handles it.
      2. ~/.cdsapirc with url containing 'ecds.ecmwf.int'.
      3. ~/.ecdsapirc — dedicated ECDS credentials file (for users who maintain
         separate files for CDS and ECDS, which is the common case).

    When ~/.ecdsapirc is found, cdsapi is invoked with explicit url= and key=
    read from that file, since cdsapi only auto-reads ~/.cdsapirc by default.

    Returns:
        Path to the credentials file if found, or None if the env var is set
        (cdsapi handles it) or no ECDS credentials exist (not an error —
        MARS fallback is available).
    """
    # 1. Environment variable override
    cdsapi_url = os.environ.get("CDSAPI_URL", "")
    if "ecds.ecmwf.int" in cdsapi_url:
        return None

    # 2. ~/.cdsapirc pointing at ECDS
    default = Path.home() / ".cdsapirc"
    if default.exists():
        text = default.read_text(encoding="utf-8")
        if "ecds.ecmwf.int" in text:
            return default

    # 3. Dedicated ~/.ecdsapirc
    ecdsrc = Path.home() / ".ecdsapirc"
    if ecdsrc.exists():
        return ecdsrc

    return None  # No ECDS credentials — not an error; MARS fallback is available


def validate_ecds_credentials() -> bool:
    """
    Check whether ECDS credentials are available and appear valid.
    Returns True if ECDS can be used, False otherwise.
    Does NOT raise — ECDS is optional (MARS is the fallback).

    ECDS credentials: ~/.cdsapirc with url: https://ecds.ecmwf.int/api + key,
    or CDSAPI_URL / CDSAPI_KEY env vars.
    """
    # Env var path: both URL (pointing at ECDS) and KEY must be set
    cdsapi_url = os.environ.get("CDSAPI_URL", "")
    cdsapi_key = os.environ.get("CDSAPI_KEY", "")
    if "ecds.ecmwf.int" in cdsapi_url and cdsapi_key:
        return True

    # File path: ~/.cdsapirc must exist, contain ecds URL, and have a key line
    path = resolve_ecds_credentials()
    if path is None:
        return False

    text = path.read_text(encoding="utf-8")
    has_ecds_url = "ecds.ecmwf.int" in text
    has_key      = any(line.strip().startswith("key") for line in text.splitlines())

    return has_ecds_url and has_key
