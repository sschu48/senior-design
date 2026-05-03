"""Tests for src.pipeline.stages.track.TrackStage.

Replaces the V1 test_events.py — same algorithm, new home.
"""

import asyncio

import pytest

from src.pipeline.contracts import ChannelRole, RFEvent, SignalFamily
from src.pipeline.stages.track import TrackStage
from tests.unit._v2_helpers import make_v2_test_config


def _event(
    *,
    event_id: str,
    role: ChannelRole = ChannelRole.OMNI,
    start_time_s: float,
    end_time_s: float | None = None,
    center_freq_hz: float = 2.437e9,
) -> RFEvent:
    return RFEvent(
        event_id=event_id,
        role=role,
        start_time_s=start_time_s,
        end_time_s=end_time_s if end_time_s is not None else start_time_s + 0.001,
        center_freq_hz=center_freq_hz,
        bandwidth_hz=1e6,
        peak_power_dbm=-60.0,
        snr_db=20.0,
        family=SignalFamily.UNKNOWN,
    )


class TestTrackStage:
    def test_first_event_starts_track(self):
        stage = TrackStage(make_v2_test_config())
        out = asyncio.run(stage.process((_event(event_id="e1", start_time_s=0.0),)))
        assert len(out) == 1
        assert out[0].track_id == "trk-1"

    def test_close_event_extends_track(self):
        stage = TrackStage(make_v2_test_config())
        e1 = _event(event_id="e1", start_time_s=0.0)
        e2 = _event(event_id="e2", start_time_s=0.1)
        asyncio.run(stage.process((e1,)))
        out = asyncio.run(stage.process((e2,)))
        assert len(out) == 1
        assert len(out[0].events) == 2

    def test_distant_event_starts_new_track(self):
        stage = TrackStage(
            make_v2_test_config(),
            max_frequency_gap_hz=10e6,
            max_time_gap_s=0.5,
        )
        e1 = _event(event_id="e1", start_time_s=0.0, center_freq_hz=2.437e9)
        e2 = _event(event_id="e2", start_time_s=10.0, center_freq_hz=2.437e9)
        asyncio.run(stage.process((e1, e2)))
        assert [t.track_id for t in stage.tracks] == ["trk-1", "trk-2"]

    def test_track_ids_are_deterministic(self):
        stage = TrackStage(make_v2_test_config())
        events = (
            _event(event_id="a", start_time_s=0.0),
            _event(event_id="b", start_time_s=10.0, center_freq_hz=2.5e9),
            _event(event_id="c", start_time_s=20.0, center_freq_hz=2.6e9),
        )
        asyncio.run(stage.process(events))
        assert [t.track_id for t in stage.tracks] == ["trk-1", "trk-2", "trk-3"]

    def test_reset_clears_state(self):
        stage = TrackStage(make_v2_test_config())
        asyncio.run(stage.process((_event(event_id="e1", start_time_s=0.0),)))
        stage.reset()
        out = asyncio.run(stage.process((_event(event_id="e2", start_time_s=0.0),)))
        assert out[0].track_id == "trk-1"

    def test_rejects_negative_gaps(self):
        with pytest.raises(ValueError):
            TrackStage(make_v2_test_config(), max_frequency_gap_hz=-1.0)
        with pytest.raises(ValueError):
            TrackStage(make_v2_test_config(), max_time_gap_s=-1.0)
