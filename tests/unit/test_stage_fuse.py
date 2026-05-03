"""Tests for src.pipeline.stages.fuse.FuseStage."""

import asyncio

from src.pipeline.contracts import (
    BearingEstimate,
    Burst,
    Candidate,
    ChannelRole,
    Classification,
    FuseRequest,
    SignalFamily,
)
from src.pipeline.stages.fuse import FuseStage
from tests.unit._v2_helpers import make_v2_test_config


def _classification(candidate_id: str, protocol: SignalFamily) -> Classification:
    burst = Burst(
        burst_id="b",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        freq_lo_hz=2.4365e9,
        freq_hi_hz=2.4373e9,
        peak_power_dbm=-55.0,
        snr_db=22.0,
        bin_start=0,
        bin_end=10,
        frame_start_index=0,
        frame_end_index=4,
    )
    cand = Candidate(
        candidate_id=candidate_id,
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        center_freq_hz=2.437e9,
        bandwidth_hz=800e3,
        bursts=(burst,),
    )
    return Classification(candidate=cand, protocol=protocol, confidence=0.7,
                         reasons=("matches",))


class TestFuseStage:
    def test_event_per_classification_with_bearing(self):
        stage = FuseStage(make_v2_test_config())
        cls = _classification("cand-1", SignalFamily.FHSS)
        bearing = BearingEstimate(
            candidate_id="cand-1",
            bearing_deg=212.0,
            confidence=0.6,
            peak_power_dbm=-50.0,
        )
        req = FuseRequest(
            frame_index=7,
            timestamp_s=0.123,
            classifications=(cls,),
            bearings=(bearing,),
        )
        out = asyncio.run(stage.process(req))
        assert len(out) == 1
        ev = out[0]
        assert ev.event_id == "evt-7-cand-1"
        assert ev.family == SignalFamily.FHSS
        assert ev.bearing_deg == 212.0
        assert ev.features["bearing_confidence"] == 0.6

    def test_classification_pending_until_bearing_arrives(self):
        # FuseStage now requires both classification and bearing before
        # emitting an event. A classification without a matching bearing
        # is held in pending and emitted later when the bearing arrives.
        stage = FuseStage(make_v2_test_config())
        cls = _classification("cand-2", SignalFamily.FHSS)

        # Frame 1: classification arrives, no bearing yet → no event.
        out1 = asyncio.run(stage.process(FuseRequest(
            frame_index=0,
            timestamp_s=0.0,
            classifications=(cls,),
            bearings=(),
        )))
        assert out1 == ()
        assert stage.pending_count == 1

        # Frame 2: bearing arrives → event emitted, pending cleared.
        bearing = BearingEstimate(
            candidate_id="cand-2",
            bearing_deg=42.0,
            confidence=0.7,
            peak_power_dbm=-50.0,
        )
        out2 = asyncio.run(stage.process(FuseRequest(
            frame_index=5,
            timestamp_s=0.005,
            classifications=(),
            bearings=(bearing,),
        )))
        assert len(out2) == 1
        assert out2[0].bearing_deg == 42.0
        assert stage.pending_count == 0
