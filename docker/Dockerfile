# =============================================================================
# CPT-DM — Nuitka Commercial Linux Build Environment
# =============================================================================
#
# Uses the official Python 3.13-slim image — no PPA, no gpg issues.
# Produces a Linux x86_64 binary compatible with Ubuntu 20.04+, Debian 11+,
# RHEL 8+, Amazon Linux 2023.
#
# USAGE (from cptdm/ directory):
#   bash docker/build-linux.sh
#
# OUTPUT: dist/cptdm  (Linux x86_64 binary, source-protected)
# =============================================================================

FROM python:3.13-slim-bookworm

LABEL maintainer="Alisios Corporation <info@alisioscorporation.com>"
LABEL description="CPT-DM Nuitka Commercial build environment (Linux x86_64)"

ENV DEBIAN_FRONTEND=noninteractive

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    patchelf \
    ccache \
    libeccodes-dev \
    libnetcdf-dev \
    libhdf5-dev \
    zlib1g-dev \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── pip upgrade ───────────────────────────────────────────────────────────────
RUN pip install --upgrade pip setuptools wheel

# ── Python runtime dependencies (separate layer — only rebuilds if changed) ───
RUN pip install \
    "cdsapi>=0.7" \
    "xarray>=2022.6" \
    "numpy>=1.22" \
    "pandas>=1.4" \
    "netCDF4>=1.6" \
    "cfgrib>=0.9" \
    "pydantic>=2.0" \
    "pyyaml>=6.0" \
    "requests>=2.28" \
    "aiohttp>=3.8" \
    "rich>=12" \
    "click>=8.0"

# ── Nuitka requirements ───────────────────────────────────────────────────────
RUN pip install \
    zstandard \
    pycryptodomex \
    ordered-set \
    Jinja2 \
    appdirs \
    tqdm

# ── Nuitka Commercial ─────────────────────────────────────────────────────────
# build-linux.sh copies the zip into docker/ before docker build runs
COPY Nuitka-commercial-main.zip /tmp/Nuitka-commercial-main.zip
RUN pip install /tmp/Nuitka-commercial-main.zip --force-reinstall \
    && rm /tmp/Nuitka-commercial-main.zip

# Verify commercial plugins present
RUN python -c "import nuitka.plugins.commercial; print('Nuitka commercial OK')"
RUN python -m nuitka --version

# ── ccache ────────────────────────────────────────────────────────────────────
ENV CCACHE_DIR=/ccache
ENV PATH="/usr/lib/ccache:$PATH"

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY docker-build.sh /usr/local/bin/docker-build.sh
RUN chmod +x /usr/local/bin/docker-build.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/docker-build.sh"]
