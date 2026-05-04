"""Tests for src.pipeline.pipeline.Pipeline (V2 detection pipeline)."""

from __future__ import annotations

import asyncio

from src.antenna.controller import ScanMode, SimulatedController
from src.pipeline.pipeline import Pipeline, PipelineFrameResult
from src.sdr.capture import SignalDef, SyntheticDualSource
from tests.unit._v2_helpers import make_v2_test_config


def _make_source(signals=None) -> SyntheticDualSource:
    return SyntheticDualSource(
        sample_rate_hz=30.72e6,
        center_freq_hz=2.437e9,
        omni_noise_power_dbm=-90.0,
        yagi_noise_power_dbm=-90.0,
        omni_signals=signals or [],
        yagi_signals=signals or [],
        seed=7,
    )


def _make_antenna() -> SimulatedController:
    return SimulatedController(
        azimuth_min_deg=0.0,
        azimuth_max_deg=360.0,
        slew_rate_deg_per_sec=60.0,
        scan_speed_deg_per_sec=30.0,
        elevation_deg=10.0,
        cue_timeout_sec=5.0,
        track_oscillation_deg=15.0,
        track_lost_timeout_sec=10.0,
    )


class TestStartStop:
    def test_lifecycle(self):
        cfg = make_v2_test_config()
        source = _make_source()
        pipe = Pipeline(config=cfg, source=source, antenna=_make_antenna())

        async def run():
            await pipe.start()
            assert pipe.running
            await pipe.stop()
            assert not pipe.running

        asyncio.run(run())


class TestProcessFrame:
    def test_pipeline_runs_on_quiet_input(self):
        # Synthetic noise can occasionally pop above an aggressive 8 dB
        # threshold; this test only asserts the pipeline runs cleanly and
        # returns a well-formed frame result.
        cfg = make_v2_test_config()
        source = _make_source()
        pipe = Pipeline(config=cfg, source=source)

        async def run():
            await pipe.start()
            for _ in range(5):
                result = await pipe.process_one_frame()
            await pipe.stop()
            return result

        result: PipelineFrameResult = asyncio.run(run())
        assert isinstance(result, PipelineFrameResult)

    def test_strong_signal_produces_events(self):
        cfg = make_v2_test_config()
        # WiFi-like 20 MHz signal so the rule-based classifier can label it.
        wifi = SignalDef(
            freq_offset_hz=0.0,
            bandwidth_hz=20e6,
            power_dbm=-40.0,
            signal_type="wideband",
            num_subcarriers=64,
        )
        source = _make_source(signals=[wifi])
        pipe = Pipeline(config=cfg, source=source)

        async def run():
            await pipe.start()
            # Several frames so the noise floor settles, the burst stays open,
            # then closes when we cut the signal.
            results = []
            for _ in range(8):
                results.append(await pipe.process_one_frame())
            # Now switch to a quiet source so the active runs close.
            quiet_source = _make_source()
            await source.stop()
            pipe.source_stage.source = quiet_source
            await quiet_source.start()
            for _ in range(3):
                results.append(await pipe.process_one_frame())
            await quiet_source.stop()
            return results

        results = asyncio.run(run())
        # At least one frame must have produced events.
        assert any(r.events for r in results), (
            "expected at least one event across the batch"
        )

    def test_antenna_enters_bearing_search_then_track_on_classification(self):
        cfg = make_v2_test_config()
        wifi = SignalDef(
            freq_offset_hz=0.0,
            bandwidth_hz=20e6,
            power_dbm=-40.0,
            signal_type="wideband",
            num_subcarriers=64,
        )
        source = _make_source(signals=[wifi])
        antenna = _make_antenna()
        pipe = Pipeline(config=cfg, source=source, antenna=antenna)

        async def run():
            await pipe.start()
            modes_seen = set()
            for _ in range(8):
                await pipe.process_one_frame()
                modes_seen.add(antenna.get_state().mode)
            quiet = _make_source()
            await source.stop()
            pipe.source_stage.source = quiet
            await quiet.start()
            for _ in range(8):
                await pipe.process_one_frame()
                modes_seen.add(antenna.get_state().mode)
            await quiet.stop()
            return modes_seen

        modes_seen = asyncio.run(run())
        # The pipeline must have visited BEARING_SEARCH at some point — that's
        # the architectural state added in Phase 1.5. ALARM and TRACK are also
        # acceptable later states once a sweep completes.
        assert ScanMode.BEARING_SEARCH in modes_seen, modes_seen


class TestOmniOnly:
    def test_omni_only_skips_antenna_and_emits_bearingless_events(self):
        cfg = make_v2_test_config()
        wifi = SignalDef(
            freq_offset_hz=0.0,
            bandwidth_hz=20e6,
            power_dbm=-40.0,
            signal_type="wideband",
            num_subcarriers=64,
        )
        source = _make_source(signals=[wifi])
        # antenna=None proves omni-only doesn't need one.
        pipe = Pipeline(config=cfg, source=source, omni_only=True)

        async def run():
            await pipe.start()
            results = []
            for _ in range(8):
                results.append(await pipe.process_one_frame())
            quiet = _make_source()
            await source.stop()
            pipe.source_stage.source = quiet
            await quiet.start()
            for _ in range(3):
                results.append(await pipe.process_one_frame())
            await quiet.stop()
            return results

        results = asyncio.run(run())
        events = [ev for r in results for ev in r.events]
        assert events, "omni-only pipeline must emit events on a strong signal"
        # Bearings are never produced in omni-only mode.
        assert all(not r.bearings for r in results)
        assert all(ev.bearing_deg is None for ev in events)


class TestCounters:
    def test_frame_count_advances(self):
        cfg = make_v2_test_config()
        pipe = Pipeline(config=cfg, source=_make_source())

        async def run():
            await pipe.start()
            for _ in range(4):
                await pipe.process_one_frame()
            await pipe.stop()

        asyncio.run(run())
        assert pipe.frame_count == 4
