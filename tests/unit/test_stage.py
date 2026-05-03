"""Tests for the V2 Stage abstraction (src.pipeline.stage)."""

from __future__ import annotations

import asyncio

import pytest

from src.pipeline.stage import Stage


class _Doubler(Stage[int, int]):
    name = "doubler"

    async def process(self, msg: int) -> int:
        return msg * 2


class _StatefulCounter(Stage[int, int]):
    name = "counter"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def process(self, msg: int) -> int:
        self.calls += 1
        return self.calls

    def reset(self) -> None:
        self.calls = 0


class TestStageProcess:
    def test_default_process_raises(self):
        stage: Stage[int, int] = Stage()
        with pytest.raises(NotImplementedError, match="process"):
            asyncio.run(stage.process(1))

    def test_subclass_process(self):
        stage = _Doubler()
        assert asyncio.run(stage.process(3)) == 6


class TestStageSubscribe:
    def test_subscribe_returns_queue_that_receives_emits(self):
        async def run():
            stage = _Doubler()
            q = stage.subscribe()
            assert stage.subscriber_count == 1
            await stage.emit(42)
            return q.get_nowait()

        assert asyncio.run(run()) == 42

    def test_multiple_subscribers_each_receive(self):
        async def run():
            stage = _Doubler()
            q1 = stage.subscribe()
            q2 = stage.subscribe()
            await stage.emit("hello")
            return q1.get_nowait(), q2.get_nowait()

        assert asyncio.run(run()) == ("hello", "hello")

    def test_full_queue_drops_oldest(self):
        async def run():
            stage = _Doubler()
            stage.subscriber_queue_maxsize = 2
            q = stage.subscribe()
            await stage.emit(1)
            await stage.emit(2)
            # third emit should drop the oldest
            await stage.emit(3)
            return [q.get_nowait() for _ in range(2)]

        # Oldest (1) was dropped, so we get 2 then 3.
        assert asyncio.run(run()) == [2, 3]

    def test_emit_with_no_subscribers_is_noop(self):
        async def run():
            stage = _Doubler()
            # Should not raise.
            await stage.emit(99)

        asyncio.run(run())


class TestStageReset:
    def test_default_reset_is_noop(self):
        stage: Stage[int, int] = Stage()
        # Should not raise.
        stage.reset()

    def test_subclass_reset_clears_state(self):
        async def run():
            stage = _StatefulCounter()
            await stage.process(0)
            await stage.process(0)
            assert stage.calls == 2
            stage.reset()
            assert stage.calls == 0

        asyncio.run(run())
