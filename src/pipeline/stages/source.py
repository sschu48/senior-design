"""SourceStage — wraps a DualIQSource into a Stage.

Reads ``num_samples_per_frame`` complex samples per frame from the underlying
DualIQSource and emits a ``DualIQFrame`` downstream. Lifecycle (``start``/``stop``)
is delegated to the underlying source.
"""

from __future__ import annotations

from src.pipeline.contracts import DualIQFrame
from src.pipeline.stage import Stage
from src.sdr.capture import DualIQSource


class SourceStage(Stage[None, DualIQFrame]):
    """Pipeline source: DualIQSource → DualIQFrame."""

    name = "source"

    def __init__(self, source: DualIQSource, num_samples_per_frame: int) -> None:
        super().__init__()
        if num_samples_per_frame < 1:
            raise ValueError("num_samples_per_frame must be >= 1")
        self.source = source
        self.num_samples_per_frame = num_samples_per_frame

    async def start(self) -> None:
        await self.source.start()

    async def stop(self) -> None:
        await self.source.stop()

    async def process(self, _: None = None) -> DualIQFrame:
        return await self.source.read(self.num_samples_per_frame)
