"""Tests for src.pipeline.stages.spectrogram.SpectrogramStage."""

import asyncio

import numpy as np

from src.pipeline.contracts import (
    ChannelRole,
    DualSpectrogramFrame,
    SpectrogramFrame,
)
from src.pipeline.stages.spectrogram import SpectrogramStage
from tests.unit._v2_helpers import make_dual_iq_frame, make_v2_test_config


class TestSpectrogramStage:
    def test_emits_dual_spectrogram(self):
        cfg = make_v2_test_config()
        stage = SpectrogramStage(cfg)
        # Need >= 2 * fft_size samples for one Welch frame
        n = 2 * cfg.dsp.spectrogram.fft_size
        iq = np.zeros(n, dtype=np.complex64)
        frame = make_dual_iq_frame(num_samples=n, omni_iq=iq, yagi_iq=iq)

        out = asyncio.run(stage.process(frame))

        assert isinstance(out, DualSpectrogramFrame)
        assert out.omni.role == ChannelRole.OMNI
        assert out.yagi.role == ChannelRole.YAGI
        assert out.omni.num_frames == 1
        assert out.omni.num_bins == cfg.dsp.spectrogram.fft_size

    def test_psd_peak_at_injected_tone(self):
        cfg = make_v2_test_config()
        stage = SpectrogramStage(cfg)
        n = 2 * cfg.dsp.spectrogram.fft_size

        # Synthesize a tone offset from center; PSD peak should land in the
        # corresponding positive-frequency bin.
        sample_rate = 30.72e6
        offset_hz = 5.0e6
        t = np.arange(n) / sample_rate
        iq = (np.exp(1j * 2 * np.pi * offset_hz * t)).astype(np.complex64)
        frame = make_dual_iq_frame(num_samples=n, omni_iq=iq, yagi_iq=iq)

        out: DualSpectrogramFrame = asyncio.run(stage.process(frame))

        psd = out.omni.latest_psd_dbm
        peak_bin = int(np.argmax(psd))
        peak_freq = out.omni.freq_hz[peak_bin]
        bin_width = sample_rate / cfg.dsp.spectrogram.fft_size
        assert np.isclose(
            peak_freq,
            out.omni.center_freq_hz + offset_hz,
            atol=2 * bin_width,
        )
