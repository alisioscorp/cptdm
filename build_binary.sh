#!/usr/bin/env bash
# =============================================================================
# CPT-DM  —  Nuitka Commercial binary build script
# =============================================================================
#
# Produces a single self-contained executable for the CURRENT platform:
#   dist/cptdm        (macOS / Linux)
#   dist/cptdm.exe    (Windows)
#
# No Python installation required on the target machine.
# Source code is ALWAYS protected via Nuitka's data-hiding plugin.
#
# ─── REQUIREMENTS ────────────────────────────────────────────────────────────
#   1. Nuitka Commercial installed in the active environment:
#        pip install /path/to/Nuitka-commercial-main.zip --force-reinstall
#   2. C compiler:
#        macOS   → xcode-select --install
#        Linux   → apt install gcc  /  yum install gcc
#        Windows → MSVC via Visual Studio Build Tools (--msvc=latest used below)
#   3. Active build environment with all cptdm runtime deps (see setup below)
#
# ─── CROSS-COMPILATION NOTE ──────────────────────────────────────────────────
#   Nuitka does NOT cross-compile. Each binary MUST be built natively:
#     macOS binary   → build on Witcher (macOS)
#     Linux binary   → build on Linux server  OR  via Podman from macOS
#     Windows binary → build on Windows server
#
#   Podman Linux build from macOS (produces CentOS-7-compatible binary):
#     brew install podman && podman machine init && podman machine start
#     python -m nuitka.tools.commercial.container_build \
#         /path/to/cptdm src/cptdm/__main__.py build \
#         --requirements="-r requirements-build.txt"
#
# ─── USAGE ───────────────────────────────────────────────────────────────────
#   conda activate cptdm-build      # activate build environment
#   bash build_binary.sh            # build for current platform
#   bash build_binary.sh --clean    # clean previous artefacts first
#   bash build_binary.sh --test     # build + smoke test
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"
OUT_DIR="$SCRIPT_DIR/dist"
CONFIG_DIR="$SCRIPT_DIR/config"
BINARY_NAME="cptdm"

# Entry point is the package directory (avoids __main__.py path issues on Windows)
ENTRY="$SRC_DIR/cptdm"

# ── Arguments ─────────────────────────────────────────────────────────────────
CLEAN=0; RUN_TEST=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        --test)  RUN_TEST=1 ;;
        --help)  grep "^#" "$0" | sed 's/^# \{0,2\}//' | head -50; exit 0 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[build]${NC}  $*"; }
success() { echo -e "${GREEN}[build]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[build]${NC}  $*"; }
error()   { echo -e "${RED}[build]${NC}  $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────────
CPTDM_VERSION=$(python -c \
    'import sys; sys.path.insert(0,"src"); from cptdm import __version__; print(__version__)' \
    2>/dev/null) || error "Cannot import cptdm. Activate the build environment first."

info "CPT-DM v${CPTDM_VERSION} — Nuitka Commercial binary build"
echo ""
info "Python   : $(python --version) @ $(which python)"

python -m nuitka --version &>/dev/null \
    || error "Nuitka not found. Run: pip install /path/to/Nuitka-commercial-main.zip"
NUITKA_VER=$(python -m nuitka --version 2>&1 | head -1)
info "Nuitka   : $NUITKA_VER"

python -c "import nuitka.plugins.commercial" 2>/dev/null \
    || error "Nuitka commercial plugins missing. Reinstall from Nuitka-commercial-main.zip."
info "Commercial plugins: OK"

python -c "import zstandard" 2>/dev/null \
    || error "zstandard missing. Run: pip install zstandard"
python -c "import Cryptodome" 2>/dev/null \
    || error "pycryptodomex missing. Run: pip install pycryptodomex"

# Detect platform
OS="$(uname -s 2>/dev/null || echo Windows)"
case "$OS" in
    Darwin)  PLATFORM="macos"   ;;
    Linux)   PLATFORM="linux"   ;;
    *)       PLATFORM="windows" ;;
esac
info "Platform : $PLATFORM"
echo ""

