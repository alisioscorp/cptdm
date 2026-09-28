# =============================================================================
# manifest.py — Manifest writer — generates manifest.json alongside output TSV files.
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
cptdm.cache.manifest
~~~~~~~~~~~~~~~~~~~~
Write a manifest.json recording inputs, settings, and outputs for a run.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from cptdm import __version__
from cptdm.request import Request


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(
    request: Request,
    written_files: list[Path],
    outdir: Path,
) -> Path:
    record = {
        "cptdm_version": __version__,
        "run_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "request": {
            "timescale": request.timescale,
            "dataset_id": request.dataset_id,
            "obs_dataset_id": request.obs_dataset_id,
            "variable": request.variable,
            "init": request.init.isoformat(),
            "aggreg": request.aggreg,
            "bbox": list(request.bbox),
            "clim": f"{request.clim_start}-{request.clim_end}",
            "ensemble_stat": request.ensemble_stat,
        },
        "outputs": [
            {
                "file": f.name,
                "path": str(f),
                "sha256": _sha256(f) if f.exists() else None,
            }
            for f in written_files
        ],
    }

    manifest_path = outdir / "manifest.json"
    with manifest_path.open("w") as fh:
        json.dump(record, fh, indent=2)

    return manifest_path
