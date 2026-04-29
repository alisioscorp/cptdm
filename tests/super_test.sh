#!/usr/bin/env bash
# =============================================================================
# CPT-DM v0.7.0 — SUPER TEST (live + available_years)
# =============================================================================
#
# Comprehensive test of ALL models × variables × obs combinations, plus
# intraseasonal (S2SDB + SubC), --full-obs, and v0.7.0 available_years
# features (open-ended, colon syntax, gap handling).
#
# LIVE MODE: uses --no-cache so every download is fresh. Small domain and
# short clim periods to keep total runtime manageable (~4-6 hours).
#
# All cache and output files go to /Volumes/DataFridge/CPT-DM/ to avoid
# filling the boot drive.
#
# Usage:
#   conda activate cptdm-build
#   cd /Volumes/Models/NextGen/CPT-DM/cptdm
#   bash tests/super_test.sh              # full live run
#   bash tests/super_test.sh --dry-run    # validate all requests without downloading
#
# =============================================================================

set -u

# ── Configuration ─────────────────────────────────────────────────────────────
BASE="/Volumes/DataFridge/CPT-DM/super_test_v070"
OUTDIR="$BASE/output"
CACHEDIR="$BASE/cache"
LOGDIR="$BASE/logs"

# Tiny domain: 5°×5° box in Iberian Peninsula
BBOX="-5,37,0,42"

# Short clim: 5 years (fast downloads, still validates the pipeline)
CLIM="2010-2014"

# Seasonal init: March 2024
SEAS_INIT="2024-03"
SEAS_LEAD="1-3"          # AMJ target

# Intraseasonal init: well within all S2SDB reforecast periods
INTRA_INIT="2010-01-04"  # Monday
INTRA_LEAD="week2"

# GEM5.2-NEMO: only inits 05-07 work
GEM52_INIT="2024-06"

# --full-obs: 2 years only
FULLOBS_YEARS="2019-2020"

# Force fresh downloads
NOCACHE="--no-cache"

# ── Parse flags ───────────────────────────────────────────────────────────────
DRY=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY="--dry-run"
    NOCACHE=""   # no need for --no-cache in dry-run
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  CPT-DM v0.7.0 SUPER TEST — DRY RUN (no downloads)            ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
else
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  CPT-DM v0.7.0 SUPER TEST — FULL LIVE RUN (--no-cache)        ║"
    echo "║  Domain : $BBOX (5°×5°)"
    echo "║  Clim   : $CLIM (5 years)"
    echo "║  Output : $OUTDIR"
    echo "║  Cache  : $CACHEDIR"
    echo "║  Logs   : $LOGDIR"
    echo "╚══════════════════════════════════════════════════════════════════╝"
fi

mkdir -p "$OUTDIR" "$CACHEDIR" "$LOGDIR"

# Master log — tee everything to a single file for sharing with Simon/Nick
MASTER_LOG="$BASE/super_test_v070_report_$(date +%Y%m%d_%H%M%S).txt"
exec > >(tee "$MASTER_LOG") 2>&1

echo "CPT-DM v0.7.0 Super Test Report"
echo "================================"
echo "Date     : $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Host     : $(hostname)"
echo "Version  : $(cptdm --version 2>&1 || echo 'unknown')"
echo "Domain   : $BBOX"
echo "Clim     : $CLIM"
echo "No-cache : ${NOCACHE:-off}"
echo "Output   : $OUTDIR"
echo "Cache    : $CACHEDIR"
echo "Logs     : $LOGDIR"
echo ""

PASS=0
FAIL=0
SKIP=0
RESULTS=()
TEST_NUM=0

# ── Helpers ───────────────────────────────────────────────────────────────────
run_test() {
    local label="$1"
    shift
    TEST_NUM=$((TEST_NUM + 1))
    local padded=$(printf "%03d" $TEST_NUM)
    local logfile="$LOGDIR/${padded}_$(echo "$label" | tr ' /()' '____').log"

    echo ""
    echo "── [$padded] $label ──"

    if cptdm "$@" \
        --outdir "$OUTDIR" \
        --cache-dir "$CACHEDIR" \
        --log-level INFO \
        $NOCACHE $DRY \
        > "$logfile" 2>&1; then
        PASS=$((PASS + 1))
        RESULTS+=("  ✅  [$padded] $label")
        echo "  ✅  PASS"
    else
        local exit_code=$?
        FAIL=$((FAIL + 1))
        RESULTS+=("  ❌  [$padded] $label  (exit=$exit_code)")
        echo "  ❌  FAIL (exit=$exit_code) — see $logfile"
    fi
}

