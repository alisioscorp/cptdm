"""
Unit tests for cptdm.request — lead parsing, filename building, and request validation.
These tests are fast (no network, no files).
"""
from __future__ import annotations

import warnings
from datetime import date

import pytest

from cptdm.request import (
    Request,
    SeasonalLead,
    IntraseasonalLead,
    build_filename,
    parse_seasonal_lead,
    parse_intraseasonal_lead,
    seasonal_target_label,
)


# ---------------------------------------------------------------------------
# Seasonal lead parsing
# ---------------------------------------------------------------------------

class TestParseSeasonalLead:
    def test_single_digit(self):
        lead = parse_seasonal_lead("1", init_month=3)
        assert lead.lead_start == 1
        assert lead.lead_end == 1

    def test_range(self):
        lead = parse_seasonal_lead("1-3", init_month=3)
        assert lead.lead_start == 1
        assert lead.lead_end == 3
        assert lead.n_months == 3

    def test_season_amj_from_march(self):
        # init=March (3), lead 1-3 → AMJ
        lead = parse_seasonal_lead("AMJ", init_month=3)
        assert lead.lead_start == 1
        assert lead.lead_end == 3

    def test_season_djf_from_october(self):
        # init=October (10), DJF starts at offset 2 (Dec)
        lead = parse_seasonal_lead("DJF", init_month=10)
        assert lead.lead_start == 2
        assert lead.lead_end == 4

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_seasonal_lead("XYZ", init_month=1)

    def test_reversed_range_raises(self):
        with pytest.raises(ValueError):
            parse_seasonal_lead("3-1", init_month=1)


# ---------------------------------------------------------------------------
# Seasonal target label
# ---------------------------------------------------------------------------

class TestSeasonalTargetLabel:
    def test_amj_no_wrap(self):
        # init=March, lead 1-3 → AMJ, no year wrap
        lead = SeasonalLead(1, 3)
        label, offset = seasonal_target_label(3, lead)
        assert label == "AMJ"
        assert offset == 0

    def test_djf_wraps(self):
        # init=November, lead 1-3 → DJF, J wraps to next year
        lead = SeasonalLead(1, 3)
        label, offset = seasonal_target_label(11, lead)
        assert label == "DJF"
        assert offset == 1

    def test_ndj_wraps(self):
        # init=October, lead 1-3 → NDJ, J wraps
        lead = SeasonalLead(1, 3)
        label, offset = seasonal_target_label(10, lead)
        assert label == "NDJ"
        assert offset == 1


# ---------------------------------------------------------------------------
# Intraseasonal lead parsing
# ---------------------------------------------------------------------------

class TestParseIntraseasonalLead:
    def setup_method(self):
        self.init = date(2026, 3, 9)

    def test_week1_from_digit(self):
        lead = parse_intraseasonal_lead("1", self.init)
        assert lead.label == "week1"
        assert lead.tgt_start == date(2026, 3, 10)
        assert lead.tgt_end == date(2026, 3, 16)

    def test_week1_from_label(self):
        lead = parse_intraseasonal_lead("week1", self.init)
        assert lead.tgt_start == date(2026, 3, 10)

    def test_week34(self):
        lead = parse_intraseasonal_lead("week34", self.init)
        assert lead.label == "week34"
        assert lead.tgt_start == date(2026, 3, 24)
        assert lead.tgt_end == date(2026, 4, 6)

    def test_weeks_1_4_sentinel(self):
        lead = parse_intraseasonal_lead("1-4", self.init)
        assert lead.label == "weeks1-4"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_intraseasonal_lead("week5", self.init)

    def test_week_cross_month(self):
        # week34 from init=2026-03-09: tgt 15-28 days after = Mar 24 – Apr 6
        lead = parse_intraseasonal_lead("week34", date(2026, 3, 9))
        assert lead.tgt_end == date(2026, 4, 6)


# ---------------------------------------------------------------------------
# Filename building
# ---------------------------------------------------------------------------

