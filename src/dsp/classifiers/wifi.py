"""Wi-Fi (802.11) rule-based classifier.

802.11 b/g/n at 2.4 GHz uses 20 MHz channels. A Candidate with ~20 MHz
bandwidth and continuous duty is most likely Wi-Fi (and most likely *not*
a drone — but we still tag it so the operator can see what's on air).
"""

from __future__ import annotations

from src.pipeline.contracts import Candidate, Classification, SignalFamily


MIN_BANDWIDTH_HZ = 16e6
MAX_BANDWIDTH_HZ = 24e6


def classify(candidate: Candidate) -> Classification | None:
    """Return a Classification if the candidate matches Wi-Fi, else None."""
    bw = candidate.bandwidth_hz
    if not (MIN_BANDWIDTH_HZ <= bw <= MAX_BANDWIDTH_HZ):
        return None

    reasons = [f"bandwidth {bw / 1e6:.1f} MHz consistent with 802.11 20 MHz channel"]
    confidence = 0.7
    if candidate.duty_cycle is not None and candidate.duty_cycle > 0.3:
        confidence = 0.85
        reasons.append(f"duty cycle {candidate.duty_cycle:.2f} suggests AP traffic")

    return Classification(
        candidate=candidate,
        protocol=SignalFamily.WIFI,
        confidence=confidence,
        reasons=tuple(reasons),
        features={"classifier": "wifi"},
    )


__all__ = ["classify"]
