"""Tests for tools.hackrf_tx.dummy_drone — link budget and CLI plumbing."""

import math

import pytest

from tools.hackrf_tx.dummy_drone import (
    B210_MAX_SAFE_INPUT_DBM,
    HACKRF_AMP_GAIN_DB,
    build_parser,
    estimate_b210_input_dbm,
    estimate_hackrf_output_dbm,
    fspl_db,
)


# ---------------------------------------------------------------------------
# FSPL
# ---------------------------------------------------------------------------

class TestFSPL:
    def test_fspl_at_one_meter_2_4_ghz(self):
        # FSPL @ 1m, 2.437 GHz ≈ 40.2 dB
        loss = fspl_db(1.0, 2.437e9)
        assert 39.5 < loss < 41.0

    def test_fspl_doubles_distance_adds_6db(self):
        loss_1m = fspl_db(1.0, 2.437e9)
        loss_2m = fspl_db(2.0, 2.437e9)
        assert math.isclose(loss_2m - loss_1m, 6.02, abs_tol=0.05)

    def test_invalid_distance_raises(self):
        with pytest.raises(ValueError):
            fspl_db(0.0, 2.4e9)


# ---------------------------------------------------------------------------
# HackRF output power model
# ---------------------------------------------------------------------------

class TestHackRFOutput:
    def test_amp_off_at_low_gain_is_low(self):
        out = estimate_hackrf_output_dbm(0, amp_on=False)
        assert out <= -50.0

    def test_amp_off_at_max_gain_is_near_zero(self):
        out = estimate_hackrf_output_dbm(47, amp_on=False)
        assert -5.0 <= out <= 5.0

    def test_amp_adds_14db(self):
        a = estimate_hackrf_output_dbm(40, amp_on=False)
        b = estimate_hackrf_output_dbm(40, amp_on=True)
        assert math.isclose(b - a, HACKRF_AMP_GAIN_DB, abs_tol=0.01)

    def test_monotonic_increasing_in_gain(self):
        prev = -math.inf
        for g in range(0, 48, 4):
            cur = estimate_hackrf_output_dbm(g, amp_on=False)
            assert cur >= prev
            prev = cur

    def test_clamps_out_of_range_gain(self):
        # Negative or >47 should clamp, not raise.
        low = estimate_hackrf_output_dbm(-10, amp_on=False)
        high = estimate_hackrf_output_dbm(99, amp_on=False)
        assert low == estimate_hackrf_output_dbm(0, amp_on=False)
        assert high == estimate_hackrf_output_dbm(47, amp_on=False)


# ---------------------------------------------------------------------------
# B210 input budget
# ---------------------------------------------------------------------------

class TestB210Budget:
    def test_safe_at_low_gain_3m(self):
        tx = estimate_hackrf_output_dbm(20, amp_on=False)
        rx = estimate_b210_input_dbm(tx, 3.0, 2.437e9)
        assert rx < B210_MAX_SAFE_INPUT_DBM - 20  # well below limit

    def test_marginal_at_max_gain_amp_close(self):
        """vga=47 + amp at 1 m should be near or above the safe limit."""
        tx = estimate_hackrf_output_dbm(47, amp_on=True)
        rx = estimate_b210_input_dbm(tx, 1.0, 2.437e9)
        # Within ±10 dB of the limit — exact value depends on Yagi/BPF
        # constants and is what the banner warns about.
        assert rx > B210_MAX_SAFE_INPUT_DBM - 10

    def test_distance_5m_safer_than_1m(self):
        tx = estimate_hackrf_output_dbm(40, amp_on=False)
        rx_close = estimate_b210_input_dbm(tx, 1.0, 2.437e9)
        rx_far = estimate_b210_input_dbm(tx, 5.0, 2.437e9)
        # 5x distance ≈ 14 dB more loss
        assert rx_far < rx_close - 12


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

class TestCLIParser:
    def test_default_args_no_profile_no_amp(self):
        args = build_parser().parse_args([])
        assert args.profile is None
        assert args.allow_amp is False
        assert args.dry_run is False
        assert args.list_profiles is False

    def test_profile_arg(self):
        args = build_parser().parse_args(["--profile", "tone_2437"])
        assert args.profile == "tone_2437"

    def test_gain_int(self):
        args = build_parser().parse_args(["--gain", "30"])
        assert args.gain == 30

    def test_dry_run_flag(self):
        args = build_parser().parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_iq_path(self):
        args = build_parser().parse_args(["--iq", "/tmp/x.cf32"])
        assert str(args.iq) == "/tmp/x.cf32"
