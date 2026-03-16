# CPT-DM — Climate Predictability Tool Data Manager

**Version:** 0.4.9  
**Produced by:** Alisios Corporation (https://alisioscorporation.com)  
**Project lead:** Ángel G. Muñoz — angel.g.munoz@alisioscorporation.com  
**SEI lead:** Simon J. Mason — simon.mason@sei.org  
**UKMO lead:** Nicholas Savage — nicholas.savage@metoffice.gov.uk  
**Contract:** SEI/25-173 | Funded by UK Met Office / WISER programme (UKRI / FCDO)

---

## Overview

CPT-DM downloads and prepares climate datasets for direct use in CPT (Climate Predictability Tool). It produces CPT-formatted TSV files for hindcasts, forecasts, and observations covering precipitation, temperature, rainfall frequency, SST, and ENSO indices.

---

## Installation

CPT-DM is distributed as a **self-contained binary** — no Python installation required.

| Platform | File | Requires |
|---|---|---|
| macOS (Apple Silicon) | `cptdm-macos-arm64` | macOS 11.0+ |
| Linux x86_64 | `cptdm-linux-x86_64` | Ubuntu 20.04+, RHEL 8+ |
| Windows x64 | `cptdm-windows-x64.exe` | Windows 10/11 |

```bash
# macOS / Linux
chmod +x cptdm-linux-x86_64
./cptdm-linux-x86_64 --version

# Windows PowerShell
.\cptdm-windows-x64.exe --version
```

---

## API credentials

### CDS (required for CDS and ERA5-Land datasets)

1. Register at https://cds.climate.copernicus.eu
2. Create `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: YOUR-API-KEY-HERE
```

### NMME — no credentials required (public FTP)

---

## Quick start

```bash
# ECMWF SEAS5 + CHIRPS3, Europe, Mar init, AMJ target season
cptdm \
  --timescale seasonal \
  --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 \
  --variable prcp \
  --init 2026-03 \
  --lead 1-3 \
  --bbox -10,30,15,55 \
  --outdir ./CPTFiles

# Dry-run (shows request without downloading)
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55 --dry-run
```

Output files in `./CPTFiles/`:
```
fcast_ecmwf_tp_init010326_tgtAMJ_26.tsv
hcast_ecmwf_tp_init010326_tgtAMJ_93-16.tsv
obs_CHIRPSv3_tp_init010326_tgtAMJ_93-16.tsv
```

---

## Command-line reference

```
Required:
  --timescale TEXT    seasonal
  --dataset TEXT      Dataset ID (see catalogue below)
  --variable TEXT     prcp | tmean | tmax | tmin | rfreq | sst | enso_index
  --init TEXT         Initialisation month YYYY-MM
  --lead TEXT         Lead range: 1-1 | 1-2 | 1-3
  --bbox TEXT         W,S,E,N in degrees

Obs/reanalysis (required for forecast models):
  --obs-dataset TEXT  chirps3 | cru409 | era5land

Optional:
  --aggreg TEXT       total | mean (auto-set by variable)
  --anomalies         Output anomalies instead of full field
  --wetdthresh FLOAT  Wet-day threshold mm/day for rfreq (default: 1.0)
  --clim-start INT    Climatology start year (default: 1993)
  --clim-end INT      Climatology end year (default: 2016)
  --outdir PATH       Output directory (default: ./CPTFiles)
  --dry-run           Show request without downloading
  --version           Show version
```

---

## Dataset catalogue

### Seasonal forecast models

| ID | Model | Centre | Variables |
|---|---|---|---|
| `cds_ecmwf_seasonal` | SEAS5 | ECMWF | prcp, tmax, tmin, tmean, rfreq |
| `cds_ncep_seasonal` | CFSv2 | NCEP/CDS | prcp, tmax, tmin, tmean |
| `cds_eccc_seasonal` | CanSIPSv2 | ECCC | prcp, tmax, tmin, tmean |
| `cds_ukmo_glosea6` | GloSea6 | UKMO | prcp, tmean |
| `cds_meteo_france_system8` | System 8 | Météo-France | prcp, tmean |
| `cds_cmcc_sps35` | SPS3.5 | CMCC | prcp, tmean |
| `cds_dwd_gcfs21` | GCFS2.1 | DWD | prcp, tmean |
| `cds_jma_cps3` | CPS3 | JMA | prcp, tmean |
| `nmme_cfsv2` | CFSv2 | NOAA/NMME | prcp, tmean, rfreq |
| `nmme_cansipsv2` | CanCM4i | ECCC/NMME | prcp, tmean |
| `nmme_gfdl_spear` | GFDL-SPEAR | NOAA/NMME | prcp, tmean |
| `nmme_nasa_geoss2s` | GEOS5v2 | NASA/NMME | prcp, tmean |
| `nmme_ncar_cesm1` | CCSM4 | NCAR/NMME | prcp, tmean |
| `nmme_gem_nemo` | GEM-NEMO | ECCC/NMME | prcp, tmean |
| `nmme_gem52_nemo` | GEM5.2-NEMO | ECCC/NMME | prcp, tmean ⚠️ init 05–07 only |

### Observations / Reanalysis

| ID | Source | Variables |
|---|---|---|
| `chirps3` | CHIRPS v3 | prcp, rfreq |
| `cru409` | CRU TS4.09 | prcp, tmean |
| `era5land` | ERA5-Land | tmean |

### Standalone predictors

| ID | Source | Variable |
|---|---|---|
| `ersst5` | NOAA ERSSTv5 | sst |
| `roni` | NOAA CPC RONI | enso_index |

---

## Validated test combinations

### A — Precipitation

```bash
# ECMWF SEAS5 + CHIRPS3
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55

# ECMWF SEAS5 + CRU
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset cru409 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55

# NCEP CFSv2 + CHIRPS3
cptdm --timescale seasonal --dataset cds_ncep_seasonal \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55

# NMME CFSv2 + CHIRPS3
cptdm --timescale seasonal --dataset nmme_cfsv2 \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55

# NMME GFDL-SPEAR + CHIRPS3
cptdm --timescale seasonal --dataset nmme_gfdl_spear \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55
```

### B — Temperature

```bash
# ECMWF SEAS5 + ERA5-Land
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset era5land --variable tmean \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55

# ECMWF SEAS5 + CRU
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset cru409 --variable tmean \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55

# NMME CFSv2 + ERA5-Land
cptdm --timescale seasonal --dataset nmme_cfsv2 \
  --obs-dataset era5land --variable tmean \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55
```

### C — Rainfall frequency (rfreq)

```bash
# ECMWF SEAS5 forecast rfreq + CHIRPS3
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 --variable rfreq \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55 --wetdthresh 1.0

# NMME CFSv2 (obs rfreq only — monthly model)
cptdm --timescale seasonal --dataset nmme_cfsv2 \
  --obs-dataset chirps3 --variable rfreq \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55
```

### D — SST predictor

```bash
cptdm --timescale seasonal --dataset ersst5 \
  --variable sst --init 2026-03 --lead 1-3 \
  --bbox -60,-30,30,30
```

### E — ENSO index predictor

```bash
cptdm --timescale seasonal --dataset roni \
  --variable enso_index --init 2026-03 --lead 1-3 \
  --bbox 0,0,0,0
```

### F — Lead season variants

```bash
# 1-month lead (single month)
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-1 --bbox -10,30,15,55

# 2-month lead
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-2 --bbox -10,30,15,55
```

---

## Notes

**ECMWF rfreq:** Uses `seasonal-original-single-levels` daily product. Slower than monthly — allow 5–10 min per run.

**CRU TS4.09:** Downloads ~200 MB on first use; cached for subsequent runs.

**NMME GEM5.2-NEMO:** Only init months 05–07 have climatology files on NOAA FTP. Other months fail with a clear warning.

**CHIRPS rfreq:** Only downloads daily files for the target season months. Default threshold: 1.0 mm/day (WMO).

---

## Licence

Proprietary — Alisios Corporation. Developed under contract SEI/25-173 with the Stockholm Environment Institute, funded by UK Met Office / WISER (UKRI/FCDO). Redistribution outside the project team requires written authorisation from Alisios Corporation.

**Powered by Alisios Corporation** — https://alisioscorporation.com
