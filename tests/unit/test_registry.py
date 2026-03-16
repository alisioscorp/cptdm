"""Unit tests for the dataset registry loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from cptdm.registry.loader import Registry

REGISTRY_PATH = Path(__file__).parent.parent.parent / "config" / "datasets.yml"


class TestRegistry:
    def test_loads_without_error(self):
        reg = Registry.load(REGISTRY_PATH)
        assert len(reg.all_ids()) > 0

    def test_all_expected_datasets_present(self):
        reg = Registry.load(REGISTRY_PATH)
        expected = [
            "cds_ecmwf_seasonal",
            "cds_ncep_seasonal",
            "cds_eccc_seasonal",
            "nmme_cfsv2",
            "chirps3",
            "era5land",
            "cru409",
            "ersst5",
            "roni",
            "s2sdb_ecmwf",
            "subc_esrl_fimr1p1",
        ]
        for ds_id in expected:
            assert ds_id in reg, f"Expected dataset '{ds_id}' not found in registry"

    def test_valid_pair_ecmwf_chirps_prcp(self):
        reg = Registry.load(REGISTRY_PATH)
        # Should not raise
        reg.validate_pair("cds_ecmwf_seasonal", "chirps3", "prcp")

    def test_valid_pair_ecmwf_era5land_tmean(self):
        reg = Registry.load(REGISTRY_PATH)
        reg.validate_pair("cds_ecmwf_seasonal", "era5land", "tmean")

    def test_invalid_pair_raises(self):
        reg = Registry.load(REGISTRY_PATH)
        with pytest.raises(ValueError, match="Invalid pairing"):
            reg.validate_pair("cds_ecmwf_seasonal", "era5land", "prcp")

    def test_by_timescale_seasonal(self):
        reg = Registry.load(REGISTRY_PATH)
        seasonal = reg.by_timescale("seasonal")
        assert "cds_ecmwf_seasonal" in seasonal
        assert "s2sdb_ecmwf" not in seasonal

    def test_by_timescale_intraseasonal(self):
        reg = Registry.load(REGISTRY_PATH)
        intra = reg.by_timescale("intraseasonal")
        assert "s2sdb_ecmwf" in intra
        assert "cds_ecmwf_seasonal" not in intra

    def test_unknown_dataset_raises(self):
        reg = Registry.load(REGISTRY_PATH)
        with pytest.raises(KeyError, match="not found in registry"):
            _ = reg["nonexistent_dataset"]

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            Registry.load(Path("/nonexistent/path/datasets.yml"))
