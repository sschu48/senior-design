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

    def test_swept_bearing_emits_after_min_samples(self):
        cfg = make_v2_test_config()
        stage = BearingStage(cfg)
        n = cfg.dsp.spectrogram.fft_size
        psd = np.full(n, -90.0)
        # Strong tone at center, swept across azimuths.
        psd[n // 2] = -40.0
        yagi_spec = make_spectrogram_frame(
            role=ChannelRole.YAGI, fft_size=n, psd_dbm=psd
        )
        candidate = _candidate(center_hz=2.437e9, bw_hz=4e6)
        cls = Classification(
            candidate=candidate,
            protocol=SignalFamily.FHSS,
            confidence=0.7,
        )

        async def run():
            outs = []
            # Frame 1: classification arrives, sweep opens, first sample.
            outs.append(await stage.process(BearingRequest(
                yagi_spectrogram=yagi_spec,
                classifications=(cls,),
                azimuth_deg=120.0,
            )))
            # Frame 2: sweep continues, second sample.
            outs.append(await stage.process(BearingRequest(
                yagi_spectrogram=yagi_spec,
                classifications=(),
                azimuth_deg=130.0,
            )))
            # Frame 3: third sample → triggers emission (min_sweep_samples=3).
            outs.append(await stage.process(BearingRequest(
                yagi_spectrogram=yagi_spec,
                classifications=(),
                azimuth_deg=140.0,
            )))
            return outs

        outs = asyncio.run(run())
        assert outs[0] == ()
        assert outs[1] == ()
        assert len(outs[2]) == 1
        est = outs[2][0]
        assert est.candidate_id == "cand-1"
        assert est.bearing_deg in {120.0, 130.0, 140.0}  # one of the sampled azimuths
        assert est.confidence > 0.0
        assert est.peak_power_dbm > -50.0
        assert est.sweep_powers_dbm is not None
        assert est.sweep_powers_dbm.size == 3

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

        async def run():
            outs = []
            for az in (0.0, 5.0, 10.0):
                req = BearingRequest(
                    yagi_spectrogram=yagi_spec,
                    classifications=(cls,) if az == 0.0 else (),
                    azimuth_deg=az,
                )
                outs.append(await stage.process(req))
            return outs

        outs = asyncio.run(run())
        assert len(outs[2]) == 1
        assert outs[2][0].confidence < 0.2