run_test_fullobs() {
    local label="$1"
    shift
    TEST_NUM=$((TEST_NUM + 1))
    local padded=$(printf "%03d" $TEST_NUM)
    local logfile="$LOGDIR/${padded}_$(echo "$label" | tr ' /()' '____').log"

    echo ""
    echo "── [$padded] $label ──"

    if cptdm "$@" \
        --outdir "$OUTDIR" \
        --cache-dir "$CACHEDIR" \
        $NOCACHE $DRY \
        > "$logfile" 2>&1; then
        PASS=$((PASS + 1))
        RESULTS+=("  ✅  [$padded] $label")
        echo "  ✅  PASS"
    else
        local exit_code=$?
        FAIL=$((FAIL + 1))
        RESULTS+=("  ❌  [$padded] $label  (exit=$exit_code)")
        echo "  ❌  FAIL (exit=$exit_code) — see $logfile"
    fi
}


# #############################################################################
#  PART 1: SEASONAL — CDS MODELS (8 models)
# #############################################################################
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  PART 1: SEASONAL — CDS MODELS"
echo "═══════════════════════════════════════════════════════════════════"

# ── 1a. prcp + CHIRPS ─────────────────────────────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal \
          cds_ukmo_glosea605 cds_meteo_france_system8 cds_cmcc_sps35 \
          cds_dwd_gcfs21 cds_jma_cps3; do
    run_test "seasonal $DS prcp+chirps3" \
        -t seasonal -d "$DS" --obs chirps3 -v prcp \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1b. prcp + CRU ───────────────────────────────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal; do
    run_test "seasonal $DS prcp+cru409" \
        -t seasonal -d "$DS" --obs cru409 -v prcp \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1c. tmean + ERA5-Land ────────────────────────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal \
          cds_ukmo_glosea605 cds_meteo_france_system8 cds_cmcc_sps35 \
          cds_dwd_gcfs21 cds_jma_cps3; do
    run_test "seasonal $DS tmean+era5land" \
        -t seasonal -d "$DS" --obs era5land -v tmean \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1d. tmean + CRU ──────────────────────────────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal; do
    run_test "seasonal $DS tmean+cru409" \
        -t seasonal -d "$DS" --obs cru409 -v tmean \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1e. tmax/tmin (ECMWF, NCEP, ECCC only) ──────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal; do
    run_test "seasonal $DS tmax+era5land" \
        -t seasonal -d "$DS" --obs era5land -v tmax \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"

    run_test "seasonal $DS tmin+era5land" \
        -t seasonal -d "$DS" --obs era5land -v tmin \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1f. rfreq + CHIRPS (ALL 8 CDS models) ───────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal \
          cds_ukmo_glosea605 cds_meteo_france_system8 cds_cmcc_sps35 \
          cds_dwd_gcfs21 cds_jma_cps3; do
    run_test "seasonal $DS rfreq+chirps3" \
        -t seasonal -d "$DS" --obs chirps3 -v rfreq \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1g. rfreq + CRU ──────────────────────────────────────────────────────────
for DS in cds_ecmwf_seasonal cds_ncep_seasonal cds_eccc_seasonal; do
    run_test "seasonal $DS rfreq+cru409" \
        -t seasonal -d "$DS" --obs cru409 -v rfreq \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# ── 1h. Predictor datasets (ERSSTv5, RONI) — standalone ─────────────────────
run_test "seasonal ersst5 sst (standalone)" \
    -t seasonal -d ersst5 -v sst \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "-180,-60,180,60" --clim "$CLIM"

run_test "seasonal roni enso_index (standalone)" \
    -t seasonal -d roni -v enso_index \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" --clim "$CLIM"

# ── 1i. Different lead windows ───────────────────────────────────────────────
run_test "seasonal ecmwf lead 1-1 (single month)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "1-1" -b "$BBOX" --clim "$CLIM"

run_test "seasonal ecmwf lead 1-2 (two months)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "1-2" -b "$BBOX" --clim "$CLIM"

run_test "seasonal ecmwf lead 2-4 (FMA-like)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "2-4" -b "$BBOX" --clim "$CLIM"

