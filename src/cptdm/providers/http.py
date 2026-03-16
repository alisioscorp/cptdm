"""
cptdm.providers.http
~~~~~~~~~~~~~~~~~~~~
Provider for HTTP/HTTPS data sources:
  - CHIRPS v3     (CHC UCSB)              — observations, prcp
  - CRU TS4.09    (UEA)                   — observations, prcp + temp
  - NMME          (NOAA FTP mirror)       — forecasts/hindcasts, prcp + tmean
  - ERSST v5      (NOAA PSL)              — observations, sst
  - RONI          (NOAA CPC)              — index

All downloads stream with resume support and clear error messages.
No eval(), no shell calls.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import requests
import xarray as xr

from cptdm.registry.loader import DatasetEntry
from cptdm.request import Request

log = logging.getLogger(__name__)
_CHUNK = 1024 * 1024   # 1 MB streaming chunks


class HTTPProvider:
    def __init__(self, entry: DatasetEntry) -> None:
        self.entry = entry

    def fetch(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        label = self.entry.label.lower()
        if "chirps" in label:
            return self._fetch_chirps(request, cache_dir)
        elif "cru" in label:
            return self._fetch_cru(request, cache_dir)
        elif ("nmme" in label or "cfsv2" in label or "cansips" in label
              or "gfdl" in label or "nasa" in label or "ncar" in label
              or "gem" in label or "cancm" in label or "cesm" in label
              or (self.entry.http_params or {}).get("model_id")):
            return self._fetch_nmme(request, cache_dir)
        elif "ersst" in label:
            return self._fetch_ersst(request, cache_dir)
        elif "roni" in label or "enso" in label:
            return self._fetch_roni(request, cache_dir)
        else:
            raise NotImplementedError(
                f"HTTPProvider: no sub-fetcher for dataset '{self.entry.label}'."
            )

    # ------------------------------------------------------------------
    # CHIRPS v3
    # ------------------------------------------------------------------

    def _fetch_chirps(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        import numpy as np
        p = self.entry.http_params

        clim_start = request.clim_start or self.entry.clim_years[0]
        clim_end   = request.clim_end   or self.entry.clim_years[1]

        if request.variable == "rfreq":
            # Rainfall frequency: count wet days (precip > threshold) from daily files
            var_cfg   = self.entry.get_variable_params("rfreq")
            # CLI --wetdthresh overrides the dataset default; both default to WMO 1.0 mm/day
            threshold = float(getattr(request, "wet_day_threshold", None)
                              or var_cfg.get("wet_day_threshold", 1.0))
            daily_url = p["daily_url"]
            pattern_d = p["file_pattern_daily"]

            # Derive the target calendar months from the lead spec so we only
            # download the months we actually need (e.g. AMJ → [4, 5, 6])
            if request.lead and request.timescale == "seasonal":
                target_months = [
                    (request.init.month - 1 + lm) % 12 + 1
                    for lm in range(request.lead.lead_start,
                                    request.lead.lead_end + 1)
                ]
            else:
                target_months = list(range(1, 13))  # fallback: all months

            log.info("CHIRPS v3: fetching daily rfreq %d–%d, months %s "
                     "(threshold %.1f mm/day)",
                     clim_start, clim_end, target_months, threshold)

            datasets = []
            for year in range(clim_start, clim_end + 1):
                for month in target_months:
                    fname = (pattern_d
                             .replace("{YYYY}", str(year))
                             .replace("{MM}", f"{month:02d}"))
                    local = cache_dir / "chirps_daily" / fname
                    if not local.exists() or request.no_cache:
                        _download_file(daily_url + fname, local)
                    else:
                        log.debug("  cached: %s", fname)
                    ds = xr.open_dataset(local)
                    ds = _normalise_coords(ds)
                    ds = _subset_bbox(ds, request.bbox)
                    # Count days where precip > threshold
                    wet = (ds["precip"] > threshold).sum(dim="time", skipna=True)
                    # Assign a monthly timestamp
                    import pandas as pd
                    ts = pd.Timestamp(year=year, month=month, day=1)
                    wet = wet.expand_dims({"time": [np.datetime64(ts)]})
                    datasets.append(wet.to_dataset(name="rfreq"))

            merged = xr.concat(datasets, dim="time")
            log.info("CHIRPS v3 rfreq: %d months loaded (%d–%d)",
                     len(datasets), clim_start, clim_end)
            return {"obs": merged}

        else:
            # Standard prcp: monthly totals from monthly files
            base_url    = p["monthly_url"]
            pattern     = p["file_pattern_monthly"]
            var_cfg     = self.entry.get_variable_params("prcp")
            nc_var      = var_cfg.get("nc_var", "precip")
            fill_thresh = float(var_cfg.get("fill_value_threshold", 0))
            log.info("CHIRPS v3: fetching %d–%d", clim_start, clim_end)

            datasets = []
            for year in range(clim_start, clim_end + 1):
                fname = pattern.replace("{YYYY}", str(year))
                local = cache_dir / "chirps" / fname
                if not local.exists() or request.no_cache:
                    _download_file(base_url + fname, local)
                else:
                    log.info("  cached: %s", fname)
                ds = xr.open_dataset(local)
                ds = _normalise_coords(ds)
                ds = _subset_bbox(ds, request.bbox)
                datasets.append(ds)

            merged = xr.concat(datasets, dim="time")
            for v in merged.data_vars:
                merged[v] = merged[v].where(merged[v] >= fill_thresh)
            return {"obs": merged}

    # ------------------------------------------------------------------
    # CRU TS4.09
    # ------------------------------------------------------------------

    def _fetch_cru(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        import gzip

        p = self.entry.http_params
        base_url = p["base_url"]
        var_cfg  = self.entry.get_variable_params(request.variable)
        filename = var_cfg.get("filename")
        if not filename:
            raise ValueError(f"CRU: no filename for variable '{request.variable}' in datasets.yml")

        # filename may include a subdir prefix (e.g. "pre/cru_ts4.09...gz")
        # Use only the basename for the local cache path
        fname_local = Path(filename).name
        local_gz = cache_dir / "cru" / fname_local
        local_nc = local_gz.with_suffix("")

        if not local_nc.exists() or request.no_cache:
            if not local_gz.exists() or request.no_cache:
                _download_file(base_url + filename, local_gz)
            log.info("CRU: decompressing %s", local_gz.name)
            local_nc.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(local_gz, "rb") as fi, local_nc.open("wb") as fo:
                shutil.copyfileobj(fi, fo)
        else:
            log.info("CRU: cached %s", local_nc.name)

        # decode_timedelta=False prevents xarray from misinterpreting variables
        # with units like "days" (e.g. CRU 'wet') as timedelta64 instead of float.
        ds = xr.open_dataset(local_nc, decode_timedelta=False)
        ds = _normalise_coords(ds)
        ds = ds.sortby("time")   # guard against non-monotonic time after decode

        # CRU wet: verify time coord is datetime64; if it came out as timedelta,
        # re-decode it from the raw integer values using the file's time units.
        if "time" in ds.coords:
            t0 = ds["time"].values[0]
            if hasattr(t0, "dtype") and "timedelta" in str(t0.dtype):
                import pandas as pd
                # Re-open with decode_times=True to get the real datetime index
                ds_ref = xr.open_dataset(local_nc, decode_times=True,
                                         decode_timedelta=False)
                ds = ds.assign_coords(time=ds_ref["time"].values)
                ds = ds.sortby("time")
        ds = _subset_bbox(ds, request.bbox)

        clim_start = request.clim_start or self.entry.clim_years[0]
        clim_end   = request.clim_end   or self.entry.clim_years[1]
        ds = ds.sel(time=slice(f"{clim_start}-01", f"{clim_end}-12"))
        return {"obs": ds}

    # ------------------------------------------------------------------
    # NMME (NOAA FTP)
    # ------------------------------------------------------------------
    #
    # File structure (verified from ncdump 2026-03-15):
    #
    #   Clim:     ftp.cpc.ncep.noaa.gov/NMME/clim/{ModelID}.{cpc_var}.{MM}.mon.clim.nc
    #             data var: clim(target, lat, lon)  units="none"
    #             — pre-computed climatological mean, no ensemble, no year axis
    #
    #   Forecast: ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/{YYYYMM}0800/
    #                {ModelID}.{cpc_var}.{YYYYMM}.anom.nc
    #             data var: fcst(ensmem, target, lat, lon)  units="mm/s"
    #
    #   target dim: float, units="months since 1960-01-01 00:00:00" — decode manually
    #   ensmem dim: integer member index
    # ------------------------------------------------------------------

    def _fetch_nmme(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        import numpy as np

        p        = self.entry.http_params
        base_url = p["base_url"].rstrip("/")   # https://ftp.cpc.ncep.noaa.gov/NMME
        model_id = p["model_id"]
        cpc_var  = {"prcp": "prate", "tmean": "tmp2m"}.get(
            request.variable,
            self.entry.get_variable_params(request.variable).get("nc_var", "prate"),
        )
        nc_var   = self.entry.get_variable_params(request.variable).get("nc_var", "prec")

        init_month = f"{request.init.month:02d}"
        init_year  = request.init.year
        init_m     = request.init.month

        def _target_to_lead_offsets(ds):
            """Convert target coord (months-since-1960 floats) to 1-based integer lead offsets."""
            offsets = []
            for v in ds["target"].values:
                n = int(round(float(v)))
                cal_month = n % 12 + 1
                offsets.append(((cal_month - init_m) % 12) + 1)
            return offsets

        # ── clim (hindcast mean) ──────────────────────────────────────
        clim_fname = f"{model_id}.{cpc_var}.{init_month}.mon.clim.nc"
        clim_url   = f"{base_url}/clim/{clim_fname}"
        clim_local = cache_dir / "nmme" / "clim" / clim_fname

        if not clim_local.exists() or request.no_cache:
            _download_file(clim_url, clim_local)
        else:
            log.info("  NMME clim cached: %s", clim_fname)

        hcast_ds = xr.open_dataset(clim_local, decode_times=False)

        # Rename data var 'clim' to the standard nc_var name expected downstream
        if "clim" in hcast_ds.data_vars:
            hcast_ds = hcast_ds.rename({"clim": nc_var})

        # Convert target dim to integer lead offsets, rename to leadtime_month
        hcast_ds = hcast_ds.assign_coords(
            target=("target", np.array(_target_to_lead_offsets(hcast_ds), dtype=int))
        )
        hcast_ds = hcast_ds.rename({"target": "leadtime_month"})

        # Select only the requested leads
        if request.lead is not None:
            wanted    = list(range(request.lead.lead_start, request.lead.lead_end + 1))
            available = hcast_ds.leadtime_month.values.tolist()
            select    = [lm for lm in wanted if lm in available]
            if select:
                hcast_ds = hcast_ds.sel(leadtime_month=select)

        hcast_ds = _normalise_coords(hcast_ds)
        hcast_ds = _subset_bbox(hcast_ds, request.bbox)

        # Mark as NMME clim so the writer routes it to _write_single_field
        hcast_ds.attrs["nmme_clim"] = "true"

        # ── forecast (realtime anomaly) ───────────────────────────────
        yyyymm      = f"{init_year}{init_month}"
        fcast_dir   = f"{yyyymm}0800"
        fcast_fname = f"{model_id}.{cpc_var}.{yyyymm}.anom.nc"
        fcast_url   = f"{base_url}/realtime_anom/{fcast_dir}/{fcast_fname}"
        fcast_local = cache_dir / "nmme" / "realtime_anom" / fcast_dir / fcast_fname

        result: dict[str, xr.Dataset] = {"hindcast": hcast_ds}

        if not fcast_local.exists() or request.no_cache:
            try:
                _download_file(fcast_url, fcast_local)
            except Exception as exc:
                log.warning(
                    "NMME realtime forecast not available (%s). "
                    "Producing hindcast only.", exc
                )
                return result
        else:
            log.info("  NMME fcast cached: %s", fcast_fname)

        fcast_ds = xr.open_dataset(fcast_local, decode_times=False)

        if "fcst" in fcast_ds.data_vars:
            fcast_ds = fcast_ds.rename({"fcst": nc_var})
        for ens in ("ensmem", "ensemble", "member"):
            if ens in fcast_ds.dims:
                fcast_ds = fcast_ds.rename({ens: "number"})
                break

        fcast_ds = fcast_ds.assign_coords(
            target=("target", np.array(_target_to_lead_offsets(fcast_ds), dtype=int))
        )
        fcast_ds = fcast_ds.rename({"target": "leadtime_month"})

        if request.lead is not None:
            wanted    = list(range(request.lead.lead_start, request.lead.lead_end + 1))
            available = fcast_ds.leadtime_month.values.tolist()
            select    = [lm for lm in wanted if lm in available]
            if select:
                fcast_ds = fcast_ds.sel(leadtime_month=select)

        fcast_ds = _normalise_coords(fcast_ds)
        fcast_ds = _subset_bbox(fcast_ds, request.bbox)
        result["forecast"] = fcast_ds
        return result


    # ------------------------------------------------------------------
    # ERSST v5
    # ------------------------------------------------------------------
    #
    # File: sst.mnmean.nc  (single multi-year file, ~30 MB)
    # Dims: time(months), lat(89), lon(180)  — 2°×2° grid
    # Var:  sst(time, lat, lon)  units="degC"  fill=-9.96921e+36
    # Some versions have a singleton 'lev' dim — squeezed out here.
    # ------------------------------------------------------------------

    def _fetch_ersst(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        import numpy as np

        p     = self.entry.http_params
        fname = p["file_pattern"]
        url   = p["base_url"].rstrip("/") + "/" + fname
        local = cache_dir / "ersst" / fname

        if not local.exists() or request.no_cache:
            _download_file(url, local)
        else:
            log.info("ERSST: cached %s", fname)

        ds = xr.open_dataset(local)

        # Clean up ERSST-specific artifacts — applied every time (cache hit or fresh).
        # Drop bounds variables and ALL non-spatial non-time dims/coords.
        # (ERSST has time_bnds with a nbnds dim, and sometimes a singleton lev dim.)
        drop_vars = [v for v in list(ds.data_vars) + list(ds.coords)
                     if any(s in str(v).lower() for s in ("bnd", "bound", "nbnds"))]
        if drop_vars:
            ds = ds.drop_vars(drop_vars, errors="ignore")
        for bad_dim in ("nbnds", "nbnd", "bnds"):
            if bad_dim in ds.dims:
                ds = ds.drop_dims(bad_dim)
        # Squeeze out remaining singleton non-spatial dims (e.g. lev=1)
        for dim in list(ds.dims):
            if dim not in ("time", "lat", "lon", "latitude", "longitude") and ds.sizes[dim] == 1:
                ds = ds.squeeze(dim, drop=True)

        # ERSST is always used as SST predictor regardless of forecast variable.
        # Use sst variable config directly (not request.variable which may be prcp/tmean).
        try:
            var_cfg = self.entry.get_variable_params("sst")
        except KeyError:
            var_cfg = {}
        fill_val = float(var_cfg.get("fill_value", -9.96921e+36))
        nc_var   = var_cfg.get("nc_var", "sst")
        if nc_var in ds.data_vars:
            ds[nc_var] = ds[nc_var].where(ds[nc_var] > fill_val * 0.01)

        ds = _normalise_coords(ds)
        ds = _subset_bbox(ds, request.bbox)

        clim_start = request.clim_start or self.entry.clim_years[0]
        clim_end   = request.clim_end   or self.entry.clim_years[1]
        ds = ds.sel(time=slice(f"{clim_start}-01", f"{clim_end}-12"))

        log.info("ERSST: %d months loaded (%d–%d)", len(ds.time), clim_start, clim_end)
        return {"obs": ds}

    # ------------------------------------------------------------------
    # RONI (Relative ONI, NOAA CPC)
    # ------------------------------------------------------------------
    #
    # Plain-text file. Columns: year  month  value
    # Already dimensionless — no unit conversion needed.
    # Returns a 1-D time series Dataset with no lat/lon.
    # ------------------------------------------------------------------

    def _fetch_roni(self, request: Request, cache_dir: Path) -> dict[str, xr.Dataset]:
        import pandas as pd
        import numpy as np

        p        = self.entry.http_params
        # Verified URL 2026-03-15: https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt
        data_url = p.get("data_url",
            "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt")

        local = cache_dir / "roni" / "RONI.ascii.txt"
        if not local.exists() or request.no_cache:
            _download_file(data_url, local)
        else:
            log.info("RONI: cached")

        # RONI.ascii.txt format: VALUE SEASON YEAR
        # e.g.  0.34 MJJ 2019   (3-month running mean centred on middle month)
        # Convert season label to the middle month as a timestamp.
        SEASON_MID = {
            "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
            "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
        }

        rows = []
        with open(local) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    season = parts[0].upper()
                    year   = int(parts[1])
                    val    = float(parts[2])
                    month  = SEASON_MID.get(season)
                    if month is None:
                        continue
                    rows.append((year, month, val))
                except (ValueError, IndexError):
                    continue

        if not rows:
            raise ValueError("RONI: could not parse any data rows from " + str(local))

        years, months, vals = zip(*rows)
        times = pd.DatetimeIndex([
            pd.Timestamp(year=y, month=m, day=1)
            for y, m in zip(years, months)
        ])
        ds = xr.Dataset(
            {"enso_index": ("time", np.array(vals, dtype=float))},
            coords={"time": times.values},
        )
        if request.clim_start and request.clim_end:
            ds = ds.sel(time=slice(
                f"{request.clim_start}-01", f"{request.clim_end}-12"
            ))
        log.info("RONI: %d seasons loaded", len(ds.time))
        return {"obs": ds}


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: Path, timeout: int = 120) -> None:
    """
    Stream-download url → dest with resume support (.part file).
    Always shows an in-place progress bar — important for slow connections.
    """
    import sys

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    headers: dict[str, str] = {}
    resume_pos = 0
    if tmp.exists():
        resume_pos = tmp.stat().st_size
        headers["Range"] = f"bytes={resume_pos}-"

    log.info("  Downloading: %s", url)
    total = 0   # initialise before try so except blocks can reference it
    try:
        resp = requests.get(url, stream=True, timeout=timeout, headers=headers)

        if resp.status_code == 416:
            tmp.rename(dest)
            return

        resp.raise_for_status()

        mode    = "ab" if resume_pos and resp.status_code == 206 else "wb"
        total   = int(resp.headers.get("content-length", 0)) + resume_pos
        written = resume_pos
        last_pct = -1
        fname = dest.name

        with tmp.open(mode) as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
                    if total:
                        pct = int(written / total * 100)
                        if pct != last_pct:
                            # \r overwrites the line; works in TTY and most terminals
                            sys.stdout.write(
                                f"\r    {fname}  {pct:3d}%  "
                                f"({written/1e6:.1f} / {total/1e6:.1f} MB)   "
                            )
                            sys.stdout.flush()
                            last_pct = pct

        if total:
            # Final newline so the next log line starts clean
            sys.stdout.write(f"\r    {fname}  100%  ({total/1e6:.1f} MB) — done\n")
            sys.stdout.flush()

        tmp.rename(dest)
        log.info("  Saved: %s  (%.1f MB)", dest.name, dest.stat().st_size / 1e6)

    except requests.HTTPError as exc:
        if total:
            sys.stdout.write("\n")
        tmp.unlink(missing_ok=True)
        raise requests.HTTPError(
            f"Download failed [{exc.response.status_code}]: {url}"
        ) from exc
    except requests.ConnectionError as exc:
        if total:
            sys.stdout.write("\n")
        tmp.unlink(missing_ok=True)
        raise requests.ConnectionError(
            f"Connection failed: {url}\n"
            f"Check your internet connection and try again."
        ) from exc
    except Exception:
        try:
            sys.stdout.write("\n")
        except Exception:
            pass
        tmp.unlink(missing_ok=True)
        raise


def _normalise_coords(ds: xr.Dataset) -> xr.Dataset:
    """longitude/latitude → lon/lat, 0–360 → –180–180, sort N→S."""
    renames = {}
    for old, new in [("longitude", "lon"), ("latitude", "lat")]:
        if old in ds.coords:
            renames[old] = new
    if renames:
        ds = ds.rename(renames)
    if "lon" in ds.coords and float(ds.lon.max()) > 180:
        ds = ds.assign_coords(lon=((ds.lon + 180) % 360) - 180)
        ds = ds.sortby("lon")
    if "lat" in ds.coords and float(ds.lat.values[0]) < float(ds.lat.values[-1]):
        ds = ds.sortby("lat", ascending=False)
    return ds


def _subset_bbox(ds: xr.Dataset, bbox: tuple) -> xr.Dataset:
    lonW, latS, lonE, latN = bbox
    if "lat" in ds.coords:
        ds = ds.sel(lat=slice(latN, latS))
    if "lon" in ds.coords:
        ds = ds.sel(lon=slice(lonW, lonE))
    return ds
