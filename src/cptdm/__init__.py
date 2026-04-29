# cptdm – CPT Data Acquisition and Download Module
# Copyright Alisios Corporation. All rights reserved.

__version__ = "1.0.3"
__author__ = "Ángel G. Muñoz"

# ---------------------------------------------------------------------------
# Global warning suppression — must run before any provider imports xarray.
# cfgrib: the ecCodes C library is not bundled in the Nuitka onefile binary;
#         CPT-DM uses netCDF4 for all downloads so cfgrib is never needed.
# numpy.ma: numpy ≥2.2 has a broken docstring assertion; we pin <2.2 at
#           build time but suppress just in case.
# ---------------------------------------------------------------------------
import warnings as _warnings
_warnings.filterwarnings("ignore", message=".*Engine.*cfgrib.*loading failed.*")
_warnings.filterwarnings("ignore", message=".*Failed to replace.*fromfunction.*")
