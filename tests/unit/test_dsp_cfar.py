"""Tests for src.dsp.cfar."""

import numpy as np
import pytest

from src.dsp import cfar


class TestBuildKernel:
    def test_kernel_sums_to_one(self):
        k = cfar.build_kernel(guard_cells=2, reference_cells=4)
        assert k.sum() == pytest.approx(1.0)

    def test_kernel_zeros_at_cut_and_guards(self):
        k = cfar.build_kernel(guard_cells=2, reference_cells=4)
        center = (k.size - 1) // 2
        assert k[center] == 0.0
        assert k[center - 1] == 0.0
        assert k[center - 2] == 0.0
        assert k[center + 1] == 0.0
        assert k[center + 2] == 0.0

    def test_rejects_bad_args(self):
        with pytest.raises(ValueError):
            cfar.build_kernel(guard_cells=-1, reference_cells=4)
        with pytest.raises(ValueError):
            cfar.build_kernel(guard_cells=2, reference_cells=0)


class TestApply:
    def test_flat_noise_no_detections(self):
        psd = np.full(64, -90.0)
        result = cfar.apply(
            psd,
            guard_cells=2,
            reference_cells=4,
            threshold_factor_db=10.0,
        )
        # Flat noise → no bins exceed threshold above local mean.
        assert not result.detection_mask.any()

    def test_narrowband_signal_detected(self):
        psd = np.full(64, -90.0)
        psd[32] = -50.0  # 40 dB pop
        result = cfar.apply(
            psd,
            guard_cells=2,
            reference_cells=8,
            threshold_factor_db=12.0,
        )
        assert result.detection_mask[32]
        # Adjacent bins (within guard window) shouldn't trigger.
        assert not result.detection_mask[33]

    def test_snr_is_psd_minus_noise_estimate(self):
        psd = np.full(64, -90.0)
        psd[32] = -60.0
        result = cfar.apply(
            psd,
            guard_cells=2,
            reference_cells=8,
            threshold_factor_db=10.0,
        )
        # SNR at the spike should be roughly 30 dB.
        assert result.snr_db[32] == pytest.approx(30.0, abs=1.0)
