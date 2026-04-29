"""RF event conversion and lightweight tracking utilities."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.dsp.detector import Detection
from src.pipeline.contracts import ChannelRole, RFEvent, SignalFamily, TrackedEmitter


DEFAULT_MAX_FREQUENCY_GAP_HZ = 1_000_000.0
DEFAULT_MAX_TIME_GAP_S = 1.0
EVENTS_FOR_FULL_CONFIDENCE = 5.0


def detection_to_event(
    detection: Detection,
    *,
    role: ChannelRole,
    frame_index: int,
    timestamp_s: float,
    source: str = "detector",
    bearing_deg: float | None = None,
    family: SignalFamily = SignalFamily.UNKNOWN,
) -> RFEvent:
    """Convert one detector output into the Phase 0 RFEvent contract."""

    role = ChannelRole(role)
    family = SignalFamily(family)
    frame_index = int(frame_index)
    if frame_index < 0:
        raise ValueError("frame_index must be non-negative")

    return RFEvent(
        event_id=f"{role.value}-{frame_index}-{detection.bin_start}-{detection.bin_end}",
        role=role,
        start_time_s=float(timestamp_s),
        end_time_s=float(timestamp_s),
        center_freq_hz=float(detection.freq_hz),
        bandwidth_hz=float(detection.bandwidth_hz),
        peak_power_dbm=float(detection.power_dbm),
        snr_db=float(detection.snr_db),
        family=family,
        source=source,
        bin_start=int(detection.bin_start),
        bin_end=int(detection.bin_end),
        bearing_deg=None if bearing_deg is None else float(bearing_deg),
    )


@dataclass
class RFEventTracker:
    """Frame-to-frame RFEvent grouping scaffold.

    Tracks are matched only against their latest event. This keeps the Phase 0
    tracker deterministic and cheap while downstream AoA/classifier work is
    still taking shape.
    """

    max_frequency_gap_hz: float = DEFAULT_MAX_FREQUENCY_GAP_HZ
    max_time_gap_s: float = DEFAULT_MAX_TIME_GAP_S
    _tracks: list[TrackedEmitter] = field(default_factory=list, init=False, repr=False)
    _next_track_number: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_frequency_gap_hz < 0:
            raise ValueError("max_frequency_gap_hz must be non-negative")
        if self.max_time_gap_s < 0:
            raise ValueError("max_time_gap_s must be non-negative")

    @property
    def tracks(self) -> tuple[TrackedEmitter, ...]:
        """Current immutable track snapshots."""

        return tuple(self._tracks)

    def reset(self) -> None:
        """Clear all tracks and restart deterministic track numbering."""

        self._tracks.clear()
        self._next_track_number = 1

    def process(self, events: list[RFEvent]) -> list[TrackedEmitter]:
        """Attach events to existing tracks or start new tracks."""

        for event in events:
            track_index = self._best_track_index(event)
            if track_index is None:
                self._tracks.append(self._new_track(event))
            else:
                self._tracks[track_index] = self._append_event(
                    self._tracks[track_index],
                    event,
                )

        return list(self._tracks)

    def _best_track_index(self, event: RFEvent) -> int | None:
        best_index: int | None = None
        best_key: tuple[float, float, int] | None = None

        for index, track in enumerate(self._tracks):
            latest = track.latest_event
            if latest.role != event.role:
                continue

            frequency_gap_hz = abs(event.center_freq_hz - latest.center_freq_hz)
            if frequency_gap_hz > self.max_frequency_gap_hz:
                continue

            time_gap_s = event.start_time_s - latest.end_time_s
            if time_gap_s < 0 or time_gap_s > self.max_time_gap_s:
                continue

            key = (frequency_gap_hz, time_gap_s, index)
            if best_key is None or key < best_key:
                best_index = index
                best_key = key

        return best_index

    def _new_track(self, event: RFEvent) -> TrackedEmitter:
        track = self._build_track(f"trk-{self._next_track_number}", (event,))
        self._next_track_number += 1
        return track

    def _append_event(self, track: TrackedEmitter, event: RFEvent) -> TrackedEmitter:
        return self._build_track(track.track_id, track.events + (event,))

    def _build_track(
        self,
        track_id: str,
        events: tuple[RFEvent, ...],
    ) -> TrackedEmitter:
        latest = events[-1]
        return TrackedEmitter(
            track_id=track_id,
            events=events,
            current_bearing_deg=latest.bearing_deg,
            bearing_rate_deg_s=latest.bearing_rate_deg_s,
            confidence=min(1.0, len(events) / EVENTS_FOR_FULL_CONFIDENCE),
        )


__all__ = [
    "DEFAULT_MAX_FREQUENCY_GAP_HZ",
    "DEFAULT_MAX_TIME_GAP_S",
    "EVENTS_FOR_FULL_CONFIDENCE",
    "RFEventTracker",
    "detection_to_event",
]
