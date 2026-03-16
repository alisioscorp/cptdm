#!/usr/bin/env bash
# =============================================================================
# CPT-DM — Docker internal build script
# Runs inside the cptdm-builder container.
# /workspace is mounted from the host (the cptdm source directory).
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[docker-build]${NC}  $*"; }
success() { echo -e "${GREEN}[docker-build]${NC}  $*"; }
error()   { echo -e "${RED}[docker-build]${NC}  $*" >&2; exit 1; }

WORKSPACE=/workspace
BINARY_NAME=cptdm

[ -f "$WORKSPACE/pyproject.toml" ] \
    || error "/workspace does not look like a cptdm source directory. Check your -v mount."
[ -f "$WORKSPACE/config/datasets.yml" ] \
    || error "config/datasets.yml not found in /workspace."

# ── Install cptdm ─────────────────────────────────────────────────────────────
info "Installing cptdm..."
cd "$WORKSPACE"
pip install -e . --quiet --root-user-action=ignore

CPTDM_VERSION=$(python -c \
    'import sys; sys.path.insert(0,"src"); from cptdm import __version__; print(__version__)')
info "CPT-DM version : $CPTDM_VERSION"
info "Python         : $(python --version)"
info "Nuitka         : $(python -m nuitka --version 2>&1 | head -1)"
info "Platform       : $(uname -m) Linux"
echo ""

# ── Clean ─────────────────────────────────────────────────────────────────────
info "Cleaning previous build artefacts..."
rm -rf dist/cptdm.build dist/cptdm.dist dist/cptdm.onefile-build \
       dist/__main__.build dist/__main__.dist dist/__main__.onefile-build
mkdir -p dist

# ── Compile ───────────────────────────────────────────────────────────────────
info "Starting Nuitka compilation with source protection..."
info "First build: 10–20 min (subsequent builds use ccache and are much faster)."
echo ""

python -m nuitka \
    --standalone \
    --onefile \
    --output-dir=dist \
    --output-filename="$BINARY_NAME" \
    \
    --enable-plugin=data-hiding \
    \
    --python-flag=no_site \
    --python-flag=isolated \
    --python-flag=no_docstrings \
    \
    --include-package=cptdm \
    --include-package=cdsapi \
    --include-package=xarray \
    --include-package=numpy \
    --include-package=pandas \
    --include-package=netCDF4 \
    --include-package=cfgrib \
    --include-package=eccodes \
    --include-package=cftime \
    --include-package=pydantic \
    --include-package=pydantic_core \
    --include-package=yaml \
    --include-package=requests \
    --include-package=urllib3 \
    --include-package=certifi \
    --include-package=charset_normalizer \
    --include-package=aiohttp \
    --include-package=aiosignal \
    --include-package=frozenlist \
    --include-package=multidict \
    --include-package=yarl \
    --include-package=rich \
    --include-package=click \
    --include-package=zstandard \
    \
    --include-data-files="config/datasets.yml=cptdm/data/datasets.yml" \
    \
    --enable-plugin=anti-bloat \
    --noinclude-pytest-mode=nofollow \
    --noinclude-setuptools-mode=nofollow \
    \
    --company-name="Alisios Corporation" \
    --product-name="CPT-DM" \
    --product-version="$CPTDM_VERSION" \
    --file-description="Climate Predictability Tool — Data Acquisition and Download Module" \
    --copyright="Copyright 2025 Alisios Corporation. All rights reserved." \
    \
    --warn-implicit-exceptions \
    --jobs=1 \
    src/cptdm

echo ""

# ── Post-build ────────────────────────────────────────────────────────────────
BINARY_OUT="dist/$BINARY_NAME"
[ -f "$BINARY_OUT" ] || error "Build completed but binary not found at $BINARY_OUT"
chmod +x "$BINARY_OUT"

sha256sum "$BINARY_OUT" > "dist/${BINARY_NAME}.linux.sha256"
BINARY_SIZE=$(du -sh "$BINARY_OUT" | cut -f1)
CHECKSUM=$(cut -d' ' -f1 "dist/${BINARY_NAME}.linux.sha256")

echo ""
success "══════════════════════════════════════════════════════════"
success "  Build complete!"
success "  Platform  : Linux $(uname -m)"
success "  Binary    : $BINARY_OUT"
success "  Size      : $BINARY_SIZE"
success "  SHA-256   : $CHECKSUM"
success "══════════════════════════════════════════════════════════"
echo ""

# ── Smoke test ────────────────────────────────────────────────────────────────
info "Smoke test..."
"$BINARY_OUT" --version
echo ""
"$BINARY_OUT" \
    --timescale seasonal \
    --dataset cds_ecmwf_seasonal \
    --obs-dataset chirps3 \
    --variable prcp \
    --init 2026-03 \
    --lead 1-3 \
    --bbox -10,34,5,45 \
    --dry-run

echo ""
success "Smoke test passed!"
success "Linux binary ready at: $WORKSPACE/dist/$BINARY_NAME"