# C compiler check
if [ "$PLATFORM" != "windows" ]; then
    command -v gcc &>/dev/null || command -v clang &>/dev/null \
        || error "No C compiler. macOS: xcode-select --install | Linux: apt install gcc"
fi

# ── Clean ─────────────────────────────────────────────────────────────────────
if [ "$CLEAN" -eq 1 ]; then
    info "Cleaning previous build..."
    rm -rf "$OUT_DIR" \
        "$SCRIPT_DIR/__main__.build" "$SCRIPT_DIR/__main__.dist" \
        "$SCRIPT_DIR/__main__.onefile-build" \
        "$SCRIPT_DIR/cli.build" "$SCRIPT_DIR/cli.dist" "$SCRIPT_DIR/cli.onefile-build"
fi
mkdir -p "$OUT_DIR"

# ── Packages ──────────────────────────────────────────────────────────────────
INCLUDE_PACKAGES=(
    cptdm cdsapi
    xarray numpy pandas
    netCDF4 cfgrib eccodes cftime
    pydantic pydantic_core
    yaml requests urllib3 certifi charset_normalizer
    aiohttp aiosignal frozenlist multidict yarl
    rich click
    zstandard
)
INCLUDE_ARGS=""
for pkg in "${INCLUDE_PACKAGES[@]}"; do
    INCLUDE_ARGS="$INCLUDE_ARGS --include-package=$pkg"
done

# ── Windows: force MSVC (avoids AV false positives from MinGW) ────────────────
PLATFORM_FLAGS=""
[ "$PLATFORM" = "windows" ] && PLATFORM_FLAGS="--msvc=latest"

BINARY_OUT="$OUT_DIR/$BINARY_NAME"
[ "$PLATFORM" = "windows" ] && BINARY_OUT="${BINARY_OUT}.exe"

# ── Build ─────────────────────────────────────────────────────────────────────
info "Compiling... (5–15 min on first build, faster subsequently)"
echo ""

python -m nuitka \
    --standalone \
    --onefile \
    --output-dir="$OUT_DIR" \
    --output-filename="$BINARY_NAME" \
    \
    --enable-plugin=data-hiding \
    \
    --python-flag=no_site \
    --python-flag=isolated \
    --python-flag=no_docstrings \
    \
    $INCLUDE_ARGS \
    \
    --include-data-files="$CONFIG_DIR/datasets.yml=cptdm/data/datasets.yml" \
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
    $PLATFORM_FLAGS \
    --warn-implicit-exceptions \
    \
    "$ENTRY"

echo ""

# ── Post-build ────────────────────────────────────────────────────────────────
[ -f "$BINARY_OUT" ] || error "Binary not found after build: $BINARY_OUT"
chmod +x "$BINARY_OUT"

CHECKSUM_FILE="$OUT_DIR/$BINARY_NAME.sha256"
if command -v sha256sum &>/dev/null; then
    sha256sum "$BINARY_OUT" > "$CHECKSUM_FILE"
elif command -v shasum &>/dev/null; then
    shasum -a 256 "$BINARY_OUT" > "$CHECKSUM_FILE"
fi

BINARY_SIZE=$(du -sh "$BINARY_OUT" | cut -f1)
echo ""
success "══════════════════════════════════════════════════════════"
success "  Build complete!"
success "  Platform  : $PLATFORM"
success "  Binary    : $BINARY_OUT"
success "  Size      : $BINARY_SIZE"
[ -f "$CHECKSUM_FILE" ] && success "  SHA-256   : $(cut -d' ' -f1 $CHECKSUM_FILE)"
success "══════════════════════════════════════════════════════════"
echo ""

# ── Smoke test ────────────────────────────────────────────────────────────────
if [ "$RUN_TEST" -eq 1 ]; then
    info "Smoke test..."
    echo ""
    "$BINARY_OUT" --version
    echo ""
    "$BINARY_OUT" \
        --timescale seasonal --dataset cds_ecmwf_seasonal \
        --obs-dataset chirps3 --variable prcp \
        --init 2026-03 --lead 1-3 --bbox -10,34,5,45 \
        --dry-run
    echo ""
    success "Smoke test passed!"
fi
