"""TrackStage — tuple[RFEvent, ...] → tuple[TrackedEmitter, ...].

Frame-to-frame association: each event is matched against the latest event
of each open track by frequency proximity and time gap. Matched events
extend the track; unmatched events start a new one. Track IDs are assigned
deterministically (``trk-1``, ``trk-2``, ...) so logs and tests are
reproducible.

Algorithm migrated from the V1 ``src.dsp.events.RFEventTracker``; the
internals are unchanged but the surface is now a Stage.
"""

from __future__ import annotations

from src.pipeline.contracts import RFEvent, TrackedEmitter
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


DEFAULT_MAX_FREQUENCY_GAP_HZ = 1_000_000.0
DEFAULT_MAX_TIME_GAP_S = 1.0
EVENTS_FOR_FULL_CONFIDENCE = 5.0


class TrackStage(Stage[tuple[RFEvent, ...], tuple[TrackedEmitter, ...]]):
    name = "track"

    def __init__(
        self,
        config: SentinelConfig,
        *,
        max_frequency_gap_hz: float = DEFAULT_MAX_FREQUENCY_GAP_HZ,
        max_time_gap_s: float = DEFAULT_MAX_TIME_GAP_S,
    ) -> None:
        super().__init__()
        if max_frequency_gap_hz < 0:
            raise ValueError("max_frequency_gap_hz must be non-negative")
        if max_time_gap_s < 0:
            raise ValueError("max_time_gap_s must be non-negative")
        self.config = config
        self.max_frequency_gap_hz = max_frequency_gap_hz
        self.max_time_gap_s = max_time_gap_s

        self._tracks: list[TrackedEmitter] = []
        self._next_track_number = 1

    @property
    def tracks(self) -> tuple[TrackedEmitter, ...]:
        return tuple(self._tracks)

    def reset(self) -> None:
        self._tracks.clear()
        self._next_track_number = 1

    async def process(
        self, events: tuple[RFEvent, ...]
    ) -> tuple[TrackedEmitter, ...]:
        for event in events:
            track_index = self._best_track_index(event)
            if track_index is None:
                self._tracks.append(self._new_track(event))
            else:
                self._tracks[track_index] = self._append(
                    self._tracks[track_index], event
                )
        return tuple(self._tracks)

    # ---- internals ------------------------------------------------------

    def _best_track_index(self, event: RFEvent) -> int | None:
        best_index: int | None = None
        best_key: tuple[float, float, int] | None = None

        for index, track in enumerate(self._tracks):
            latest = track.latest_event
            if latest.role != event.role:
                continue

            freq_gap = abs(event.center_freq_hz - latest.center_freq_hz)
            if freq_gap > self.max_frequency_gap_hz:
                continue

            time_gap = event.start_time_s - latest.end_time_s
            if time_gap < 0 or time_gap > self.max_time_gap_s:
                continue

            key = (freq_gap, time_gap, index)
            if best_key is None or key < best_key:
                best_index = index
                best_key = key

        return best_index

    def _new_track(self, event: RFEvent) -> TrackedEmitter:
        track = self._build(f"trk-{self._next_track_number}", (event,))
        self._next_track_number += 1
        return track

    def _append(self, track: TrackedEmitter, event: RFEvent) -> TrackedEmitter:
        return self._build(track.track_id, track.events + (event,))

    @staticmethod
    def _build(track_id: str, events: tuple[RFEvent, ...]) -> TrackedEmitter:
        latest = events[-1]
        return TrackedEmitter(
            track_id=track_id,
            events=events,
            current_bearing_deg=latest.bearing_deg,
            bearing_rate_deg_s=latest.bearing_rate_deg_s,
            confidence=min(1.0, len(events) / EVENTS_FOR_FULL_CONFIDENCE),
        )
