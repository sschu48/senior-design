"""Tests for src.pipeline.stages.bearing.BearingStage."""

import asyncio

import numpy as np

from src.pipeline.contracts import (
    BearingRequest,
    Burst,
    Candidate,
    ChannelRole,
    Classification,
    SignalFamily,
)
from src.pipeline.stages.bearing import BearingStage
from tests.unit._v2_helpers import make_spectrogram_frame, make_v2_test_config


def _candidate(center_hz: float, bw_hz: float) -> Candidate:
    burst = Burst(
        burst_id="b-1",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        freq_lo_hz=center_hz - bw_hz / 2,
        freq_hi_hz=center_hz + bw_hz / 2,
        peak_power_dbm=-55.0,
        snr_db=20.0,
        bin_start=0,
        bin_end=10,
        frame_start_index=0,
        frame_end_index=5,
    )
    return Candidate(
        candidate_id="cand-1",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        center_freq_hz=center_hz,
        bandwidth_hz=bw_hz,
        bursts=(burst,),
    )


class TestBearingStage:
    def test_no_classifications_returns_empty(self):
        stage = BearingStage(make_v2_test_config())
        yagi_spec = make_spectrogram_frame(role=ChannelRole.YAGI, fft_size=256)
        req = BearingRequest(
            yagi_spectrogram=yagi_spec, classifications=(), azimuth_deg=45.0
        )
        out = asyncio.run(stage.process(req))
        assert out == ()

    def test_strong_yagi_signal_yields_high_confidence(self):
        cfg = make_v2_test_config()
        stage = BearingStage(cfg)
        n = cfg.dsp.spectrogram.fft_size
        psd = np.full(n, -90.0)
        # Single-bin tone at the center frequency.
        psd[n // 2] = -40.0

        yagi_spec = make_spectrogram_frame(
            role=ChannelRole.YAGI, fft_size=n, psd_dbm=psd
        )
        # Wide candidate window so CFAR sees noise + signal.
        candidate = _candidate(center_hz=2.437e9, bw_hz=4e6)
        cls = Classification(
            candidate=candidate,
            protocol=SignalFamily.FHSS,
            confidence=0.7,
        )
        req = BearingRequest(
            yagi_spectrogram=yagi_spec,
            classifications=(cls,),
            azimuth_deg=128.0,
        )
        out = asyncio.run(stage.process(req))
        assert len(out) == 1
        est = out[0]
        assert est.candidate_id == "cand-1"
        assert est.bearing_deg == 128.0
        assert est.confidence > 0.0
        assert est.peak_power_dbm > -50.0

    def test_quiet_yagi_yields_low_confidence(self):
        cfg = make_v2_test_config()
        stage = BearingStage(cfg)
        n = cfg.dsp.spectrogram.fft_size
        psd = np.full(n, -90.0)
        yagi_spec = make_spectrogram_frame(
            role=ChannelRole.YAGI, fft_size=n, psd_dbm=psd
        )
        candidate = _candidate(center_hz=2.437e9, bw_hz=400e3)
        cls = Classification(
            candidate=candidate, protocol=SignalFamily.FHSS, confidence=0.7
        )
        req = BearingRequest(
            yagi_spectrogram=yagi_spec,
            classifications=(cls,),
            azimuth_deg=0.0,
        )
        out = asyncio.run(stage.process(req))
        assert out[0].confidence < 0.2
