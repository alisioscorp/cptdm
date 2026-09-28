<!--
README.md — User-facing documentation: installation, credentials, usage examples, dataset catalogue.
-----------------------------------------------------------------------------
Copyright (C) 2026 Alisios Corporation — https://alisioscorporation.com
Author       : Alisios Corporation
Project lead : Ángel G. Muñoz (Alisios) — angel.g.munoz@alisioscorporation.com
SEI lead     : Simon J. Mason — simon.mason@sei.org
UKMO lead    : Nicholas Savage — nicholas.savage@metoffice.gov.uk
Funding      : WISER Programme, UK International Development, Met Office UK
Repository   : https://github.com/alisioscorp/cptdm
Contract     : SEI/25-173

Licensed under the Alisios Open Non-Commercial License (AONCL) v1.0.
Free for non-commercial research, education, and public-sector use.
Commercial use requires prior written authorisation from Alisios Corporation
and SEI per contract SEI/25-173. Unauthorised commercial use will be subject
to legal action. See LICENSE for full terms.
Attribution to Alisios Corporation must be preserved in all copies,
modifications, forks, or derivative works (contract SEI/25-173).
-->
# CPT-DM — Climate Predictability Tool, Data Acquisition and Download Module

**Version:** 1.0.4  
**Produced by:** Alisios Corporation (https://alisioscorporation.com)  
**Project lead:** Ángel G. Muñoz — angel.g.munoz@alisioscorporation.com  
**SEI lead:** Simon J. Mason — simon.mason@sei.org  
**UKMO lead:** Nicholas Savage — nicholas.savage@metoffice.gov.uk  
**Contract:** SEI/25-173 | This software has been part-funded by the Weather and Climate Information Services (WISER) Programme, which is funded with UK International Development from the UK government and led by the Met Office in the UK.

---

## Overview

CPT-DM downloads and prepares climate datasets for direct use in CPT (Climate Predictability Tool). It produces CPT-formatted TSV files for hindcasts, forecasts, and observations covering precipitation, temperature, rainfall frequency, SST, and ENSO indices — at both seasonal and sub-seasonal timescales.

---

## Distribution

CPT-DM is distributed as three files. Place them all in the **same directory**:

| File | Purpose |
|---|---|
| `cptdm-macos-arm64` / `cptdm-linux-x86_64` / `cptdm-windows-x64.exe` | Self-contained binary — no Python required |
| `datasets.yml` | Dataset registry — editable without rebuilding |
| `README.md` | This file |

| Platform | Binary name | Requires |
|---|---|---|
| macOS (Apple Silicon) | `cptdm-macos-arm64` | macOS 11.0+ |
| Linux x86_64 | `cptdm-linux-x86_64` | Ubuntu 20.04+, RHEL 8+ |
| Windows x64 | `cptdm-windows-x64.exe` | Windows 10/11 |

```bash
# macOS
chmod +x cptdm-macos-arm64
./cptdm-macos-arm64 --version

# Linux
chmod +x cptdm-linux-x86_64
./cptdm-linux-x86_64 --version

# Windows PowerShell
.\cptdm-windows-x64.exe --version

# Windows CMD
cptdm-windows-x64.exe --version
```

---

## API credentials

Different data sources require different credentials. Set them up **once** and CPT-DM will use them automatically.

### CDS (Copernicus Climate Data Store)

**Required for:** All `cds_*` seasonal datasets and ERA5-Land reanalysis.

1. Register at https://cds.climate.copernicus.eu
2. Go to your profile page and copy your API key.
3. Create the credentials file:

**macOS / Linux:**
```bash
cat > ~/.cdsapirc << 'EOF'
url: https://cds.climate.copernicus.eu/api
key: YOUR-API-KEY-HERE
EOF
```

**Windows (PowerShell):**
```powershell
@"
url: https://cds.climate.copernicus.eu/api
key: YOUR-API-KEY-HERE
"@ | Out-File -Encoding ascii "$env:USERPROFILE\.cdsapirc"
```

**Windows (CMD):**
```cmd
echo url: https://cds.climate.copernicus.eu/api > %USERPROFILE%\.cdsapirc
echo key: YOUR-API-KEY-HERE >> %USERPROFILE%\.cdsapirc
```

**Alternative (any OS):** Set environment variables instead of creating the file:
```
CDSAPI_URL=https://cds.climate.copernicus.eu/api
CDSAPI_KEY=YOUR-API-KEY-HERE
```

### S2S Database — dual access (ECDS + MARS)

**Required for:** All `s2sdb_*` sub-seasonal datasets (intraseasonal forecasts).

CPT-DM tries **ECDS** (the new ECMWF Climate Data Store) first, then falls
back to **MARS** (the legacy API). You only need one set of credentials, but
having both gives you maximum reliability. ECDS is recommended for new setups.

#### Option A: ECDS (recommended)

1. Register at https://ecds.ecmwf.int and accept the S2S data licence.
2. Go to your profile page and copy your personal access token (API key).
3. Create the credentials file:

**macOS / Linux:**
```bash
cat > ~/.cdsapirc << 'EOF'
url: https://ecds.ecmwf.int/api
key: YOUR-ECDS-API-KEY-HERE
EOF
```

**Windows (PowerShell):**
```powershell
@"
url: https://ecds.ecmwf.int/api
key: YOUR-ECDS-API-KEY-HERE
"@ | Out-File -Encoding ascii "$env:USERPROFILE\.cdsapirc"
```

**Alternative (any OS):** Set environment variables:
```
CDSAPI_URL=https://ecds.ecmwf.int/api
CDSAPI_KEY=YOUR-ECDS-API-KEY-HERE
```

> **Note:** If you already have a `~/.cdsapirc` pointing at Copernicus CDS
> (`cds.climate.copernicus.eu`), you cannot reuse it for ECDS — these are
> separate services with separate accounts and keys. The recommended approach
> for users with both accounts is to keep `~/.cdsapirc` for Copernicus CDS
> and create a separate `~/.ecdsapirc` for ECDS — CPT-DM checks for both.
> Alternatively, point `CDSAPI_URL` + `CDSAPI_KEY` env vars at whichever
> service you need for a given session.

> **Note:** S2S data access on ECDS requires manual approval by ECMWF data
> services.  After accepting the licence, submit a support ticket at
> https://support.ecmwf.int requesting S2S database access for your account.
> This is free for research but not automatic.

#### Option B: MARS (legacy, fallback)

1. Register at https://www.ecmwf.int (a free ECMWF account is sufficient).
2. Go to https://api.ecmwf.int/v1/key/ and note your key and email.
3. Create the credentials file:

**macOS / Linux:**
```bash
cat > ~/.ecmwfapirc << 'EOF'
{
    "url"   : "https://api.ecmwf.int/v1",
    "key"   : "YOUR-ECMWF-API-KEY-HERE",
    "email" : "your.email@example.com"
}
EOF
```

**Windows (PowerShell):**
```powershell
@"
{
    "url"   : "https://api.ecmwf.int/v1",
    "key"   : "YOUR-ECMWF-API-KEY-HERE",
    "email" : "your.email@example.com"
}
"@ | Out-File -Encoding ascii "$env:USERPROFILE\.ecmwfapirc"
```

**Windows (CMD):**
```cmd
(
echo {
echo     "url"   : "https://api.ecmwf.int/v1",
echo     "key"   : "YOUR-ECMWF-API-KEY-HERE",
echo     "email" : "your.email@example.com"
echo }
) > %USERPROFILE%\.ecmwfapirc
```

**Alternative (any OS):** Set environment variables:
```
ECMWF_API_URL=https://api.ecmwf.int/v1
ECMWF_API_KEY=YOUR-ECMWF-API-KEY-HERE
ECMWF_API_EMAIL=your.email@example.com
```

> **Note:** MARS also requires S2S data access approval — the same support
> ticket covers both paths.

### NMME, CHIRPS, CRU, ERSST, RONI — no credentials required

These datasets are publicly accessible via HTTP/FTP. No account needed.

### SubC (Subseasonal Consortium) — no credentials required

SubC real-time forecasts are publicly accessible at weather.ou.edu.

---

## Quick start

### Recommended climatology periods

| Source | Recommended `--clim` | Full available range |
|---|---|---|
| CDS seasonal (ECMWF, UKMO, MF, CMCC, DWD, JMA) | 1993–2016 (default) | ECMWF: 1981–2016; others: 1993–2016 |
| CDS seasonal (NCEP) | 1982–2010 (default) | 1982–2010 |
| CDS seasonal (ECCC) | 1981–2010 (default) | 1981–2010 |
| NMME (all models) | 1991–2020 (default) | varies; see datasets.yml |
| S2SDB (varies by centre) | see table above | see table above |
| Observations (CHIRPS, CRU, ERA5-Land) | 1991–2020 (default) | CHIRPS: 1981–present; CRU: 1901–2021; ERA5-Land: 1950–present |

When mixing CDS and NMME models, use **1993–2016** as the common period. Omitting `--clim` uses the recommended default for each dataset.

**Quick-test tip:** For a fast test run (seconds instead of minutes), use a short `--clim` period and a small bbox:
```bash
cptdm ... --clim 2014-2016 --bbox -5,36,2,42
```

### Seasonal examples

```bash
# ECMWF SEAS5 + CHIRPS3, Europe, Mar init, AMJ target season
# Uses default clim 1993-2016 (24 years)
cptdm \
  --timescale seasonal \
  --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 \
  --variable prcp \
  --init 2026-03 \
  --lead 1-3 \
  --bbox -10,30,15,55 \
  --outdir ./CPTFiles

# NMME CFSv2 + CHIRPS3, quick test (2 years, small bbox)
cptdm \
  --timescale seasonal \
  --dataset nmme_cfsv2 \
  --obs-dataset chirps3 \
  --variable prcp \
  --init 2026-03 \
  --lead 1-3 \
  --bbox -10,34,5,45 \
  --clim 2015-2016 \
  --outdir ./CPTFiles

# Dry-run (shows request without downloading)
cptdm --timescale seasonal --dataset cds_ecmwf_seasonal \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55 --dry-run
```

### Intraseasonal examples

```bash
# SubC ensemble mean, weeks 3+4 precipitation, multi-init 2024–2025
# Quick test — only 2 years, downloads ~25 MB in ~8 seconds
cptdm \
  --timescale intraseasonal \
  --dataset subc_emean \
  --variable prcp \
  --init 2024/2025-03-15 \
  --lead week34 \
  --bbox -10,34,5,45 \
  --outdir ./CPTFiles

# S2S ECMWF, weeks 3+4, multi-init batch 2006–2025
# Requires .ecmwfapirc — downloads reforecasts for each year
cptdm \
  --timescale intraseasonal \
  --dataset s2sdb_ecmwf \
  --variable prcp \
  --init 2006/2025-03-09 \
  --lead week34 \
  --bbox -10,30,15,55 \
  --outdir ./CPTFiles

# Day-range leads also work (equivalent to week34)
cptdm \
  --timescale intraseasonal \
  --dataset subc_emean \
  --variable prcp \
  --init 2024/2025-03-15 \
  --lead 15-28 \
  --bbox -10,34,5,45

# Single init (no multi-init loop)
cptdm \
  --timescale intraseasonal \
  --dataset s2sdb_ecmwf \
  --variable prcp \
  --init 2026-03-09 \
  --lead week1 \
  --bbox -10,30,15,55
```

---

## Command-line reference

```
Required:
  --timescale TEXT    seasonal | intraseasonal
  --dataset TEXT      Dataset ID (see catalogue below)
  --variable TEXT     prcp | tmean | tmax | tmin | rfreq | sst | enso_index
  --init TEXT         Seasonal: YYYY-MM
                      Intraseasonal: YYYY-MM-DD (single init)
                                     YYYY1/YYYY2-MM-DD (multi-init batch)
  --lead TEXT         Seasonal: 1 | 3 | 1-3 | MAM | DJF
                      Lead convention: lead 1 = initialisation month (CDS
                      convention), lead 2 = first month after init, etc.
                      For a March init, --lead 1-3 = MAM, --lead 4-6 = JJA.
                      Applies consistently to all datasets (models, obs,
                      reanalysis, RONI).
                      Intraseasonal: week1 | week2 | week3 | week4 | week34
                                     or day ranges: 15-28, 8-14, etc.
  --bbox TEXT         W,S,E,N in degrees

Obs/reanalysis (required for forecast models):
  --obs-dataset TEXT  chirps3 | cru409 | era5land

Optional:
  --aggreg TEXT       total | mean (auto-set by variable)
  --anomalies         Output anomalies instead of full field
  --wetdthresh FLOAT  Wet-day threshold mm/day for rfreq (default: 1.0)
  --clim TEXT         Climatology period YYYY-YYYY (default: from registry)
  --ensemble-stat     median | mean (default: median)
  --outdir PATH       Output directory (default: ./CPTFiles)
  --cache-dir PATH    Cache directory (default: ./CPTFiles/cache)
  --no-cache          Force re-download
  --registry PATH     Path to custom datasets.yml
  --output TEXT       Override output filename
  --manifest          Write manifest.json
  --dry-run           Show request without downloading
  --version           Show version
```

---

## Dataset catalogue

### Seasonal forecast models (CDS)

| ID | Model | Centre | Variables |
|---|---|---|---|
| `cds_ecmwf_seasonal` | SEAS5 | ECMWF | prcp, tmax, tmin, tmean, sst, rfreq |
| `cds_ncep_seasonal` | CFSv2 | NCEP/CDS | prcp, tmax, tmin, tmean, sst, rfreq |
| `cds_eccc_seasonal` | CanSIPSv2 | ECCC | prcp, tmax, tmin, tmean, sst, rfreq |
| `cds_ukmo_glosea605` | GloSea605 | UKMO | prcp, tmean, rfreq |
| `cds_meteo_france_system8` | System 8 | Météo-France | prcp, tmean, sst, rfreq |
| `cds_cmcc_sps35` | SPS3.5 | CMCC | prcp, tmean, sst, rfreq |
| `cds_dwd_gcfs21` | GCFS2.1 | DWD | prcp, tmean, sst, rfreq |
| `cds_jma_cps3` | CPS3 | JMA | prcp, tmean, sst, rfreq |

### Seasonal forecast models (NMME)

| ID | Model | Centre | Variables | Notes |
|---|---|---|---|---|
| `nmme_cfsv2` | CFSv2 | NOAA | prcp, tmean, sst, rfreq | |
| `nmme_cansipsv2` | CanESM5 | ECCC | prcp, tmean, sst | |
| `nmme_gfdl_spear` | GFDL-SPEAR | NOAA | prcp, tmean, sst | ⚠️ Not operational as of April 2026 — no longer present on the NOAA CPC NMME FTP. All downloads will return 404. |
| `nmme_nasa_geoss2s` | GEOS5v2 | NASA | prcp, tmean, sst | |
| `nmme_ncar_cesm1` | CCSM4 | NCAR | prcp, tmean, sst | |
| `nmme_gem_nemo` | GEM-NEMO | ECCC | prcp, tmean, sst | ⚠️ Not operational as of April 2026 — no longer present on the NOAA CPC NMME FTP. All downloads will return 404. |
| `nmme_gem52_nemo` | GEM5.2-NEMO | ECCC | prcp, tmean, sst | ⚠️ init 05–07 only |

### Sub-seasonal forecast models (S2S Database)

Requires ECDS (`~/.cdsapirc` with ECDS URL) or MARS (`~/.ecmwfapirc`) credentials. See "API credentials" section above.

| ID | Model | Centre | Reforecast period | Init frequency | Embargo |
|---|---|---|---|---|---|
| `s2sdb_ecmwf` | ECMWF extended | ECMWF | 1999–2019 | Mon/Thu | ~3 days |
| `s2sdb_ncep` | CFSv2 | NCEP | 1999–2010 | daily | ~3 days |
| `s2sdb_ukmo` | GloSea | UKMO | 1993–2016 | Mon/Thu | ~21 days |
| `s2sdb_eccc` | GEM-NEMO | ECCC | 2001–2020 | Mon/Thu | ~3 days |
| `s2sdb_jma` | JMA-CPS | JMA | 1981–2010 | weekly | ~3 days |
| `s2sdb_cma` | CMA-BCC | CMA | 1994–2014 | Mon/Thu | ~3 days |
| `s2sdb_bom` | ACCESS-S | BoM | 1981–2013 | Mon/Thu | ~3 days |
| `s2sdb_cnrm` | CNRM-CM | Météo-France | 1993–2014 | Mon/Thu | ~42 days |
| `s2sdb_isac` | ISAC-CNR | ISAC | 1981–2010 | weekly | ~3 days |
| `s2sdb_cptec` | BAM/CPTEC | INPE/CPTEC | 1999–2010 | weekly | ~3 days |
| `s2sdb_kma` | KMA-GloSea | KMA | 1991–2020 | Mon/Thu | ~3 days |
| `s2sdb_hmcr` | SL-AV | HMCR | 1985–2010 | Mon/Thu | ~3 days |

All S2SDB models provide prcp and tmean. Available lead windows: week1, week2, week3, week4, week34 (combined weeks 3+4), or day ranges like 15-28. The embargo column indicates the minimum age of init dates available from the ECMWF MARS archive; UKMO and CNRM have longer embargoes than most centres.

### Sub-seasonal forecast models (SubC)

No credentials required. Real-time forecasts from weather.ou.edu. New forecasts are typically issued weekly on **Thursdays**; CPT-DM automatically searches within a ±7 day window around the requested init date to find the nearest available forecast.

| ID | Model | Variables | Notes |
|---|---|---|---|
| `subc_emean` | SubC multi-model ensemble mean | prcp, tmean | Weekly since May 2023 |
| `subc_emax` | SubC multi-model ensemble maximum | prcp, tmean | Weekly since May 2023 |
| `subc_emin` | SubC multi-model ensemble minimum | prcp, tmean | Weekly since May 2023 |

### Observations / Reanalysis

| ID | Source | Variables | Timescale |
|---|---|---|---|
| `chirps3` | CHIRPS v3 (CHC) | prcp, rfreq | seasonal + intraseasonal |
| `cru409` | CRU TS4.09 | prcp, tmean | seasonal only |
| `era5land` | ERA5-Land (CDS) | tmean | seasonal + intraseasonal |

### Standalone predictors

| ID | Source | Variable | Notes |
|---|---|---|---|
| `ersst5` | NOAA ERSSTv5 | sst | Paired with any seasonal predictand |
| `roni` | NOAA CPC RONI | enso_index | Paired with any seasonal predictand |

> **Note:** Index/station datasets (`roni`, `enso_index`) have no spatial dimension.
> Omit the `--bbox` flag when downloading these datasets. Example:
>
> ```bash
> cptdm --timescale seasonal --dataset roni --variable enso_index \
>   --init 2026-03 --lead 1-1 --clim 1991-2020
>
> Note: --lead 1-1 selects the single RONI value for the init month (MAM
> for a March init). Use --lead 1-1 for RONI — it is already a 3-month
> running mean and does not need further aggregation.
> ```

---

## Validated test combinations

### A — Seasonal precipitation

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
```

### B — Seasonal temperature

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

# ECCC CanSIPSv2 forecast rfreq + CHIRPS3
cptdm --timescale seasonal --dataset cds_eccc_seasonal \
  --obs-dataset chirps3 --variable rfreq \
  --init 2026-03 --lead 1-3 --bbox -10,30,15,55 --wetdthresh 1.0
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
  --variable enso_index --init 2026-03 --lead 1-1 \
  --bbox 0,0,0,0
```

### F — Intraseasonal

```bash
# S2S ECMWF week1 precipitation
cptdm --timescale intraseasonal --dataset s2sdb_ecmwf \
  --obs-dataset chirps3 --variable prcp \
  --init 2026-03-09 --lead week1 --bbox -10,30,15,55

# SubC ensemble mean, week 3+4 temperature
cptdm --timescale intraseasonal --dataset subc_emean \
  --obs-dataset era5land --variable tmean \
  --init 2026-03-12 --lead week34 --bbox -10,30,15,55
```

---

## Notes

**CDS forecast availability:** Most CDS seasonal models publish real-time forecasts around the 13th of each month. Requesting a forecast before it is posted will produce hindcast + obs only, with a warning.

**CDS year filtering:** The CDS API downloads only the years specified by `--clim` — not the entire hindcast archive. A shorter `--clim` produces faster downloads.

**C3S rfreq:** Uses `seasonal-original-single-levels` daily product. Available from all C3S models (ECMWF, NCEP, ECCC, UKMO, Météo-France, CMCC, DWD, JMA). Not available for NMME models, which are monthly-only. Slower than monthly — allow 5–10 min per run.

**NMME hindcasts:** Year-by-year ensemble-mean files are downloaded from `ftp.cpc.ncep.noaa.gov/International/nmme/netcdf/`. Each year is a separate ~3 MB file, so `--clim 2015-2016` downloads only 2 files. The full default period (1991–2020) downloads 30 files (~90 MB total).

**CRU TS4.09:** Downloads ~200 MB on first use; cached for subsequent runs.

**NMME GEM5.2-NEMO:** Only init months 05–07 have data on the NOAA FTP. Other months fail with a clear warning.

**CHIRPS rfreq:** Only downloads daily files for the target season months. Default threshold: 1.0 mm/day (WMO).

**SubC init dates:** Forecasts are issued weekly (typically Thursdays) since May 2023. If your requested init date is not available, CPT-DM finds the closest available date within ±21 days. A warning is printed if the closest init is more than ~10 days away, but the download proceeds. Use `subc_emean` for the multi-model ensemble mean, `subc_emax` for the ensemble maximum, or `subc_emin` for the ensemble minimum. SubC hindcasts are not yet available in CPT-DM (pending IRIDL migration).

**S2S Database:** Requires an ECMWF account with S2S data access. CPT-DM tries ECDS (`~/.cdsapirc` with `url: https://ecds.ecmwf.int/api`) first, then falls back to MARS (`~/.ecmwfapirc`). Most centres publish forecasts with a 48-hour delay (UKMO: 3 weeks; BoM/CNRM: 1 week). Reforecast periods vary widely by centre — check the table above. The longest common reforecast period across all 12 centres is approximately 1999–2010.

**Multi-init mode:** For intraseasonal data, use `--init YYYY1/YYYY2-MM-DD` to generate a multi-init (lags) CPT TSV file covering multiple years. Each year's forecast is downloaded separately and bundled into one file. Failed years are skipped with a warning.

**Windows ANSI colours:** CPT-DM automatically detects whether your terminal supports ANSI escape codes. On older Windows CMD, colours are disabled. Set `NO_COLOR=1` to disable colours on any platform.

---

## Licence

Proprietary — Alisios Corporation. Developed under contract SEI/25-173 with the Stockholm Environment Institute. This software has been part-funded by the Weather and Climate Information Services (WISER) Programme, which is funded with UK International Development from the UK government and led by the Met Office in the UK. Redistribution outside the project team requires written authorisation from Alisios Corporation.

**Powered by Alisios Corporation** — https://alisioscorporation.com
