"""FuseStage — FuseRequest → tuple[RFEvent, ...].

Joins each Classification with its matching BearingEstimate (by
``candidate_id``) and emits one ``RFEvent`` per Classification. Bearings
are optional — if no bearing was produced for a candidate, the event is
emitted without one.
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

    async def process(self, request: FuseRequest) -> tuple[RFEvent, ...]:
        bearings_by_id: dict[str, BearingEstimate] = {
            b.candidate_id: b for b in request.bearings
        }
        events: list[RFEvent] = []
        for cls in request.classifications:
            bearing = bearings_by_id.get(cls.candidate_id)
            events.append(self._build_event(cls, bearing, request))
        return tuple(events)

    def _build_event(
        self,
        cls: Classification,
        bearing: BearingEstimate | None,
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
            bearing_deg=None if bearing is None else bearing.bearing_deg,
            duty_cycle=cand.duty_cycle,
            hop_rate_hz=cand.hop_rate_hz,
            supporting_frames=peak_burst.frame_end_index
            - peak_burst.frame_start_index
            + 1,
            features={
                "classifier_confidence": cls.confidence,
                "classifier_reasons": list(cls.reasons),
                "bearing_confidence": (
                    None if bearing is None else bearing.confidence
                ),
            },
        )


def _peak_burst(candidate: Candidate) -> Burst:
    """Return the burst within the candidate with the highest peak power."""
    return max(candidate.bursts, key=lambda b: b.peak_power_dbm)
