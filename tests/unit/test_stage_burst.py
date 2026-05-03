"""Tests for src.pipeline.stages.burst.BurstStage."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from src.pipeline.contracts import Burst, ChannelRole, SpectrogramFrame
from src.pipeline.stages.burst import BurstStage, _contiguous_runs
from tests.unit._v2_helpers import make_spectrogram_frame, make_v2_test_config


def _seed_quiet_frames(stage: BurstStage, fft_size: int, num_frames: int = 5) -> None:
    """Push quiet frames through the stage so the noise-floor estimator settles."""
    for i in range(num_frames):
        spec = make_spectrogram_frame(
            frame_index=i,
            timestamp_s=i * 1e-3,
            fft_size=fft_size,
            psd_dbm=np.full(fft_size, -90.0),
        )
        asyncio.run(stage.process(spec))


class TestContiguousRuns:
    def test_empty_when_all_false(self):
        assert _contiguous_runs(np.zeros(8, dtype=bool)) == []

    def test_single_run(self):
        mask = np.array([0, 0, 1, 1, 1, 0, 0, 0], dtype=bool)
        assert _contiguous_runs(mask) == [(2, 4)]

    def test_run_at_end(self):
        mask = np.array([0, 0, 0, 1, 1], dtype=bool)
        assert _contiguous_runs(mask) == [(3, 4)]

    def test_two_runs(self):
        mask = np.array([1, 1, 0, 0, 1, 1, 1, 0], dtype=bool)
        assert _contiguous_runs(mask) == [(0, 1), (4, 6)]


class TestBurstStage:
    def test_quiet_input_emits_nothing(self):
        cfg = make_v2_test_config()
        stage = BurstStage(cfg)
        spec = make_spectrogram_frame(fft_size=cfg.dsp.spectrogram.fft_size)
        out = asyncio.run(stage.process(spec))
        assert out == ()

    def test_burst_emitted_when_run_closes(self):
        cfg = make_v2_test_config()
        n = cfg.dsp.spectrogram.fft_size
        stage = BurstStage(cfg)
        _seed_quiet_frames(stage, n)

        # Active frames: bins 100-110 hot for 3 frames.
        active_psd = np.full(n, -90.0)
        active_psd[100:111] = -50.0
        for i in range(3):
            spec = make_spectrogram_frame(
                frame_index=10 + i,
                timestamp_s=0.010 + i * 1e-3,
                fft_size=n,
                psd_dbm=active_psd,
            )
            out = asyncio.run(stage.process(spec))
            assert out == ()  # Run still active

        # Now go quiet — burst should close and be emitted.
        quiet_psd = np.full(n, -90.0)
        spec = make_spectrogram_frame(
            frame_index=20,
            timestamp_s=0.020,
            fft_size=n,
            psd_dbm=quiet_psd,
        )
        out = asyncio.run(stage.process(spec))
        assert len(out) == 1
        burst: Burst = out[0]
        assert burst.role == ChannelRole.OMNI
        assert burst.bin_start == 100
        assert burst.bin_end == 110
        assert burst.frame_start_index == 10
        assert burst.frame_end_index == 12
        assert burst.peak_power_dbm == pytest.approx(-50.0)
        assert burst.snr_db > 30.0

    def test_active_run_persists_across_frames(self):
        cfg = make_v2_test_config()
        n = cfg.dsp.spectrogram.fft_size
        stage = BurstStage(cfg)
        _seed_quiet_frames(stage, n)

        active_psd = np.full(n, -90.0)
        active_psd[50:60] = -40.0

        for i in range(5):
            spec = make_spectrogram_frame(
                frame_index=100 + i,
                timestamp_s=i * 1e-3,
                fft_size=n,
                psd_dbm=active_psd,
            )
            out = asyncio.run(stage.process(spec))
            assert out == ()

        # The run is still active after 5 frames.
        assert len(stage._active_runs) == 1

    def test_reset_clears_state(self):
        cfg = make_v2_test_config()
        n = cfg.dsp.spectrogram.fft_size
        stage = BurstStage(cfg)
        _seed_quiet_frames(stage, n)
        active_psd = np.full(n, -90.0)
        active_psd[40:50] = -50.0
        spec = make_spectrogram_frame(
            frame_index=10, timestamp_s=0.010, fft_size=n, psd_dbm=active_psd
        )
        asyncio.run(stage.process(spec))
        assert stage._active_runs

        stage.reset()
        assert stage._active_runs == []

    def test_rejects_yagi_input(self):
        cfg = make_v2_test_config()
        stage = BurstStage(cfg)
        spec = make_spectrogram_frame(
            role=ChannelRole.YAGI, fft_size=cfg.dsp.spectrogram.fft_size
        )
        with pytest.raises(ValueError, match="omni"):
            asyncio.run(stage.process(spec))
