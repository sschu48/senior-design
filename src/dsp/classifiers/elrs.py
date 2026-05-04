"""ELRS rule-based classifier.

ELRS at 2.4 GHz: ~800 kHz channel width, packets <10 ms, hops up to 500 Hz
across the band. We classify a Candidate as ELRS if its bandwidth and burst
shape are consistent with that profile.
"""

from __future__ import annotations

from src.pipeline.contracts import Candidate, Classification, SignalFamily


# Bandwidth window (Hz) — ELRS channels are ~800 kHz; allow some slop.
MIN_BANDWIDTH_HZ = 400e3
MAX_BANDWIDTH_HZ = 1_500e3

# Per-burst duration window (s).
MAX_BURST_DURATION_S = 0.010

# Hop characteristic: ELRS hops up to 500 Hz; require multiple bursts to
# distinguish from a single one-shot transmission.
MIN_BURSTS_FOR_HOP_EVIDENCE = 3


def classify(candidate: Candidate) -> Classification | None:
    """Return a Classification if the candidate matches ELRS, else None."""
    reasons: list[str] = []
    bw = candidate.bandwidth_hz

    if not (MIN_BANDWIDTH_HZ <= bw <= MAX_BANDWIDTH_HZ):
        return None
    reasons.append(f"bandwidth {bw / 1e3:.0f} kHz consistent with ELRS")

    max_burst_dur = max(b.duration_sec for b in candidate.bursts)
    if max_burst_dur > MAX_BURST_DURATION_S:
        return None
    reasons.append(f"max burst {max_burst_dur * 1000:.1f} ms < 10 ms")

    confidence = 0.5
    if candidate.num_bursts >= MIN_BURSTS_FOR_HOP_EVIDENCE:
        confidence = 0.75
        reasons.append(f"{candidate.num_bursts} bursts suggest hop pattern")
    if candidate.hop_rate_hz is not None and 50 <= candidate.hop_rate_hz <= 1000:
        confidence = min(0.9, confidence + 0.15)
        reasons.append(f"hop rate {candidate.hop_rate_hz:.0f} Hz in ELRS range")

    return Classification(
        candidate=candidate,
        protocol=SignalFamily.FHSS,
        confidence=confidence,
        reasons=tuple(reasons),
        features={"classifier": "elrs"},
    )


__all__ = ["classify"]
