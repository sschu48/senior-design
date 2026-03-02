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
from src.dsp.detector import CFARDetector, Detection, TripwireDetector
from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.sdr.capture import IQSource

if TYPE_CHECKING:
    from src.sdr.config import SentinelConfig


logger = logging.getLogger("sentinel.pipeline")


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
        cfg = self.config
        dsp = cfg.dsp
        rx = cfg.sdr.rx_a

        # Tripwire detector (omni channel)
        tw = dsp.tripwire
        # frames_per_sec ≈ sample_rate / fft_size
        frames_per_sec = rx.sample_rate_hz / dsp.fft_size
        noise_floor_frames = max(2, int(tw.noise_floor_window_sec * frames_per_sec))
        min_trigger_frames = max(1, int(
            tw.min_trigger_duration_ms / 1000.0 * frames_per_sec
        ))

        self._tripwire = TripwireDetector(
            threshold_db=tw.threshold_db,
            noise_floor_frames=noise_floor_frames,
            min_trigger_frames=min_trigger_frames,
            sample_rate_hz=rx.sample_rate_hz,
            center_freq_hz=rx.center_freq_hz,
            fft_size=dsp.fft_size,
        )

        # CFAR detector (yagi channel)
        cf = dsp.cfar
        self._cfar = CFARDetector(
            guard_cells=cf.guard_cells,
            reference_cells=cf.reference_cells,
            threshold_factor_db=cf.threshold_factor_db,
            min_detection_bw_hz=cf.min_detection_bw_hz,
            sample_rate_hz=rx.sample_rate_hz,
            center_freq_hz=rx.center_freq_hz,
            fft_size=dsp.fft_size,
        )

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
        all_detections = self._deduplicate(all_detections)

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

    def _deduplicate(self, detections: list[Detection]) -> list[Detection]:
        """Remove overlapping detections, keeping the one with higher SNR."""
        if len(detections) <= 1:
            return detections

        # Sort by SNR descending
        detections.sort(key=lambda d: d.snr_db, reverse=True)
        kept: list[Detection] = []

        for d in detections:
            overlaps = False
            for k in kept:
                # Check bin overlap
                if d.bin_start <= k.bin_end and d.bin_end >= k.bin_start:
                    overlaps = True
                    break
            if not overlaps:
                kept.append(d)

        return kept

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

    def _log_detection(self, d: Detection) -> None:
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

        if self.antenna is not None:
            state = self.antenna.get_state()
            event["azimuth_deg"] = state.azimuth_deg
            event["antenna_mode"] = state.mode.value

        logger.info(json.dumps(event))
