"""Tests for src.pipeline.stages.source.SourceStage."""

import asyncio

import numpy as np
import pytest

from src.pipeline.contracts import ChannelRole, DualIQFrame, IQChannelFrame
from src.pipeline.stages.source import SourceStage
from src.sdr.capture import DualIQSource


class _FakeDualSource(DualIQSource):
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.read_calls: list[int] = []
        self._frame_index = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def read(self, num_samples: int) -> DualIQFrame:
        self.read_calls.append(num_samples)
        rx_a = IQChannelFrame(
            role=ChannelRole.OMNI,
            channel_index=0,
            frame_index=self._frame_index,
            timestamp_s=float(self._frame_index),
            sample_rate_hz=30.72e6,
            center_freq_hz=2.437e9,
            antenna_port="RX2",
            iq=np.zeros(num_samples, dtype=np.complex64),
        )
        rx_b = IQChannelFrame(
            role=ChannelRole.YAGI,
            channel_index=1,
            frame_index=self._frame_index,
            timestamp_s=float(self._frame_index),
            sample_rate_hz=30.72e6,
            center_freq_hz=2.437e9,
            antenna_port="TX/RX",
            iq=np.zeros(num_samples, dtype=np.complex64),
        )
        frame = DualIQFrame(
            frame_index=self._frame_index,
            timestamp_s=float(self._frame_index),
            rx_a=rx_a,
            rx_b=rx_b,
        )
        self._frame_index += 1
        return frame


class TestSourceStage:
    def test_process_returns_dual_iq_frame(self):
        async def run():
            source = _FakeDualSource()
            stage = SourceStage(source=source, num_samples_per_frame=128)
            await stage.start()
            frame = await stage.process(None)
            await stage.stop()
            return source, frame

        source, frame = asyncio.run(run())
        assert source.started and source.stopped
        assert source.read_calls == [128]
        assert isinstance(frame, DualIQFrame)
        assert frame.rx_a.num_samples == 128

    def test_rejects_zero_samples(self):
        with pytest.raises(ValueError):
            SourceStage(source=_FakeDualSource(), num_samples_per_frame=0)
