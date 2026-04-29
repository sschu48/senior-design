"""SENTINEL detection pipeline engine.

Wires IQ source → PSD → detectors → antenna control → logging into a
single async processing loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from src.antenna.controller import AntennaController, ScanMode
from src.dsp.detector import Detection, create_detectors, deduplicate
from src.dsp.events import RFEventTracker, detection_to_event
from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.pipeline.contracts import (
    ChannelRole,
    DualIQFrame,
    IQChannelFrame,
    PSDFrame,
    RFEvent,
    TrackedEmitter,
)
from src.sdr.capture import DualIQSource, IQSource

if TYPE_CHECKING:
    from src.sdr.config import SentinelConfig


logger = logging.getLogger("sentinel.pipeline")


@dataclass(frozen=True)
class DualPipelineFrameResult:
    """Processing result for one dual-RX frame."""

    frame: DualIQFrame
    omni_psd: PSDFrame
    yagi_psd: PSDFrame
    tripwire_detections: tuple[Detection, ...]
    cfar_detections: tuple[Detection, ...]
    rf_events: tuple[RFEvent, ...] = field(default_factory=tuple)
    tracks: tuple[TrackedEmitter, ...] = field(default_factory=tuple)

    @property
    def all_detections(self) -> tuple[Detection, ...]:
        return self.tripwire_detections + self.cfar_detections


@dataclass
class PipelineEngine:
    """Async pipeline: source → PSD → detect → antenna → log.

    Parameters
    ----------
    config : SentinelConfig
        Full system configuration.
    source : IQSource
        IQ sample source (SyntheticSource or hardware).
    antenna : AntennaController or None
        Antenna controller.  If None, antenna logic is skipped.
    """

    config: SentinelConfig
    source: IQSource
    antenna: AntennaController | None = None

    # Internal state
    _tripwire: TripwireDetector | None = field(default=None, init=False, repr=False)
    _cfar: CFARDetector | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _detection_count: int = field(default=0, init=False, repr=False)
    _last_tick_time: float = field(default=0.0, init=False, repr=False)

    async def start(self) -> None:
        """Initialize detectors and start the source + antenna."""
        self._tripwire, self._cfar = create_detectors(self.config)

        await self.source.start()

        if self.antenna is not None:
            self.antenna.start()
            self.antenna.set_mode(ScanMode.SCAN)

        self._running = True
        self._frame_count = 0
        self._detection_count = 0
        self._last_tick_time = time.monotonic()

        logger.info("Pipeline started")

    async def stop(self) -> None:
        """Stop the pipeline."""
        self._running = False
        await self.source.stop()
        if self.antenna is not None:
            self.antenna.stop()
        logger.info(
            "Pipeline stopped — %d frames, %d detections",
            self._frame_count,
            self._detection_count,
        )

    @property
    def running(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def detection_count(self) -> int:
        return self._detection_count

    async def process_one_frame(self) -> list[Detection]:
        """Read IQ, compute PSD, run detectors, update antenna, log.

        Returns all detections from this frame.
        """
        cfg = self.config
        dsp = cfg.dsp
        rx = cfg.sdr.rx_a

        # Read one FFT frame worth of IQ samples
        num_samples = dsp.fft_size * 2  # 2x for Welch overlap
        iq = await self.source.read(num_samples)

        # DC removal
        iq = remove_dc_offset(iq, window=dsp.dc_offset_window)

        # PSD
        freq_hz, psd_dbm = compute_psd(
            iq,
            sample_rate=rx.sample_rate_hz,
            fft_size=dsp.fft_size,
            window=dsp.window,
            overlap=dsp.overlap,
            center_freq=rx.center_freq_hz,
        )

        # Run detectors
        all_detections: list[Detection] = []

        if self._tripwire is not None:
            all_detections.extend(self._tripwire.process(psd_dbm, freq_hz))

        if self._cfar is not None:
            all_detections.extend(self._cfar.process(psd_dbm, freq_hz))

        # Deduplicate: if both detectors flag overlapping bins, keep the
        # one with higher SNR
        all_detections = deduplicate(all_detections)

        # Update antenna state
        now = time.monotonic()
        dt = now - self._last_tick_time
        self._last_tick_time = now

        if self.antenna is not None:
            self._update_antenna(all_detections, dt)

        # Log detections
        for d in all_detections:
            self._detection_count += 1
            self._log_detection(d)

        self._frame_count += 1
        return all_detections

    async def run(self, max_frames: int = 0) -> None:
        """Run the processing loop.

        Parameters
        ----------
        max_frames : int
            Stop after this many frames.  0 means run until stopped.
        """
        frame = 0
        while self._running:
            await self.process_one_frame()
            frame += 1
            if max_frames > 0 and frame >= max_frames:
                break
            # Yield to event loop
            await asyncio.sleep(0)

    def _update_antenna(self, detections: list[Detection], dt: float) -> None:
        """Update antenna mode based on detections."""
        if self.antenna is None:
            return

        self.antenna.tick(dt)
        state = self.antenna.get_state()

        if detections:
            # Pick strongest detection
            best = max(detections, key=lambda d: d.snr_db)
            # Estimate bearing from frequency → map to antenna position
            # In a real system this comes from the directional channel.
            # For now, use a placeholder: detection exists → cue/track.
            bearing = state.azimuth_deg  # keep current bearing as estimate

            if state.mode == ScanMode.SCAN:
                self.antenna.cue_to(bearing)
            elif state.mode == ScanMode.CUE:
                self.antenna.start_track(bearing)
            elif state.mode == ScanMode.TRACK:
                self.antenna.refresh_track()
        # No detections: let timeouts handle mode transitions

    def _log_detection(
        self,
        d: Detection,
        *,
        detector: str | None = None,
        role: ChannelRole | None = None,
    ) -> None:
        """Emit a structured JSON log for a detection event."""
        event = {
            "event": "detection",
            "timestamp": time.time(),
            "freq_hz": d.freq_hz,
            "bandwidth_hz": d.bandwidth_hz,
            "power_dbm": d.power_dbm,
            "snr_db": d.snr_db,
            "bin_start": d.bin_start,
            "bin_end": d.bin_end,
            "frame": self._frame_count,
        }
        if detector is not None:
            event["detector"] = detector
        if role is not None:
            event["channel_role"] = role.value

        if self.antenna is not None:
            state = self.antenna.get_state()
            event["azimuth_deg"] = state.azimuth_deg
            event["antenna_mode"] = state.mode.value

        logger.info(json.dumps(event))


@dataclass
class DualPipelineEngine:
    """Async dual-RX pipeline: omni tripwire + Yagi CFAR paths."""

    config: SentinelConfig
    source: DualIQSource
    antenna: AntennaController | None = None
    event_tracker: RFEventTracker = field(default_factory=RFEventTracker)

    _tripwire: TripwireDetector | None = field(default=None, init=False, repr=False)
    _cfar: CFARDetector | None = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _detection_count: int = field(default=0, init=False, repr=False)
    _last_tick_time: float = field(default=0.0, init=False, repr=False)

    async def start(self) -> None:
        self._tripwire, self._cfar = create_detectors(self.config)

        await self.source.start()

        if self.antenna is not None:
            self.antenna.start()
            self.antenna.set_mode(ScanMode.SCAN)

        self._running = True
        self._frame_count = 0
        self._detection_count = 0
        self._last_tick_time = time.monotonic()
        self.event_tracker.reset()
        logger.info("Dual pipeline started")

    async def stop(self) -> None:
        self._running = False
        await self.source.stop()
        if self.antenna is not None:
            self.antenna.stop()
        logger.info(
            "Dual pipeline stopped — %d frames, %d detections",
            self._frame_count,
            self._detection_count,
        )

    @property
    def running(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def detection_count(self) -> int:
        return self._detection_count

    async def process_one_frame(self) -> DualPipelineFrameResult:
        """Process one synchronized omni/Yagi frame."""
        cfg = self.config
        dsp = cfg.dsp
        num_samples = dsp.fft_size * 2

        frame = await self.source.read(num_samples)
        omni_psd = self._compute_psd_frame(frame.rx_a)
        yagi_psd = self._compute_psd_frame(frame.rx_b)

        tripwire_detections: list[Detection] = []
        cfar_detections: list[Detection] = []

        if self._tripwire is not None:
            tripwire_detections = deduplicate(
                self._tripwire.process(omni_psd.power_dbm, omni_psd.freq_hz)
            )

        if self._cfar is not None:
            cfar_detections = deduplicate(
                self._cfar.process(yagi_psd.power_dbm, yagi_psd.freq_hz)
            )

        now = time.monotonic()
        dt = now - self._last_tick_time
        self._last_tick_time = now

        if self.antenna is not None:
            # Only directional Yagi detections can support a bearing update.
            self._update_antenna(cfar_detections, dt)

        rf_events = self._build_events(
            frame=frame,
            yagi_psd=yagi_psd,
            tripwire_detections=tripwire_detections,
            cfar_detections=cfar_detections,
        )
        tracks = tuple(self.event_tracker.process(rf_events))

        for d in tripwire_detections:
            self._detection_count += 1
            self._log_detection(
                d,
                detector="tripwire",
                role=ChannelRole.OMNI,
            )
        for d in cfar_detections:
            self._detection_count += 1
            self._log_detection(
                d,
                detector="cfar",
                role=ChannelRole.YAGI,
            )

        self._frame_count += 1
        return DualPipelineFrameResult(
            frame=frame,
            omni_psd=omni_psd,
            yagi_psd=yagi_psd,
            tripwire_detections=tuple(tripwire_detections),
            cfar_detections=tuple(cfar_detections),
            rf_events=tuple(rf_events),
            tracks=tracks,
        )

    async def run(self, max_frames: int = 0) -> None:
        frame = 0
        while self._running:
            await self.process_one_frame()
            frame += 1
            if max_frames > 0 and frame >= max_frames:
                break
            await asyncio.sleep(0)

    def _compute_psd_frame(self, channel: IQChannelFrame) -> PSDFrame:
        iq = remove_dc_offset(channel.iq, window=self.config.dsp.dc_offset_window)
        freq_hz, power_dbm = compute_psd(
            iq,
            sample_rate=channel.sample_rate_hz,
            fft_size=self.config.dsp.fft_size,
            window=self.config.dsp.window,
            overlap=self.config.dsp.overlap,
            center_freq=channel.center_freq_hz,
        )
        return PSDFrame(
            role=channel.role,
            frame_index=channel.frame_index,
            timestamp_s=channel.timestamp_s,
            sample_rate_hz=channel.sample_rate_hz,
            center_freq_hz=channel.center_freq_hz,
            freq_hz=freq_hz,
            power_dbm=power_dbm,
            azimuth_deg=channel.azimuth_deg,
            elevation_deg=channel.elevation_deg,
        )

    def _update_antenna(self, detections: list[Detection], dt: float) -> None:
        if self.antenna is None:
            return

        self.antenna.tick(dt)
        state = self.antenna.get_state()

        if detections:
            bearing = state.azimuth_deg

            if state.mode == ScanMode.SCAN:
                self.antenna.cue_to(bearing)
            elif state.mode == ScanMode.CUE:
                self.antenna.start_track(bearing)
            elif state.mode == ScanMode.TRACK:
                self.antenna.refresh_track()

    def _log_detection(
        self,
        d: Detection,
        *,
        detector: str,
        role: ChannelRole,
    ) -> None:
        event = {
            "event": "detection",
            "timestamp": time.time(),
            "freq_hz": d.freq_hz,
            "bandwidth_hz": d.bandwidth_hz,
            "power_dbm": d.power_dbm,
            "snr_db": d.snr_db,
            "bin_start": d.bin_start,
            "bin_end": d.bin_end,
            "frame": self._frame_count,
            "detector": detector,
            "channel_role": role.value,
        }

        if self.antenna is not None:
            state = self.antenna.get_state()
            event["azimuth_deg"] = state.azimuth_deg
            event["antenna_mode"] = state.mode.value

        logger.info(json.dumps(event))

    def _build_events(
        self,
        *,
        frame: DualIQFrame,
        yagi_psd: PSDFrame,
        tripwire_detections: list[Detection],
        cfar_detections: list[Detection],
    ) -> list[RFEvent]:
        events: list[RFEvent] = []

        for d in tripwire_detections:
            events.append(
                detection_to_event(
                    d,
                    role=ChannelRole.OMNI,
                    frame_index=frame.frame_index,
                    timestamp_s=frame.timestamp_s,
                    source="tripwire",
                )
            )

        for d in cfar_detections:
            events.append(
                detection_to_event(
                    d,
                    role=ChannelRole.YAGI,
                    frame_index=frame.frame_index,
                    timestamp_s=frame.timestamp_s,
                    source="cfar",
                    bearing_deg=yagi_psd.azimuth_deg,
                )
            )

        return events
