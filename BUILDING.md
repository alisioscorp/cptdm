# CPT-DM — Binary Build Guide (Internal — Alisios Corporation)

This document explains how to compile the CPT-DM binary for each platform.
**Keep this file private — do not include in deliveries to SEI/UKMO.**

---

## Overview

CPT-DM is compiled with **Nuitka Commercial 4.0.5** which:
- Produces a fully self-contained single-file executable
- Applies source-code protection (data-hiding plugin — always enabled)
- Requires no Python on the target machine

Three platform binaries are built and delivered:

| Binary | Build machine | Method |
|---|---|---|
| `cptdm-macos-arm64` | Witcher (macOS Apple Silicon) | Native conda env |
| `cptdm-linux-x86_64` | Witcher via Podman | Docker container, x86_64 emulated |
| `cptdm-windows-x64.exe` | Orinoco (Windows 11) | Native conda env |

---

## Prerequisites (all platforms)

- Nuitka Commercial zip: `~/Downloads/Nuitka-commercial-main.zip`
  (GitHub access: https://github.com/Nuitka/Nuitka-commercial)
- Source tarball: `cptdm_vX.Y.Z.tar.gz`
- C compiler (see per-platform notes below)

---

## 1. macOS binary (Witcher — native)

**Machine:** Witcher, Apple Silicon (arm64), macOS  
**Compiler:** Clang (Xcode Command Line Tools)  
**Environment:** conda `cptdm-build` (Python 3.13)

### One-time setup

```zsh
# Install Xcode tools if not present
xcode-select --install

# Create build environment
conda create -n cptdm-build python=3.13 -y
conda activate cptdm-build
pip install cdsapi xarray numpy pandas netCDF4 cfgrib pydantic pyyaml \
            requests aiohttp rich click
pip install zstandard pycryptodomex ordered-set Jinja2 appdirs tqdm
pip install ~/Downloads/Nuitka-commercial-main.zip --force-reinstall

# Pin compiler to clang (avoids Homebrew gcc confusion)
conda env config vars set CC=clang CXX=clang++ -n cptdm-build
```

### Build

```zsh
conda activate cptdm-build
cd /Volumes/Models/NextGen/CPT-DM
tar -xzf cptdm_vX.Y.Z.tar.gz
cd cptdm
pip install -e .

# Clear WRF env vars that leak from shell profile
unset CPPFLAGS CFLAGS LDFLAGS

bash build_binary.sh --clean --test
# Output: dist/cptdm  (~100 MB, macOS 11.0+ arm64)
```

### Subsequent builds (same version)

```zsh
conda activate cptdm-build
cd /Volumes/Models/NextGen/CPT-DM/cptdm
unset CPPFLAGS CFLAGS LDFLAGS
bash build_binary.sh --test   # no --clean, uses ccache → ~3 min
```

---

## 2. Linux x86_64 binary (Witcher via Podman)

**Machine:** Witcher (macOS)  
**Method:** Podman runs a Linux VM with x86_64 emulation (QEMU)  
**Output:** Genuine Linux x86_64 ELF binary

> ⚠️ **Warning — slow first build:** x86_64 emulation on Apple Silicon takes
> 2–3 hours for the first build. Subsequent builds use ccache (~10–15 min).
> Consider scheduling this overnight.

### One-time setup

```zsh
brew install podman
podman machine init --cpus 4 --memory 8192 --disk-size 40
podman machine start
```

### Build

```zsh
cd /Volumes/Models/NextGen/CPT-DM/cptdm
bash docker/build-linux-podman.sh
# Output: dist/cptdm  (Linux x86_64, ~100 MB)
```

### Force rebuild Docker image (after Nuitka update etc.)

```zsh
bash docker/build-linux-podman.sh --rebuild-image
```

### How it works internally

1. `build-linux-podman.sh` copies the Nuitka zip into `docker/` as build context
2. `podman build --platform linux/amd64` builds the image from `docker/Dockerfile`
   (base: `python:3.13-slim-bookworm`, installs all deps + Nuitka Commercial)
3. Source is staged to `~/.cptdm-podman-build/` (Podman VM can't see `/Volumes/`)
4. `podman run` mounts the staged source and runs `docker/docker-build.sh`
5. Binary is copied back from staging dir to `dist/cptdm`

### Podman management commands

```zsh
podman machine list          # check VM status
podman machine start         # start VM
podman machine stop          # stop VM
podman images                # list built images
podman rmi cptdm-builder-amd64  # remove image (force rebuild)
podman volume ls             # list volumes (ccache lives here)
podman volume rm cptdm-ccache-amd64  # clear ccache (full rebuild)
```

---

## 3. Windows x64 binary (Orinoco — native)

**Machine:** Orinoco (Dell OptiPlex 7060, Windows 11 Pro, Intel i5-8500T x64)  
**Compiler:** MSVC 14.3 (Visual Studio 2022 Build Tools)  
**Environment:** base conda env (Python 3.13) — no cptdm-build env created

### One-time setup (already done on Orinoco)

```powershell
# MSVC is at:
# C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\

# Nuitka Commercial installed in base conda env
# All deps installed in base conda env
```

### Build

```powershell
# 1. Extract source
cd C:\Users\Ori\Downloads
tar -xzf cptdm_vX.Y.Z.tar.gz -C C:\
cd C:\cptdm
pip install -e .

# 2. Initialise MSVC environment
$vsPath = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"
cmd /c "`"$vsPath\VC\Auxiliary\Build\vcvars64.bat`" && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
    }
}

