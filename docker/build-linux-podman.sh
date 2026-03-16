#!/usr/bin/env bash
# =============================================================================
# CPT-DM — Podman Linux x86_64 build wrapper (run on Witcher / macOS)
# =============================================================================
#
# Builds a Linux x86_64 binary from macOS using Podman.
# Forces --platform linux/amd64 so the binary runs on x86_64 Linux servers.
#
# USAGE (from cptdm/ directory on Witcher):
#   bash docker/build-linux-podman.sh
#   bash docker/build-linux-podman.sh --rebuild-image
#
# REQUIREMENTS:
#   brew install podman
#   podman machine init --cpus 4 --memory 8192 --disk-size 40
#   podman machine start
#
# NOTE on /Volumes paths:
#   Podman VM cannot see /Volumes/... directly. This script copies the source
#   to a temporary directory under $HOME (which IS visible to the VM),
#   runs the build there, then copies the binary back.
#
# OUTPUT:
#   dist/cptdm              Linux x86_64 binary
#   dist/cptdm.linux.sha256 SHA-256 checksum
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${CYAN}[podman-build]${NC}  $*"; }
success() { echo -e "${GREEN}[podman-build]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[podman-build]${NC}  $*"; }
error()   { echo -e "${RED}[podman-build]${NC}  $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CPTDM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="cptdm-builder-amd64"
CCACHE_VOLUME="cptdm-ccache-amd64"

# Temporary staging directory under $HOME (visible to Podman VM)
STAGE_DIR="$HOME/.cptdm-podman-build"

# ── Arguments ─────────────────────────────────────────────────────────────────
REBUILD_IMAGE=0
for arg in "$@"; do
    case "$arg" in
        --rebuild-image) REBUILD_IMAGE=1 ;;
        --help)
            grep "^#" "$0" | sed 's/^# \{0,2\}//' | head -30
            exit 0 ;;
    esac
done

# ── Find Nuitka zip ───────────────────────────────────────────────────────────
NUITKA_ZIP="${NUITKA_ZIP:-$HOME/Downloads/Nuitka-commercial-main.zip}"
[ -f "$NUITKA_ZIP" ] \
    || error "Nuitka zip not found at: $NUITKA_ZIP\nSet: export NUITKA_ZIP=/path/to/Nuitka-commercial-main.zip"

info "Nuitka zip : $NUITKA_ZIP"
info "Source dir : $CPTDM_DIR"
info "Stage dir  : $STAGE_DIR"
info "Platform   : linux/amd64 (forced)"

# ── Check Podman ──────────────────────────────────────────────────────────────
command -v podman &>/dev/null \
    || error "Podman not found. Install: brew install podman"

podman info &>/dev/null \
    || { warn "Podman machine not running. Starting..."; podman machine start; sleep 5; }

info "Podman : $(podman --version)"

# ── Stage source under $HOME so Podman VM can see it ─────────────────────────
info "Staging source to $STAGE_DIR..."
rm -rf "$STAGE_DIR"
# Copy only source — exclude heavy/unnecessary dirs
rsync -a --delete \
    --exclude='.venv' \
    --exclude='CPTFiles' \
    --exclude='dist' \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='*.pyc' \
    "$CPTDM_DIR/" "$STAGE_DIR/"
info "Source staged."

# ── Copy Nuitka zip into docker/ build context ────────────────────────────────
cp "$NUITKA_ZIP" "$SCRIPT_DIR/Nuitka-commercial-main.zip"
trap 'rm -f "$SCRIPT_DIR/Nuitka-commercial-main.zip"; echo ""' EXIT

# ── Build image (linux/amd64) ─────────────────────────────────────────────────
IMAGE_EXISTS=$(podman images -q "$IMAGE_NAME" 2>/dev/null)

if [ -z "$IMAGE_EXISTS" ] || [ "$REBUILD_IMAGE" -eq 1 ]; then
    info "Building container image '$IMAGE_NAME' for linux/amd64... (10–15 min first time)"
    podman build \
        --platform linux/amd64 \
        -t "$IMAGE_NAME" \
        "$SCRIPT_DIR"
    success "Image built."
else
    info "Image '$IMAGE_NAME' exists. Use --rebuild-image to force rebuild."
fi

# ── Create persistent ccache volume ──────────────────────────────────────────
podman volume create "$CCACHE_VOLUME" &>/dev/null || true
info "ccache volume : $CCACHE_VOLUME"

# ── Run build container ───────────────────────────────────────────────────────
echo ""
info "Starting Linux x86_64 build container..."
info "First build: 20–35 min (emulated x86 on ARM). Subsequent: 3–5 min with ccache."
echo ""

mkdir -p "$STAGE_DIR/dist"

podman run --rm \
    --platform linux/amd64 \
    -v "${STAGE_DIR}:/workspace:z" \
    -v "${CCACHE_VOLUME}:/ccache:z" \
    --env "CCACHE_DIR=/ccache" \
    "$IMAGE_NAME"

# ── Copy binary back to original source directory ─────────────────────────────
mkdir -p "$CPTDM_DIR/dist"
if [ -f "$STAGE_DIR/dist/cptdm" ]; then
    cp "$STAGE_DIR/dist/cptdm" "$CPTDM_DIR/dist/cptdm"
    [ -f "$STAGE_DIR/dist/cptdm.linux.sha256" ] \
        && cp "$STAGE_DIR/dist/cptdm.linux.sha256" "$CPTDM_DIR/dist/"
    info "Binary copied back to $CPTDM_DIR/dist/"
fi

# ── Report ────────────────────────────────────────────────────────────────────
echo ""
if [ -f "$CPTDM_DIR/dist/cptdm" ]; then
    BINARY_SIZE=$(du -sh "$CPTDM_DIR/dist/cptdm" | cut -f1)
    success "══════════════════════════════════════════════════════════"
    success "  Linux x86_64 binary ready!"
    success "  Binary  : $CPTDM_DIR/dist/cptdm  ($BINARY_SIZE)"
    [ -f "$CPTDM_DIR/dist/cptdm.linux.sha256" ] \
        && success "  SHA-256 : $(cut -d' ' -f1 $CPTDM_DIR/dist/cptdm.linux.sha256)"
    success "══════════════════════════════════════════════════════════"
else
    error "Binary not found after build. Check container output above."
fi