# ── 1j. Custom clim period ───────────────────────────────────────────────────
run_test "seasonal ecmwf custom clim 2001-2020" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim 2001-2020

# ── 1k. Anomalies mode ──────────────────────────────────────────────────────
run_test "seasonal ecmwf anomalies" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM" --anomalies

# ── 1l. Global domain (no bbox, CHIRPS auto-cap) ────────────────────────────
run_test "seasonal ecmwf global domain" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" --clim "$CLIM"


# #############################################################################
#  PART 2: SEASONAL — NMME MODELS (7 models)
# #############################################################################
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  PART 2: SEASONAL — NMME MODELS"
echo "═══════════════════════════════════════════════════════════════════"

for DS in nmme_cfsv2 nmme_cansipsv2 nmme_gem_nemo \
          nmme_nasa_geoss2s nmme_ncar_cesm1; do
    run_test "seasonal $DS prcp+chirps3" \
        -t seasonal -d "$DS" --obs chirps3 -v prcp \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"

    run_test "seasonal $DS tmean+era5land" \
        -t seasonal -d "$DS" --obs era5land -v tmean \
        -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"
done

# NMME CFSv2 rfreq
run_test "seasonal nmme_cfsv2 rfreq+chirps3" \
    -t seasonal -d nmme_cfsv2 --obs chirps3 -v rfreq \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"

# GEM5.2-NEMO: hindcast_only, init months 05-07 only
run_test "seasonal nmme_gem52_nemo prcp+chirps3 (init June)" \
    -t seasonal -d nmme_gem52_nemo --obs chirps3 -v prcp \
    -i "$GEM52_INIT" -l "$SEAS_LEAD" -b "$BBOX"

# GFDL-SPEAR
run_test "seasonal nmme_gfdl_spear prcp+chirps3" \
    -t seasonal -d nmme_gfdl_spear --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "$CLIM"


# #############################################################################
#  PART 3: INTRASEASONAL — S2SDB (12 centres)
# #############################################################################
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  PART 3: INTRASEASONAL — S2SDB (12 centres)"
echo "═══════════════════════════════════════════════════════════════════"

for DS in s2sdb_ecmwf s2sdb_ncep s2sdb_eccc s2sdb_ukmo s2sdb_jma \
          s2sdb_cma s2sdb_bom s2sdb_cnrm s2sdb_isac s2sdb_cptec \
          s2sdb_hmcr s2sdb_kma; do
    run_test "intraseasonal $DS prcp week2" \
        -t intraseasonal -d "$DS" -v prcp \
        -i "$INTRA_INIT" -l "$INTRA_LEAD" -b "$BBOX"
done

# tmean for a subset
for DS in s2sdb_ecmwf s2sdb_ncep s2sdb_ukmo; do
    run_test "intraseasonal $DS tmean week2" \
        -t intraseasonal -d "$DS" -v tmean \
        -i "$INTRA_INIT" -l "$INTRA_LEAD" -b "$BBOX"
done

# Different lead windows
for LEAD in week1 week3 week4 week34; do
    run_test "intraseasonal s2sdb_ecmwf prcp $LEAD" \
        -t intraseasonal -d s2sdb_ecmwf -v prcp \
        -i "$INTRA_INIT" -l "$LEAD" -b "$BBOX"
done


# #############################################################################
#  PART 4: INTRASEASONAL — SubC (3 datasets)
# #############################################################################
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  PART 4: INTRASEASONAL — SubC"
echo "═══════════════════════════════════════════════════════════════════"

for DS in subc_emean subc_emax subc_emin; do
    run_test "intraseasonal $DS prcp week2" \
        -t intraseasonal -d "$DS" -v prcp \
        -i "2025-03-10" -l week2 -b "$BBOX"

    run_test "intraseasonal $DS tmean week1" \
        -t intraseasonal -d "$DS" -v tmean \
        -i "2025-03-10" -l week1 -b "$BBOX"
done

# Multi-init SubC
run_test "intraseasonal subc_emean prcp multi-init 2023-2025" \
    -t intraseasonal -d subc_emean -v prcp \
    -i "2023/2025-03-10" -l week2 -b "$BBOX"


# #############################################################################
#  PART 5: FULL DAILY OBS (--full-obs)
# #############################################################################
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  PART 5: FULL DAILY OBS"
echo "═══════════════════════════════════════════════════════════════════"

