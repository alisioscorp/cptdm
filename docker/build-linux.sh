#!/usr/bin/env bash
# =============================================================================
# CPT-DM — Host wrapper for Docker Linux build
# Run from the cptdm/ source directory on macOS or Linux.
#
# USAGE:
#   bash docker/build-linux.sh                  # build image + compile
#   bash docker/build-linux.sh --rebuild-image  # force rebuild Docker image
#   bash docker/build-linux.sh --no-cache       # disable ccache
#
# REQUIREMENTS:
#   - Docker installed and running
#   - NUITKA_ZIP env var set, or zip at ~/Downloads/Nuitka-commercial-main.zip
#
# OUTPUT:
#   dist/cptdm              Linux x86_64 binary
#   dist/cptdm.linux.sha256 SHA-256 checksum
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${CYAN}[build-linux]${NC}  $*"; }
success() { echo -e "${GREEN}[build-linux]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[build-linux]${NC}  $*"; }
error()   { echo -e "${RED}[build-linux]${NC}  $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CPTDM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="cptdm-builder"
CCACHE_VOLUME="cptdm-ccache"

# ── Arguments ─────────────────────────────────────────────────────────────────
REBUILD_IMAGE=0
NO_CACHE=0
for arg in "$@"; do
    case "$arg" in
        --rebuild-image) REBUILD_IMAGE=1 ;;
        --no-cache)      NO_CACHE=1 ;;
        --help)
            grep "^#" "$0" | sed 's/^# \{0,2\}//' | head -20
            exit 0 ;;
    esac
done

# ── Find Nuitka zip ───────────────────────────────────────────────────────────
NUITKA_ZIP="${NUITKA_ZIP:-$HOME/Downloads/Nuitka-commercial-main.zip}"
[ -f "$NUITKA_ZIP" ] \
    || error "Nuitka zip not found at: $NUITKA_ZIP\nSet: export NUITKA_ZIP=/path/to/Nuitka-commercial-main.zip"

info "Nuitka zip : $NUITKA_ZIP"
info "Source dir : $CPTDM_DIR"

# ── Check Docker ──────────────────────────────────────────────────────────────
command -v docker &>/dev/null || error "Docker not found."
docker info &>/dev/null       || error "Docker daemon not running or permission denied. Try: sudo docker info"

# ── Copy Nuitka zip into docker/ build context (cleaned up on exit) ───────────
cp "$NUITKA_ZIP" "$SCRIPT_DIR/Nuitka-commercial-main.zip"
trap 'rm -f "$SCRIPT_DIR/Nuitka-commercial-main.zip"' EXIT

# ── Build Docker image ────────────────────────────────────────────────────────
IMAGE_EXISTS=$(docker images -q "$IMAGE_NAME" 2>/dev/null)

if [ -z "$IMAGE_EXISTS" ] || [ "$REBUILD_IMAGE" -eq 1 ]; then
    info "Building Docker image '$IMAGE_NAME'... (5–10 min first time)"
    docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
    success "Docker image built."
else
    info "Docker image '$IMAGE_NAME' exists. Use --rebuild-image to force rebuild."
fi

# ── Create persistent ccache volume ──────────────────────────────────────────
docker volume create "$CCACHE_VOLUME" &>/dev/null || true
info "ccache volume : $CCACHE_VOLUME (persists across builds)"

# ── Run build container ───────────────────────────────────────────────────────
echo ""
info "Starting Linux build container..."

# Run as root inside container (avoids /.local permission issues with pip).
# Output files in /workspace/dist are owned by root on Linux hosts —
# we fix ownership after the run using the host user's UID/GID.
docker run --rm \
    -v "${CPTDM_DIR}:/workspace" \
    -v "${CCACHE_VOLUME}:/ccache" \
    --env "CCACHE_DIR=/ccache" \
    ${NO_CACHE:+--env CCACHE_DISABLE=1} \
    --memory=1400m \
    --memory-swap=3400m \
    "$IMAGE_NAME"

# ── Fix output ownership on Linux (files are created as root inside container)
if [[ "$(uname -s)" == "Linux" ]]; then
    HOST_UID=$(id -u)
    HOST_GID=$(id -g)
    if [ "$HOST_UID" -ne 0 ]; then
        sudo chown -R "${HOST_UID}:${HOST_GID}" "${CPTDM_DIR}/dist" 2>/dev/null || true
    fi
fi

# ── Report ────────────────────────────────────────────────────────────────────
echo ""
if [ -f "$CPTDM_DIR/dist/cptdm" ]; then
    BINARY_SIZE=$(du -sh "$CPTDM_DIR/dist/cptdm" | cut -f1)
    success "Linux binary ready:"
    success "  $CPTDM_DIR/dist/cptdm  ($BINARY_SIZE)"
    [ -f "$CPTDM_DIR/dist/cptdm.linux.sha256" ] \
        && success "  SHA-256: $(cut -d' ' -f1 $CPTDM_DIR/dist/cptdm.linux.sha256)"
else
    error "Binary not found after build. Check container output above."
fi
