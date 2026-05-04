"""Tests for src.pipeline.stages.classify.ClassifyStage."""

import asyncio
import dataclasses

from src.pipeline.contracts import Burst, Candidate, ChannelRole, SignalFamily
from src.pipeline.stages.classify import ClassifyStage
from tests.unit._v2_helpers import make_v2_test_config


def _candidate(*, bandwidth_hz: float, candidate_id: str = "c-1") -> Candidate:
    burst = Burst(
        burst_id=f"b-for-{candidate_id}",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        freq_lo_hz=2.437e9 - bandwidth_hz / 2,
        freq_hi_hz=2.437e9 + bandwidth_hz / 2,
        peak_power_dbm=-55.0,
        snr_db=20.0,
        bin_start=0,
        bin_end=10,
        frame_start_index=0,
        frame_end_index=5,
    )
    return Candidate(
        candidate_id=candidate_id,
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        center_freq_hz=2.437e9,
        bandwidth_hz=bandwidth_hz,
        bursts=(burst,),
    )


class TestClassifyStage:
    def test_elrs_match(self):
        stage = ClassifyStage(make_v2_test_config())
        out = asyncio.run(stage.process((_candidate(bandwidth_hz=800e3),)))
        assert out[0].protocol == SignalFamily.FHSS

    def test_ocusync_match(self):
        stage = ClassifyStage(make_v2_test_config())
        out = asyncio.run(stage.process((_candidate(bandwidth_hz=10e6),)))
        assert out[0].protocol == SignalFamily.OFDM

    def test_wifi_match(self):
        stage = ClassifyStage(make_v2_test_config())
        out = asyncio.run(stage.process((_candidate(bandwidth_hz=20e6),)))
        assert out[0].protocol == SignalFamily.WIFI

    def test_unknown_falls_through(self):
        stage = ClassifyStage(make_v2_test_config())
        out = asyncio.run(stage.process((_candidate(bandwidth_hz=3e6),)))
        assert out[0].protocol == SignalFamily.UNKNOWN
        assert out[0].confidence == 0.0

    def test_unknown_protocol_in_config_raises(self):
        cfg = make_v2_test_config()
        bad_classifier = dataclasses.replace(
            cfg.dsp.classifier, protocols=("not-a-real-protocol",)
        )
        bad_dsp = dataclasses.replace(cfg.dsp, classifier=bad_classifier)
        bad_cfg = dataclasses.replace(cfg, dsp=bad_dsp)
        try:
            ClassifyStage(bad_cfg)
        except ValueError as e:
            assert "not-a-real-protocol" in str(e)
        else:
            raise AssertionError("expected ValueError")