run_test_fullobs "full-obs CHIRPS prcp $FULLOBS_YEARS" \
    --full-obs "$FULLOBS_YEARS" -v prcp -b "$BBOX"

run_test_fullobs "full-obs ERA5-Land tmean $FULLOBS_YEARS" \
    --full-obs "$FULLOBS_YEARS" -v tmean -b "$BBOX"


# #############################################################################
#  PART 6: v0.7.0 — AVAILABLE_YEARS EXTENSION
# #############################################################################
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  PART 6: v0.7.0 — AVAILABLE_YEARS (open-ended, colon, gaps)"
echo "═══════════════════════════════════════════════════════════════════"

# ── 6a. --clim colon syntax YYYY:YYYY ────────────────────────────────────────
run_test "v0.7.0: --clim colon 2010:2014 (ecmwf prcp)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2010:2014"

# ── 6b. --clim open-ended YYYY: ──────────────────────────────────────────────
run_test "v0.7.0: --clim open-ended 2020: (ecmwf prcp)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2020:"

# ── 6c. Extended beyond old hindcast end ─────────────────────────────────────
run_test "v0.7.0: --clim 1993:2025 extended (ecmwf prcp)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "1993:2025"

# ── 6d. Open-ended on NCEP ───────────────────────────────────────────────────
run_test "v0.7.0: --clim 2005: (ncep prcp)" \
    -t seasonal -d cds_ncep_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2005:"

# ── 6e. Open-ended on UKMO GloSea605 ────────────────────────────────────────
run_test "v0.7.0: --clim 2005: (glosea605 prcp)" \
    -t seasonal -d cds_ukmo_glosea605 --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2005:"

# ── 6f. Open-ended on NMME CFSv2 ────────────────────────────────────────────
run_test "v0.7.0: --clim 2010: (nmme_cfsv2 prcp)" \
    -t seasonal -d nmme_cfsv2 --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2010:"

# ── 6g. Backward-compat dash (same range as 6a) ─────────────────────────────
run_test "v0.7.0: --clim dash 2010-2014 (ecmwf prcp)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2010-2014"

# ── 6h. Open-ended tmean ─────────────────────────────────────────────────────
run_test "v0.7.0: --clim 2010: tmean (ecmwf)" \
    -t seasonal -d cds_ecmwf_seasonal --obs era5land -v tmean \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2010:"

# ── 6i. Open-ended rfreq ─────────────────────────────────────────────────────
run_test "v0.7.0: --clim 2010: rfreq (ecmwf)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v rfreq \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "2010:"

# ── 6j. Full open-ended 1993: (ecmwf, 34 years) ────────────────────────────
run_test "v0.7.0: --clim 1993: full open-ended (ecmwf prcp)" \
    -t seasonal -d cds_ecmwf_seasonal --obs chirps3 -v prcp \
    -i "$SEAS_INIT" -l "$SEAS_LEAD" -b "$BBOX" --clim "1993:"


# #############################################################################
#  RESULTS
# #############################################################################
echo ""
echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  SUPER TEST RESULTS: $PASS passed, $FAIL failed, $SKIP skipped"
echo "  Total tests: $TEST_NUM"
echo "══════════════════════════════════════════════════════════════════════"
for R in "${RESULTS[@]}"; do
    echo "$R"
done
echo ""
echo "Logs   : $LOGDIR/"
echo "Output : $OUTDIR/"
echo "Cache  : $CACHEDIR/"
echo ""

# Count output files
if [ -z "$DRY" ]; then
    TSV_COUNT=$(find "$OUTDIR" -name "*.tsv" 2>/dev/null | wc -l | tr -d ' ')
    echo "TSV files produced: $TSV_COUNT"
    echo ""
    echo "Output files (by size):"
    ls -lhS "$OUTDIR"/*.tsv 2>/dev/null | head -30
    echo "..."
    echo ""
    echo "Disk usage:"
    du -sh "$OUTDIR" "$CACHEDIR" 2>/dev/null
fi

echo ""
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "🎉  ALL $TEST_NUM TESTS PASSED!"
else
    echo "⚠️   $FAIL of $TEST_NUM test(s) FAILED — check logs above."
fi
echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  MASTER LOG (send to Simon/Nick): $MASTER_LOG"
echo "══════════════════════════════════════════════════════════════════════"

exit $FAIL
