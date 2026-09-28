#!/usr/bin/env bash
# =============================================================================
# test_s2sdb_all_centres.sh — S2S Database integration test — tests all 12 S2SDB centres for intraseasonal forecast downloads.
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
#
# Tests real-time forecast download from all 12 S2S Database centres.
# Small domain (Iberian Peninsula), prcp only, week2 lead.
#
# Init date: 2010-01-04 (Monday — valid for twice-weekly centres)
# This date is well within all models' reforecast periods and safely
# past the embargo window.
#
# Prerequisites:
#   - ~/.ecmwfapirc configured with ECMWF S2S access
#   - cptdm installed or run from source
#
# Usage:
#   chmod +x tests/test_s2sdb_all_centres.sh
#   cd cptdm/
#   ./tests/test_s2sdb_all_centres.sh [--live]
#
# Without --live: dry-run only (no downloads, tests request construction).
# With --live: actual MARS retrieval (requires S2S credentials + access).
# =============================================================================

set -u

BBOX="-10,34,5,45"           # Iberian Peninsula
INIT="2010-01-04"            # Monday, within all reforecast periods
LEAD="week2"
VARIABLE="prcp"
OUTDIR="./CPTFiles/s2sdb_test"
LOGDIR="./CPTFiles/s2sdb_test/logs"

DRY_RUN="--dry-run"
if [[ "${1:-}" == "--live" ]]; then
    DRY_RUN=""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  S2SDB LIVE TEST — all 12 centres (prcp, week2)            ║"
    echo "║  Init: $INIT   Domain: $BBOX                    ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
else
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  S2SDB DRY-RUN TEST — all 12 centres (prcp, week2)        ║"
    echo "║  Pass --live for actual MARS downloads                     ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
fi

mkdir -p "$OUTDIR" "$LOGDIR"

CENTRES=(
    s2sdb_ecmwf
    s2sdb_ncep
    s2sdb_eccc
    s2sdb_ukmo
    s2sdb_jma
    s2sdb_cma
    s2sdb_bom
    s2sdb_cnrm
    s2sdb_isac
    s2sdb_cptec
    s2sdb_hmcr
    s2sdb_kma
)

PASS=0
FAIL=0
SKIP=0
RESULTS=()

for DS in "${CENTRES[@]}"; do
    echo ""
    echo "── Testing $DS ──────────────────────────────────────"
    LOG="$LOGDIR/${DS}.log"

    if cptdm \
        --timescale intraseasonal \
        --dataset "$DS" \
        --variable "$VARIABLE" \
        --init "$INIT" \
        --lead "$LEAD" \
        --bbox "$BBOX" \
        --outdir "$OUTDIR" \
        --log-level INFO \
        $DRY_RUN \
        2>&1 | tee "$LOG"; then

        RESULTS+=("  ✅  $DS")
        PASS=$((PASS + 1))
    else
        RESULTS+=("  ❌  $DS")
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  RESULTS: $PASS passed, $FAIL failed, $SKIP skipped"
echo "══════════════════════════════════════════════════════════════"
for R in "${RESULTS[@]}"; do
    echo "$R"
done
echo ""
echo "Logs: $LOGDIR/"
if [[ -z "$DRY_RUN" ]]; then
    echo "Output TSVs: $OUTDIR/"
fi
echo ""

exit $FAIL
