"""Tests for detector-to-event conversion and lightweight RF tracking."""

import pytest

from src.dsp.detector import Detection
from src.dsp.events import RFEventTracker, detection_to_event
from src.pipeline.contracts import ChannelRole, RFEvent, SignalFamily


BASE_FREQ_HZ = 2.437e9


def _detection(
    *,
    freq_hz: float = BASE_FREQ_HZ,
    bin_start: int = 100,
    bin_end: int = 112,
) -> Detection:
    return Detection(
        freq_hz=freq_hz,
        bandwidth_hz=180_000.0,
        power_dbm=-52.5,
        snr_db=18.0,
        bin_start=bin_start,
        bin_end=bin_end,
    )


def _event(
    frame_index: int,
    *,
    role: ChannelRole = ChannelRole.YAGI,
    freq_hz: float = BASE_FREQ_HZ,
    timestamp_s: float | None = None,
    bearing_deg: float | None = None,
) -> RFEvent:
    return detection_to_event(
        _detection(
            freq_hz=freq_hz,
            bin_start=100 + frame_index,
            bin_end=112 + frame_index,
        ),
        role=role,
        frame_index=frame_index,
        timestamp_s=frame_index * 0.1 if timestamp_s is None else timestamp_s,
        bearing_deg=bearing_deg,
    )


def test_detection_to_event_copies_detector_fields() -> None:
    detection = _detection()

    event = detection_to_event(
        detection,
        role=ChannelRole.YAGI,
        frame_index=7,
        timestamp_s=123.456,
        source="cfar",
        bearing_deg=42.0,
        family=SignalFamily.FHSS,
    )

    assert event.event_id == "yagi-7-100-112"
    assert event.role == ChannelRole.YAGI
    assert event.start_time_s == pytest.approx(123.456)
    assert event.end_time_s == pytest.approx(123.456)
    assert event.center_freq_hz == pytest.approx(detection.freq_hz)
    assert event.bandwidth_hz == pytest.approx(detection.bandwidth_hz)
    assert event.peak_power_dbm == pytest.approx(detection.power_dbm)
    assert event.snr_db == pytest.approx(detection.snr_db)
    assert event.bin_start == detection.bin_start
    assert event.bin_end == detection.bin_end
    assert event.bearing_deg == pytest.approx(42.0)
    assert event.source == "cfar"
    assert event.family == SignalFamily.FHSS


def test_detection_to_event_id_is_deterministic() -> None:
    detection = _detection(bin_start=12, bin_end=17)

    first = detection_to_event(
        detection,
        role=ChannelRole.OMNI,
        frame_index=3,
        timestamp_s=1.0,
    )
    second = detection_to_event(
        detection,
        role=ChannelRole.OMNI,
        frame_index=3,
        timestamp_s=1.5,
    )

    assert first.event_id == "omni-3-12-17"
    assert second.event_id == first.event_id


def test_tracker_groups_nearby_events_by_role_frequency_and_time() -> None:
    tracker = RFEventTracker(max_frequency_gap_hz=100_000.0, max_time_gap_s=0.5)
    first = _event(1, freq_hz=BASE_FREQ_HZ, timestamp_s=1.0, bearing_deg=10.0)
    second = _event(
        2,
        freq_hz=BASE_FREQ_HZ + 40_000.0,
        timestamp_s=1.2,
        bearing_deg=16.0,
    )

    tracks = tracker.process([first, second])

    assert len(tracks) == 1
    assert tracks[0].track_id == "trk-1"
    assert tracks[0].events == (first, second)
    assert tracks[0].latest_event is second
    assert tracks[0].current_bearing_deg == pytest.approx(16.0)


def test_tracker_starts_new_track_when_frequency_gap_is_too_large() -> None:
    tracker = RFEventTracker(max_frequency_gap_hz=100_000.0, max_time_gap_s=0.5)
    first = _event(1, freq_hz=BASE_FREQ_HZ, timestamp_s=1.0)
    second = _event(2, freq_hz=BASE_FREQ_HZ + 250_000.0, timestamp_s=1.2)

    tracks = tracker.process([first, second])

    assert [track.track_id for track in tracks] == ["trk-1", "trk-2"]
    assert tracks[0].events == (first,)
    assert tracks[1].events == (second,)


def test_tracker_starts_new_track_for_different_channel_role() -> None:
    tracker = RFEventTracker(max_frequency_gap_hz=100_000.0, max_time_gap_s=0.5)
    yagi = _event(1, role=ChannelRole.YAGI, timestamp_s=1.0)
    omni = _event(2, role=ChannelRole.OMNI, timestamp_s=1.2)

    tracks = tracker.process([yagi, omni])

    assert [track.track_id for track in tracks] == ["trk-1", "trk-2"]


def test_tracker_confidence_grows_to_full_after_five_events() -> None:
    tracker = RFEventTracker(max_frequency_gap_hz=100_000.0, max_time_gap_s=0.5)

    first_track = tracker.process([_event(1, timestamp_s=1.0)])[0]
    assert first_track.confidence == pytest.approx(0.2)

    tracks = tracker.process(
        [
            _event(2, timestamp_s=1.1),
            _event(3, timestamp_s=1.2),
            _event(4, timestamp_s=1.3),
            _event(5, timestamp_s=1.4),
        ]
    )

    assert len(tracks) == 1
    assert tracks[0].confidence == pytest.approx(1.0)
    assert len(tracks[0].events) == 5
