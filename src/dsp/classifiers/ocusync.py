"""OcuSync / DJI DroneID rule-based classifier.

DJI OcuSync 2/3 video downlink: ~10 MHz OFDM, high duty cycle.
DJI DroneID: 10 MHz OFDM burst every ~600 ms.

A Candidate is classified as OcuSync-family when its bandwidth is consistent
with 10 MHz OFDM. Burst cadence (continuous video vs. ~600 ms beacons)
adjusts confidence but doesn't gate the match.
"""

from __future__ import annotations

from src.pipeline.contracts import Candidate, Classification, SignalFamily


MIN_BANDWIDTH_HZ = 8e6
MAX_BANDWIDTH_HZ = 12e6


def classify(candidate: Candidate) -> Classification | None:
    """Return a Classification if the candidate matches OcuSync, else None."""
    bw = candidate.bandwidth_hz

    if not (MIN_BANDWIDTH_HZ <= bw <= MAX_BANDWIDTH_HZ):
        return None

    reasons = [f"bandwidth {bw / 1e6:.1f} MHz consistent with 10 MHz OFDM"]
    confidence = 0.6

    if candidate.duty_cycle is not None and candidate.duty_cycle > 0.5:
        confidence = 0.8
        reasons.append(
            f"high duty cycle {candidate.duty_cycle:.2f} suggests video downlink"
        )
    elif candidate.num_bursts >= 2:
        confidence = 0.7
        reasons.append(
            f"{candidate.num_bursts} bursts may indicate DroneID beacon cadence"
        )

    return Classification(
        candidate=candidate,
        protocol=SignalFamily.OFDM,
        confidence=confidence,
        reasons=tuple(reasons),
        features={"classifier": "ocusync"},
    )


__all__ = ["classify"]
