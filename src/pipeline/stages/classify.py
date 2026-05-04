"""ClassifyStage — tuple[Candidate, ...] → tuple[Classification, ...].

Runs each candidate through the configured rule-based classifiers in order
(``config.dsp.classifier.protocols``) and emits the first match whose
confidence meets ``config.dsp.classifier.min_confidence``. Candidates that
no classifier accepts are still emitted, labelled ``UNKNOWN_RF`` — Stage 3
(bearing) and downstream consumers still want to know about them.
"""

from __future__ import annotations

from typing import Callable

from src.dsp.classifiers import elrs, ocusync, wifi
from src.pipeline.contracts import Candidate, Classification, SignalFamily
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


_CLASSIFIERS: dict[str, Callable[[Candidate], Classification | None]] = {
    "elrs": elrs.classify,
    "ocusync": ocusync.classify,
    "wifi": wifi.classify,
}


class ClassifyStage(Stage[tuple[Candidate, ...], tuple[Classification, ...]]):
    name = "classify"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config
        self.min_confidence = config.dsp.classifier.min_confidence

        self._classifiers: list[Callable[[Candidate], Classification | None]] = []
        for proto in config.dsp.classifier.protocols:
            if proto not in _CLASSIFIERS:
                raise ValueError(
                    f"Unknown classifier protocol: {proto!r} "
                    f"(known: {sorted(_CLASSIFIERS)})"
                )
            self._classifiers.append(_CLASSIFIERS[proto])

    async def process(
        self, candidates: tuple[Candidate, ...]
    ) -> tuple[Classification, ...]:
        out: list[Classification] = []
        for cand in candidates:
            cls = self._classify_one(cand)
            out.append(cls)
        return tuple(out)

    def _classify_one(self, candidate: Candidate) -> Classification:
        for classifier in self._classifiers:
            result = classifier(candidate)
            if result is None:
                continue
            if result.confidence >= self.min_confidence:
                return result
        return Classification(
            candidate=candidate,
            protocol=SignalFamily.UNKNOWN,
            confidence=0.0,
            reasons=("no rule matched",),
            features={"classifier": "fallthrough"},
        )
