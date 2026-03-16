#!/usr/bin/env bash
# =============================================================================
# CPT-DM installer
# Run once after untarring:  bash install.sh
#
# What it does:
#   1. Finds a suitable Python (3.9+)
#   2. Creates a virtual environment in .venv/
#   3. Installs CPT-DM and its dependencies
#   4. Prints usage instructions
# =============================================================================

set -e

CPTDM_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$CPTDM_DIR/.venv"

# ── Find Python ───────────────────────────────────────────────────────────────
find_python() {
    for cmd in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            version=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
            major=$("$cmd" -c "import sys; print(sys.version_info[0])" 2>/dev/null)
            minor=$("$cmd" -c "import sys; print(sys.version_info[1])" 2>/dev/null)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ] 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    echo "ERROR: Python 3.9+ not found. Please install Python 3.9 or later."
    exit 1
}

echo "Using Python: $PYTHON ($($PYTHON --version))"

# ── Create virtual environment ────────────────────────────────────────────────
echo ""
echo "Creating virtual environment in $VENV_DIR ..."
"$PYTHON" -m venv "$VENV_DIR"

PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"

# ── Upgrade pip ───────────────────────────────────────────────────────────────
echo "Upgrading pip ..."
"$PIP" install --upgrade pip setuptools wheel --quiet

# ── Install CPT-DM ───────────────────────────────────────────────────────────
echo "Installing CPT-DM ..."
"$PIP" install -e "$CPTDM_DIR" --quiet

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  CPT-DM installed successfully."
echo "============================================================"
echo ""
echo "  To use CPT-DM, either:"
echo ""
echo "  a) Activate the virtual environment:"
echo "       source $VENV_DIR/bin/activate"
echo "       cptdm --help"
echo ""
echo "  b) Run directly without activating:"
echo "       $VENV_DIR/bin/cptdm --help"
echo ""
echo "  c) Run without installing (no activation needed):"
echo "       $PYTHON_VENV $CPTDM_DIR/cptdm_run.py --help"
echo ""
echo "  Quick test (no CDS credentials needed):"
echo "       cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \\"
echo "             --obs-dataset chirps3 --variable prcp \\"
echo "             --init 2026-03 --lead 1-3 --bbox -10,34,5,45 --dry-run"
echo ""
