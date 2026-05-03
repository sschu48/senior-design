"""Tests for src.dsp.noise_floor."""

import numpy as np
import pytest

from src.dsp.noise_floor import ProtectedNoiseFloor, alpha_for_window_frames


class TestAlphaForWindowFrames:
    def test_alpha_decreases_with_window(self):
        assert alpha_for_window_frames(1) > alpha_for_window_frames(10)
        assert alpha_for_window_frames(10) > alpha_for_window_frames(100)

    def test_rejects_zero_window(self):
        with pytest.raises(ValueError):
            alpha_for_window_frames(0)


class TestProtectedNoiseFloor:
    def test_first_call_seeds_floor(self):
        nf = ProtectedNoiseFloor(num_bins=4, alpha=0.5, threshold_db=10.0)
        psd = np.array([-90.0, -88.0, -91.0, -89.0])
        floor = nf.update(psd)
        np.testing.assert_array_equal(floor, psd)

    def test_quiet_bins_track_psd(self):
        nf = ProtectedNoiseFloor(num_bins=4, alpha=0.5, threshold_db=10.0)
        nf.update(np.full(4, -90.0))
        # Drop psd by 4 dB; all bins still quiet (within 10 dB)
        floor = nf.update(np.full(4, -94.0))
        # 0.5 * -90 + 0.5 * -94 = -92
        np.testing.assert_allclose(floor, np.full(4, -92.0))

    def test_signal_bin_holds_floor(self):
        nf = ProtectedNoiseFloor(num_bins=4, alpha=0.5, threshold_db=10.0)
        nf.update(np.full(4, -90.0))
        # Bin 1 jumps 30 dB above floor — it must NOT update.
        psd = np.array([-90.0, -60.0, -90.0, -90.0])
        floor = nf.update(psd)
        # Bin 1's floor is unchanged at -90; others stay (already at -90)
        np.testing.assert_allclose(floor, np.array([-90.0, -90.0, -90.0, -90.0]))

    def test_persistent_signal_does_not_train_floor(self):
        nf = ProtectedNoiseFloor(num_bins=4, alpha=0.5, threshold_db=10.0)
        nf.update(np.full(4, -90.0))
        signal_psd = np.array([-90.0, -50.0, -90.0, -90.0])
        for _ in range(100):
            nf.update(signal_psd)
        # Bin 1 should still be at -90 — protected from contamination.
        assert nf.floor[1] == pytest.approx(-90.0)

    def test_reset_clears_state(self):
        nf = ProtectedNoiseFloor(num_bins=4, alpha=0.5, threshold_db=10.0)
        nf.update(np.full(4, -90.0))
        nf.reset()
        assert nf.floor is None

    def test_shape_mismatch_raises(self):
        nf = ProtectedNoiseFloor(num_bins=4, alpha=0.5, threshold_db=10.0)
        with pytest.raises(ValueError):
            nf.update(np.zeros(5))
