"""FuseStage — FuseRequest → tuple[RFEvent, ...].

Joins each Classification with its matching BearingEstimate (by
``candidate_id``) and emits one ``RFEvent`` per joined pair.

In V2 the pairing is **delayed**: ClassifyStage emits a Classification on
the frame the burst closes, but BearingStage may take many frames to
complete the swept bearing measurement. FuseStage holds pending
classifications keyed by ``candidate_id`` and emits the event only when
the matching ``BearingEstimate`` arrives. This is what makes the emitted
``RFEvent`` the operational ALARM event from
``docs/ARCHITECTURE_V2.md`` §4.4.
"""

from __future__ import annotations

from src.pipeline.contracts import (
    BearingEstimate,
    Burst,
    Candidate,
    Classification,
    FuseRequest,
    RFEvent,
)
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


class FuseStage(Stage[FuseRequest, tuple[RFEvent, ...]]):
    name = "fuse"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config
        self._pending: dict[str, Classification] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def reset(self) -> None:
        self._pending.clear()

    async def process(self, request: FuseRequest) -> tuple[RFEvent, ...]:
        # Stage classifications until their bearing arrives.
        for cls in request.classifications:
            self._pending[cls.candidate_id] = cls

        # Match bearings to pending classifications.
        events: list[RFEvent] = []
        for bearing in request.bearings:
            cls = self._pending.pop(bearing.candidate_id, None)
            if cls is None:
                # Bearing without a pending classification — drop it. This
                # shouldn't happen given the pipeline order, but the
                # contract is "events require both", so we honor that.
                continue
            events.append(self._build_event(cls, bearing, request))
        return tuple(events)

    # ---- internals ------------------------------------------------------

    def _build_event(
        self,
        cls: Classification,
        bearing: BearingEstimate,
        request: FuseRequest,
    ) -> RFEvent:
        cand = cls.candidate
        peak_burst = _peak_burst(cand)
        return RFEvent(
            event_id=f"evt-{request.frame_index}-{cand.candidate_id}",
            role=cand.role,
            start_time_s=cand.start_time_s,
            end_time_s=cand.end_time_s,
            center_freq_hz=cand.center_freq_hz,
            bandwidth_hz=cand.bandwidth_hz,
            peak_power_dbm=peak_burst.peak_power_dbm,
            snr_db=peak_burst.snr_db,
            family=cls.protocol,
            source="v2-pipeline",
            bin_start=peak_burst.bin_start,
            bin_end=peak_burst.bin_end,
            bearing_deg=bearing.bearing_deg,
            duty_cycle=cand.duty_cycle,
            hop_rate_hz=cand.hop_rate_hz,
            supporting_frames=peak_burst.frame_end_index
            - peak_burst.frame_start_index
            + 1,
            features={
                "classifier_confidence": cls.confidence,
                "classifier_reasons": list(cls.reasons),
                "bearing_confidence": bearing.confidence,
                "bearing_peak_power_dbm": bearing.peak_power_dbm,
            },
        )


def _peak_burst(candidate: Candidate) -> Burst:
    return max(candidate.bursts, key=lambda b: b.peak_power_dbm)
