"""
cptdm.auth.credentials
~~~~~~~~~~~~~~~~~~~~~~~
Credential resolution for CPT-DM data providers.

Resolution order for CDS credentials:
  1. Explicit --credentials flag (path to a .cdsapirc-style file)
  2. Environment variable CDSAPI_KEY  (format: "uid:key")
  3. Standard ~/.cdsapirc  (cdsapi's own default)

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
        "       key: <your-uid>:<your-api-key>\n"
        "  b) Set the CDSAPI_KEY environment variable:\n"
        "       export CDSAPI_KEY='<your-uid>:<your-api-key>'\n"
        "  c) Pass an explicit credentials file:\n"
        "       cptdm ... --credentials /path/to/.cdsapirc\n\n"
        "Get your CDS API key at: https://cds.climate.copernicus.eu/user"
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