class TestBuildFilename:
    def test_seasonal_fcast(self):
        fname = build_filename(
            "fcast", "CFSv2", "prcp",
            init=date(2026, 3, 1),
            timescale="seasonal",
            season_label="AMJ",
            fcast_year=2026,
            clim_start=1991, clim_end=2020,
        )
        # init 010326, tgt AMJ, year 26
        assert fname == "fcast_CFSv2_tp_init010326_tgtAMJ_26.tsv"

    def test_seasonal_hcast(self):
        fname = build_filename(
            "hcast", "CFSv2", "prcp",
            init=date(2026, 3, 1),
            timescale="seasonal",
            season_label="AMJ",
            clim_start=1991, clim_end=2020,
        )
        assert fname == "hcast_CFSv2_tp_init010326_tgtAMJ_91-20.tsv"

    def test_seasonal_obs(self):
        fname = build_filename(
            "obs", "CHIRPSv3", "prcp",
            init=date(2026, 3, 1),
            timescale="seasonal",
            season_label="AMJ",
            clim_start=1991, clim_end=2020,
        )
        assert fname == "obs_CHIRPSv3_tp_init010326_tgtAMJ_91-20.tsv"

    def test_intraseasonal_fcast(self):
        fname = build_filename(
            "fcast", "ecmwf", "tmean",
            init=date(2026, 3, 9),
            timescale="intraseasonal",
            tgt_start=date(2026, 3, 10),
            tgt_end=date(2026, 3, 16),
            clim_start=2006, clim_end=2025,
        )
        assert fname == "fcast_ecmwf_t2m_init090326_tgt100326-160326.tsv"

    def test_intraseasonal_hcast(self):
        fname = build_filename(
            "hcast", "ecmwf", "tmean",
            init=date(2026, 3, 9),
            timescale="intraseasonal",
            tgt_start=date(2026, 3, 10),
            tgt_end=date(2026, 3, 16),
            clim_start=2006, clim_end=2025,
        )
        assert fname == "hcast_ecmwf_t2m_init090326_tgt100326-160326_06-25.tsv"

    def test_intraseasonal_rean(self):
        fname = build_filename(
            "rean", "era5land", "tmean",
            init=date(2026, 3, 9),
            timescale="intraseasonal",
            tgt_start=date(2026, 3, 10),
            tgt_end=date(2026, 3, 16),
            clim_start=2006, clim_end=2025,
        )
        assert fname == "rean_era5land_t2m_init090326_tgt100326-160326_06-25.tsv"

    def test_override(self):
        fname = build_filename(
            "fcast", "ecmwf", "prcp",
            init=date(2026, 3, 9),
            timescale="intraseasonal",
            tgt_start=date(2026, 3, 10),
            tgt_end=date(2026, 3, 16),
            output_override="my_custom_file",
        )
        assert fname == "my_custom_file.tsv"

    def test_week34_cross_month(self):
        # From email: tgt240326-060426
        fname = build_filename(
            "fcast", "ecmwf", "tmean",
            init=date(2026, 3, 9),
            timescale="intraseasonal",
            tgt_start=date(2026, 3, 24),
            tgt_end=date(2026, 4, 6),
            clim_start=2006, clim_end=2025,
        )
        assert fname == "fcast_ecmwf_t2m_init090326_tgt240326-060426.tsv"


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

class TestRequest:
    def _make_seasonal_request(self, **kwargs):
        defaults = dict(
            timescale="seasonal",
            dataset_id="cds_ecmwf_seasonal",
            obs_dataset_id="chirps3",
            variable="prcp",
            init=date(2026, 3, 1),
            lead=SeasonalLead(1, 3),
            season_label="AMJ",
            season_year_offset=0,
            aggreg="total",
            bbox=(-10.0, 34.0, 5.0, 45.0),
            clim_start=1991,
            clim_end=2020,
            fcast_year=2026,
        )
        defaults.update(kwargs)
        return Request(**defaults)

    def test_basic_seasonal_request(self):
        req = self._make_seasonal_request()
        assert req.timescale == "seasonal"
        assert req.fcast_filename == "fcast_ecmwfseasonal_tp_init010326_tgtAMJ_26.tsv"
        assert req.hcast_filename == "hcast_ecmwfseasonal_tp_init010326_tgtAMJ_91-20.tsv"

    def test_total_non_precip_raises(self):
        with pytest.raises(ValueError, match="total.*only valid for precipitation"):
            self._make_seasonal_request(variable="tmean", aggreg="total")

    def test_mean_precip_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            self._make_seasonal_request(variable="prcp", aggreg="mean")
        assert any("mean" in str(x.message).lower() for x in w)

    def test_dry_run_summary_contains_key_fields(self):
        req = self._make_seasonal_request()
        summary = req.summary()
        assert "ecmwf" in summary.lower()
        assert "prcp" in summary
        assert "AMJ" in summary
