"""Tests for src.dsp.spectrogram_buffer.SpectrogramBuffer."""

import numpy as np
import pytest

from src.dsp.spectrogram_buffer import SpectrogramBuffer


class TestSpectrogramBuffer:
    def test_starts_empty(self):
        buf = SpectrogramBuffer(num_bins=4, max_frames=8)
        assert buf.size == 0
        assert not buf.is_full
        assert buf.latest() is None
        ts, pwr = buf.snapshot()
        assert ts.size == 0
        assert pwr.shape == (0, 4)

    def test_append_and_latest(self):
        buf = SpectrogramBuffer(num_bins=3, max_frames=4)
        buf.append(0.0, np.array([-90.0, -80.0, -70.0]))
        ts, psd = buf.latest()
        assert ts == 0.0
        np.testing.assert_array_equal(psd, np.array([-90.0, -80.0, -70.0]))

    def test_snapshot_oldest_first(self):
        buf = SpectrogramBuffer(num_bins=2, max_frames=4)
        for i in range(3):
            buf.append(float(i), np.array([float(-100 + i), float(-90 + i)]))
        ts, pwr = buf.snapshot()
        assert ts.tolist() == [0.0, 1.0, 2.0]
        assert pwr.shape == (3, 2)
        assert pwr[0].tolist() == [-100.0, -90.0]
        assert pwr[2].tolist() == [-98.0, -88.0]

    def test_circular_eviction(self):
        buf = SpectrogramBuffer(num_bins=1, max_frames=3)
        for i in range(5):
            buf.append(float(i), np.array([float(i)]))
        assert buf.is_full
        assert buf.size == 3
        ts, pwr = buf.snapshot()
        assert ts.tolist() == [2.0, 3.0, 4.0]
        assert pwr.flatten().tolist() == [2.0, 3.0, 4.0]

    def test_latest_after_wrap(self):
        buf = SpectrogramBuffer(num_bins=1, max_frames=2)
        for i in range(5):
            buf.append(float(i), np.array([float(i)]))
        ts, psd = buf.latest()
        assert ts == 4.0
        assert psd[0] == 4.0

    def test_snapshot_is_a_copy(self):
        buf = SpectrogramBuffer(num_bins=1, max_frames=2)
        buf.append(0.0, np.array([-90.0]))
        ts, pwr = buf.snapshot()
        pwr[0, 0] = 0.0
        # Original buffer should not be mutated.
        ts2, pwr2 = buf.snapshot()
        assert pwr2[0, 0] == -90.0

    def test_reset_clears_state(self):
        buf = SpectrogramBuffer(num_bins=1, max_frames=2)
        buf.append(0.0, np.array([-90.0]))
        buf.reset()
        assert buf.size == 0
        assert buf.latest() is None

    def test_shape_mismatch_raises(self):
        buf = SpectrogramBuffer(num_bins=4, max_frames=2)
        with pytest.raises(ValueError):
            buf.append(0.0, np.zeros(3))

    def test_invalid_args_raise(self):
        with pytest.raises(ValueError):
            SpectrogramBuffer(num_bins=0, max_frames=4)
        with pytest.raises(ValueError):
            SpectrogramBuffer(num_bins=4, max_frames=0)
