"""ClusterStage — tuple[Burst, ...] → tuple[Candidate, ...].

Groups Bursts into candidate emissions. For Phase 1 the rule is the
simplest correct one: every Burst becomes a single-burst Candidate. This is
honest about the level of analysis available (Stage 2 classifiers run on
single-burst bandwidth and duration), and leaves room for richer FHSS-style
clustering in a follow-up phase without changing the contract.

Wider clustering (merging bursts that share a frequency band over a short
window, or recognizing a hop train) is intentionally deferred until we
have ESP32 + HackRF captures to tune against.
"""

from __future__ import annotations

from src.pipeline.contracts import Burst, Candidate
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


class ClusterStage(Stage[tuple[Burst, ...], tuple[Candidate, ...]]):
    name = "cluster"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config
        self._next_candidate_id = 1

    def reset(self) -> None:
        self._next_candidate_id = 1

    async def process(self, bursts: tuple[Burst, ...]) -> tuple[Candidate, ...]:
        candidates: list[Candidate] = []
        for burst in bursts:
            candidates.append(self._wrap(burst))
        return tuple(candidates)

    def _wrap(self, burst: Burst) -> Candidate:
        candidate_id = f"cand-{self._next_candidate_id}"
        self._next_candidate_id += 1
        return Candidate(
            candidate_id=candidate_id,
            role=burst.role,
            start_time_s=burst.start_time_s,
            end_time_s=burst.end_time_s,
            center_freq_hz=burst.center_freq_hz,
            bandwidth_hz=burst.bandwidth_hz,
            bursts=(burst,),
        )
