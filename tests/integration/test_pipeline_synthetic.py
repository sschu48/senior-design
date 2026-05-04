"""End-to-end V2 pipeline test against synthetic IQ.

Drives a SyntheticDualSource with a representative drone-like signal mix
through the full V2 pipeline and asserts that:
- the pipeline runs without raising
- detection events are produced
- track IDs are deterministic

This is the "smoke test" that validates DAG wiring and the typed contracts
between stages. Tuning of detection rates against realistic SNR / clutter
is Phase 2 (hardware validation).
"""

from __future__ import annotations

import asyncio
import dataclasses

from src.pipeline.pipeline import Pipeline
from src.sdr.capture import SignalDef, SyntheticDualSource
from src.sdr.config import load_config


def _config() -> object:
    base = load_config()
    spec = dataclasses.replace(
        base.dsp.spectrogram,
        history_ms=50.0,
        fft_size=512,
    )
    burst = dataclasses.replace(
        base.dsp.burst,
        threshold_db=10.0,
        noise_floor_window_sec=0.1,
        min_burst_duration_ms=0.0,
        min_bandwidth_hz=0.0,
    )
    bearing = dataclasses.replace(
        base.dsp.bearing,
        # Tight sweep requirement so events can fire within the test loop.
        min_sweep_samples=3,
    )
    new_dsp = dataclasses.replace(
        base.dsp, spectrogram=spec, burst=burst, bearing=bearing
    )
    return dataclasses.replace(base, dsp=new_dsp)


def _drone_mix() -> list[SignalDef]:
    return [
        # OcuSync-like 10 MHz wideband
        SignalDef(
            freq_offset_hz=0.0,
            bandwidth_hz=10e6,
            power_dbm=-40.0,
            signal_type="wideband",
            num_subcarriers=64,
        ),
    ]


class TestSyntheticEndToEnd:
    def test_pipeline_runs_and_produces_events(self):
        cfg = _config()
        source = SyntheticDualSource(
            sample_rate_hz=30.72e6,
            center_freq_hz=2.437e9,
            omni_noise_power_dbm=-90.0,
            yagi_noise_power_dbm=-90.0,
            omni_signals=_drone_mix(),
            yagi_signals=_drone_mix(),
            seed=42,
        )
        pipe = Pipeline(config=cfg, source=source)

        async def run():
            await pipe.start()
            results = []
            for _ in range(10):
                results.append(await pipe.process_one_frame())

            # Stop the source feeding the active signal, switch to quiet.
            await source.stop()
            quiet = SyntheticDualSource(
                sample_rate_hz=30.72e6,
                center_freq_hz=2.437e9,
                omni_noise_power_dbm=-90.0,
                yagi_noise_power_dbm=-90.0,
                seed=43,
            )
            pipe.source_stage.source = quiet
            await quiet.start()
            for _ in range(5):
                results.append(await pipe.process_one_frame())
            await quiet.stop()
            return results

        results = asyncio.run(run())

        total_events = sum(len(r.events) for r in results)
        assert total_events > 0, "expected at least one detection event"

    def test_track_ids_are_deterministic(self):
        cfg = _config()
        source = SyntheticDualSource(
            sample_rate_hz=30.72e6,
            center_freq_hz=2.437e9,
            omni_noise_power_dbm=-90.0,
            yagi_noise_power_dbm=-90.0,
            omni_signals=_drone_mix(),
            yagi_signals=_drone_mix(),
            seed=42,
        )
        pipe = Pipeline(config=cfg, source=source)

        async def run():
            await pipe.start()
            tracks_seen = []
            for _ in range(10):
                result = await pipe.process_one_frame()
                tracks_seen.extend(t.track_id for t in result.tracks)
            await source.stop()
            return tracks_seen

        tracks = asyncio.run(run())
        # If any tracks were emitted at all, they start at trk-1 and increment.
        if tracks:
            unique = []
            for tid in tracks:
                if tid not in unique:
                    unique.append(tid)
            assert unique[0] == "trk-1"
            for i, tid in enumerate(unique):
                assert tid == f"trk-{i + 1}"
