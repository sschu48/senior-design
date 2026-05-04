"""Stage abstraction for the SENTINEL V2 pipeline.

A Stage is a single processing step with one typed input and one typed output.
Stages compose into a pipeline (see ``src/pipeline/pipeline.py`` — Phase 1) and
each stage exposes a fan-out subscribe interface so visualization, logging, or
test harnesses can tap a stage's output without modifying the pipeline.

Design intent (kept deliberately small):
- No DI framework, no plugin registry, no event-bus library.
- One typed input, one typed output. Multi-output stages return a ``tuple``.
- Subscribers receive emitted messages via ``asyncio.Queue``. A slow
  subscriber drops its oldest queued message rather than blocking the
  pipeline — visualization is best-effort, detection is not.
- Stateful stages put their state on the instance and implement ``reset()``.
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

InMsg = TypeVar("InMsg")
OutMsg = TypeVar("OutMsg")


class Stage(Generic[InMsg, OutMsg]):
    """Base class for pipeline stages.

    Subclasses override ``process``. The pipeline runner calls
    ``process`` then ``emit`` on the returned value, so subscribers see
    every output the stage produces.
    """

    name: str = "stage"
    subscriber_queue_maxsize: int = 64

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[OutMsg]] = []

    def subscribe(self) -> asyncio.Queue[OutMsg]:
        """Return a new queue that will receive every emitted message."""
        q: asyncio.Queue[OutMsg] = asyncio.Queue(maxsize=self.subscriber_queue_maxsize)
        self._subscribers.append(q)
        return q

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def emit(self, msg: OutMsg) -> None:
        """Fan out one message to every subscriber, dropping oldest on full queues."""
        for q in self._subscribers:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)

    async def process(self, msg: InMsg) -> OutMsg:
        """Transform one input message into one output message.

        Override in subclasses. Multi-output stages return a tuple.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must override process()"
        )

    def reset(self) -> None:
        """Clear any frame-spanning state. Override if the stage is stateful."""
        return None


__all__ = ["Stage", "InMsg", "OutMsg"]
