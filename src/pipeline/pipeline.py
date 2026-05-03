"""V2 detection pipeline runner.

Composes the 8 stages of the V2 detector into a single async pipeline:

    Source → Spectrogram → Burst → Cluster → Classify ─┐
                                                       │ cue
                                                       ▼
                                                   Bearing → Fuse → Track → sinks

The Pipeline:
- Wires the stages together with explicit typed message handoffs
- Drives the antenna state machine from classifier output
- Emits at every stage so dashboards / loggers / tests can subscribe
- Logs each ``RFEvent`` as a structured JSON line (the operational sink)

This module replaces the V1 ``DualPipelineEngine``. The V1 single-channel
``PipelineEngine`` is gone — only the dual-RX path is supported.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from src.antenna.controller import AntennaController, ScanMode
from src.pipeline.contracts import (
    BearingEstimate,
    BearingRequest,
    Burst,
    Candidate,
    Classification,
    DualIQFrame,
    DualSpectrogramFrame,
    FuseRequest,
    RFEvent,
    SignalFamily,
    TrackedEmitter,
)
from src.pipeline.stages import (
    BearingStage,
    BurstStage,
    ClassifyStage,
    ClusterStage,
    FuseStage,
    SourceStage,
    SpectrogramStage,
    TrackStage,
)
from src.pipeline.stages.fuse import build_rf_event
from src.sdr.capture import DualIQSource
from src.sdr.config import SentinelConfig


logger = logging.getLogger("sentinel.pipeline")


@dataclass(frozen=True)
class PipelineFrameResult:
    """All stage outputs from one pipeline cycle.

    Holds onto everything so tests and the dashboard can inspect any
    intermediate stage without re-running the pipeline.
    """

    iq_frame: DualIQFrame
    dual_spectrogram: DualSpectrogramFrame
    bursts: tuple[Burst, ...]
    candidates: tuple[Candidate, ...]
    classifications: tuple[Classification, ...]
    bearings: tuple[BearingEstimate, ...]
    events: tuple[RFEvent, ...]
    tracks: tuple[TrackedEmitter, ...]


@dataclass
class Pipeline:
    """Async detection pipeline composed of typed stages."""

    config: SentinelConfig
    source: DualIQSource
    antenna: AntennaController | None = None
    omni_only: bool = False

    source_stage: SourceStage = field(init=False, repr=False)
    spectrogram_stage: SpectrogramStage = field(init=False, repr=False)
    burst_stage: BurstStage = field(init=False, repr=False)
    cluster_stage: ClusterStage = field(init=False, repr=False)
    classify_stage: ClassifyStage = field(init=False, repr=False)
    bearing_stage: BearingStage = field(init=False, repr=False)
    fuse_stage: FuseStage = field(init=False, repr=False)
    track_stage: TrackStage = field(init=False, repr=False)

    _running: bool = field(default=False, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _event_count: int = field(default=0, init=False, repr=False)
    _last_tick_time: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        spec_cfg = self.config.dsp.spectrogram
        # Welch with 50% overlap consumes 2 * fft_size samples per output frame.
        num_samples = 2 * spec_cfg.fft_size

        self.source_stage = SourceStage(
            source=self.source,
            num_samples_per_frame=num_samples,
        )
        self.spectrogram_stage = SpectrogramStage(self.config)
        self.burst_stage = BurstStage(self.config)
        self.cluster_stage = ClusterStage(self.config)
        self.classify_stage = ClassifyStage(self.config)
        self.bearing_stage = BearingStage(self.config)
        self.fuse_stage = FuseStage(self.config)
        self.track_stage = TrackStage(self.config)

    @property
    def stages(self) -> tuple:
        return (
            self.source_stage,
            self.spectrogram_stage,
            self.burst_stage,
            self.cluster_stage,
            self.classify_stage,
            self.bearing_stage,
            self.fuse_stage,
            self.track_stage,
        )

    @property
    def running(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def event_count(self) -> int:
        return self._event_count

    async def start(self) -> None:
        await self.source_stage.start()
        if self.antenna is not None:
            self.antenna.start()
            self.antenna.set_mode(ScanMode.SCAN)

        self._running = True
        self._frame_count = 0
        self._event_count = 0
        self._last_tick_time = time.monotonic()
        logger.info("V2 pipeline started")

    async def stop(self) -> None:
        self._running = False
        await self.source_stage.stop()
        if self.antenna is not None:
            self.antenna.stop()
        logger.info(
            "V2 pipeline stopped — %d frames, %d events",
            self._frame_count,
            self._event_count,
        )

    async def process_one_frame(self) -> PipelineFrameResult:
        # 1. Source.
        iq_frame = await self.source_stage.process(None)
        await self.source_stage.emit(iq_frame)

        # 2. Spectrogram (also appends to per-role rolling buffers).
        dual_spec = await self.spectrogram_stage.process(iq_frame)
        await self.spectrogram_stage.emit(dual_spec)

        # 3. Burst (omni).
        bursts = await self.burst_stage.process(dual_spec.omni)
        await self.burst_stage.emit(bursts)

        # 4. Cluster.
        candidates = await self.cluster_stage.process(bursts)
        await self.cluster_stage.emit(candidates)

        # 5. Classify.
        classifications = await self.classify_stage.process(candidates)
        await self.classify_stage.emit(classifications)

        if self.omni_only:
            return await self._finish_omni_only(
                iq_frame, dual_spec, bursts, candidates, classifications
            )

        # 6. Tick antenna and decide whether to enter BEARING_SEARCH.
        now = time.monotonic()
        dt = now - self._last_tick_time
        self._last_tick_time = now

        actionable = tuple(
            cls
            for cls in classifications
            if cls.protocol != SignalFamily.UNKNOWN
            and cls.confidence >= self.config.detection.min_confidence
        )
        forwarded = self._update_antenna_pre_bearing(dt, actionable)
        azimuth = (
            self.antenna.get_state().azimuth_deg if self.antenna is not None else 0.0
        )

        # 7. Bearing — cued sweep, accumulates samples across frames.
        bearing_request = BearingRequest(
            yagi_spectrogram=dual_spec.yagi,
            classifications=forwarded,
            azimuth_deg=azimuth,
        )
        bearings = await self.bearing_stage.process(bearing_request)
        await self.bearing_stage.emit(bearings)

        # 8. Bearing emitted → ALARM(peak), antenna will settle into TRACK.
        if bearings and self.antenna is not None:
            best = max(bearings, key=lambda b: b.confidence)
            self.antenna.alarm(best.bearing_deg)

        # 9. Fuse — joins pending classifications with arriving bearings.
        fuse_request = FuseRequest(
            frame_index=iq_frame.frame_index,
            timestamp_s=iq_frame.timestamp_s,
            classifications=forwarded,
            bearings=bearings,
        )
        events = await self.fuse_stage.process(fuse_request)
        await self.fuse_stage.emit(events)

        # 10. Track.
        tracks = await self.track_stage.process(events)
        await self.track_stage.emit(tracks)

        # Sinks.
        for event in events:
            self._event_count += 1
            self._log_event(event)

        self._frame_count += 1
        return PipelineFrameResult(
            iq_frame=iq_frame,
            dual_spectrogram=dual_spec,
            bursts=bursts,
            candidates=candidates,
            classifications=classifications,
            bearings=bearings,
            events=events,
            tracks=tracks,
        )

    async def run(self, max_frames: int = 0) -> None:
        """Process frames until stopped or until ``max_frames`` is reached."""
        frame = 0
        while self._running:
            await self.process_one_frame()
            frame += 1
            if max_frames > 0 and frame >= max_frames:
                break
            await asyncio.sleep(0)

    # ---- internals ------------------------------------------------------

    async def _finish_omni_only(
        self,
        iq_frame: DualIQFrame,
        dual_spec: DualSpectrogramFrame,
        bursts: tuple[Burst, ...],
        candidates: tuple[Candidate, ...],
        classifications: tuple[Classification, ...],
    ) -> PipelineFrameResult:
        """Tripwire-only completion: skip antenna/bearing/fuse, build events directly.

        Used when only the omni is connected. Each actionable classification
        becomes an RFEvent with bearing_deg=None; tracks are still maintained
        so repeated detections at the same frequency cluster into one emitter.
        """
        actionable = tuple(
            cls
            for cls in classifications
            if cls.protocol != SignalFamily.UNKNOWN
            and cls.confidence >= self.config.detection.min_confidence
        )
        events = tuple(
            build_rf_event(cls, frame_index=iq_frame.frame_index)
            for cls in actionable
        )
        bearings: tuple[BearingEstimate, ...] = ()

        # Emit on the same channels so dashboards/loggers see a uniform stream.
        await self.bearing_stage.emit(bearings)
        await self.fuse_stage.emit(events)

        tracks = await self.track_stage.process(events)
        await self.track_stage.emit(tracks)

        for event in events:
            self._event_count += 1
            self._log_event(event)

        self._frame_count += 1
        return PipelineFrameResult(
            iq_frame=iq_frame,
            dual_spectrogram=dual_spec,
            bursts=bursts,
            candidates=candidates,
            classifications=classifications,
            bearings=bearings,
            events=events,
            tracks=tracks,
        )

    def _update_antenna_pre_bearing(
        self,
        dt: float,
        actionable: tuple[Classification, ...],
    ) -> tuple[Classification, ...]:
        """Tick antenna and decide which classifications to forward to bearing.

        State transitions implemented (matches ARCHITECTURE_V2.md §4.4):
          IDLE/SCAN/CUE → BEARING_SEARCH on actionable classification
          BEARING_SEARCH → ALARM (handled in caller after bearing emits)
          ALARM → TRACK (handled inside SimulatedController.tick)
          TRACK refreshes timer on continued classifications

        Returns the classifications forwarded to BearingStage. Classifications
        received while in TRACK or ALARM only refresh the track timer; they
        are not forwarded so a new sweep doesn't start mid-track.
        """
        if self.antenna is None:
            return actionable

        self.antenna.tick(dt)
        state = self.antenna.get_state()

        if not actionable:
            return ()

        if state.mode in (ScanMode.IDLE, ScanMode.SCAN, ScanMode.CUE):
            self.antenna.start_bearing_search()
            return actionable
        if state.mode == ScanMode.BEARING_SEARCH:
            return actionable
        if state.mode == ScanMode.TRACK and hasattr(self.antenna, "refresh_track"):
            self.antenna.refresh_track()
        return ()

    def _log_event(self, event: RFEvent) -> None:
        payload = {
            "event": "detection",
            "timestamp": time.time(),
            "frame": self._frame_count,
            "event_id": event.event_id,
            "freq_hz": event.center_freq_hz,
            "bandwidth_hz": event.bandwidth_hz,
            "peak_power_dbm": event.peak_power_dbm,
            "snr_db": event.snr_db,
            "family": event.family.value,
            "bearing_deg": event.bearing_deg,
            "channel_role": event.role.value,
        }
        if self.antenna is not None:
            state = self.antenna.get_state()
            payload["azimuth_deg"] = state.azimuth_deg
            payload["antenna_mode"] = state.mode.value
        logger.info(json.dumps(payload))


__all__ = ["Pipeline", "PipelineFrameResult"]