# 3. Build
$VERSION = python -c "import sys; sys.path.insert(0,'src'); from cptdm import __version__; print(__version__)"

python -m nuitka `
    --standalone --onefile `
    --output-dir=dist --output-filename=cptdm `
    --enable-plugin=data-hiding `
    --python-flag=no_site --python-flag=isolated --python-flag=no_docstrings `
    --include-package=cptdm --include-package=cdsapi `
    --include-package=xarray --include-package=numpy --include-package=pandas `
    --include-package=netCDF4 --include-package=cfgrib --include-package=eccodes --include-package=cftime `
    --include-package=pydantic --include-package=pydantic_core `
    --include-package=yaml --include-package=requests --include-package=urllib3 `
    --include-package=certifi --include-package=charset_normalizer `
    --include-package=aiohttp --include-package=aiosignal `
    --include-package=frozenlist --include-package=multidict --include-package=yarl `
    --include-package=rich --include-package=click --include-package=zstandard `
    --include-data-files="config\datasets.yml=cptdm/data/datasets.yml" `
    --enable-plugin=anti-bloat `
    --noinclude-pytest-mode=nofollow --noinclude-setuptools-mode=nofollow `
    --msvc=latest `
    --company-name="Alisios Corporation" --product-name="CPT-DM" `
    --product-version="$VERSION" `
    --file-description="Climate Predictability Tool - Data Acquisition and Download Module" `
    --copyright="Copyright 2025 Alisios Corporation. All rights reserved." `
    --warn-implicit-exceptions `
    src\cptdm

# 4. Smoke test
dist\cptdm.exe --version
dist\cptdm.exe --timescale seasonal --dataset cds_ecmwf_seasonal `
    --obs-dataset chirps3 --variable prcp `
    --init 2026-03 --lead 1-3 --bbox -10,30,15,55 --dry-run
# Output: dist\cptdm.exe (~120-150 MB)
```

### Notes on Windows build

- **Do NOT use ziglang** — force MSVC with `--msvc=latest` to avoid 8.3 path errors
  and AV false positives
- **Entry point is `src\cptdm`** (package directory) — NOT `src\cptdm\__main__.py`
  (the latter triggers an 8.3 filename collision: `__MAIN~1.DIS`)
- **Source at `C:\cptdm`** — keep path short, deep paths cause MSVC linker failures

---

## GitHub delivery (private repo: github.com/alisioscorp/cptdm)

### Repo structure delivered to Simon & Nick

```
cptdm/
├── README.md               ← user instructions + test combinations
├── config/
│   └── datasets.yml        ← dataset registry (for source installs)
├── src/cptdm/              ← full Python source
├── tests/                  ← unit tests
└── releases/               ← pre-built binaries (via GitHub Releases)
    ├── cptdm-macos-arm64
    ├── cptdm-linux-x86_64
    └── cptdm-windows-x64.exe
```

### What is NOT in the repo

- `docker/` directory (internal build tooling)
- `BUILDING.md` (this file)
- `.venv/`, `dist/`, `CPTFiles/`
- `build_binary.sh`
- Any `.env` files

### Inviting Simon and Nick

1. Go to https://github.com/alisioscorp/cptdm/settings/access
2. Click "Invite a collaborator"
3. Add: `simon-mason-sei` (or their GitHub username — confirm with them)
4. Add: `nicholas-savage-ukmo` (confirm username)
5. Set role: **Read** (they only need to clone and download)

---

## Version history

| Version | Date | Key changes |
|---|---|---|
| 0.4.0 | 2026-03 | CRU rfreq, CHIRPS target-months-only |
| 0.4.1 | 2026-03 | `__main__.py` entry point added |
| 0.4.2 | 2026-03 | build scripts use `python` not `python3` |
| 0.4.3 | 2026-03 | `datasets.yml` path fixed for onefile binary |
| 0.4.4 | 2026-03 | Docker build environment added |
| 0.4.5 | 2026-03 | Dockerfile: python:3.13-slim-bookworm (no deadsnakes PPA) |
| 0.4.6 | 2026-03 | Docker: root user fix, memory limits for CF1 |
| 0.4.7 | 2026-03 | `--jobs=1` for CF1, deprecated numpy plugin removed |
| 0.4.8 | 2026-03 | Entry point → package dir (fixes Windows 8.3 path error) |
| 0.4.9 | 2026-03 | Podman x86_64 fix, README + BUILDING.md, GitHub delivery |
