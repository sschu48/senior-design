"""SENTINEL — Web Dashboard Server.

aiohttp WebSocket server that streams real-time PSD data to browser clients.
Replaces matplotlib spectrum display for headless/remote operation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from aiohttp import web

from src.dsp.detector import CFARDetector, Detection, TripwireDetector
from src.dsp.spectrum import compute_psd_from_config

if TYPE_CHECKING:
    from src.sdr.capture import IQSource
    from src.sdr.config import SentinelConfig

logger = logging.getLogger("sentinel.dashboard")

STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class DashboardServer:
    """WebSocket server that broadcasts PSD frames to connected browsers.

    Parameters
    ----------
    source : IQSource
        IQ sample source (SyntheticSource or USRPSource).
    config : SentinelConfig
        Loaded SENTINEL configuration.
    fps : int
        Target frames per second for PSD updates.
    host : str
        Bind address.
    port : int
        Bind port.
    """

    source: IQSource
    config: SentinelConfig
    fps: int = 15
    host: str = "0.0.0.0"
    port: int = 3000
    enable_detections: bool = False

    _clients: set[web.WebSocketResponse] = field(default_factory=set, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _freq_mhz: list[float] = field(default_factory=list, init=False, repr=False)
    _tripwire: TripwireDetector | None = field(default=None, init=False, repr=False)
    _cfar: CFARDetector | None = field(default=None, init=False, repr=False)
    _detection_count: int = field(default=0, init=False, repr=False)

    async def start(self) -> None:
        """Start the IQ source, aiohttp app, and broadcast loop."""
        await self.source.start()

        # Pre-compute frequency axis (sent once per client on connect)
        iq = await self.source.read(self.config.dsp.fft_size)
        freq_hz, _ = compute_psd_from_config(iq, self.config)
        self._freq_mhz = (freq_hz / 1e6).tolist()

        # Initialize detectors if enabled
        if self.enable_detections:
            self._init_detectors()

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()

        logger.info("Dashboard listening on http://%s:%d", self.host, self.port)

        try:
            await self._broadcast_loop()
        finally:
            await self.source.stop()
            await runner.cleanup()

    async def _handle_index(self, _request: web.Request) -> web.Response:
        """Serve the single-page dashboard HTML."""
        index_path = STATIC_DIR / "index.html"
        return web.FileResponse(index_path)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """Handle a new WebSocket connection."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Send config on connect so client knows the frequency axis
        config_msg = json.dumps({
            "type": "config",
            "freq_mhz": self._freq_mhz,
            "center_mhz": self.config.sdr.rx_a.center_freq_hz / 1e6,
            "rate_msps": self.config.sdr.rx_a.sample_rate_hz / 1e6,
            "fft_size": self.config.dsp.fft_size,
        })
        await ws.send_str(config_msg)

        self._clients.add(ws)
        logger.info("Client connected (%d total)", len(self._clients))

        try:
            async for _msg in ws:
                pass  # clients don't send data; just keep connection alive
        finally:
            self._clients.discard(ws)
            logger.info("Client disconnected (%d remaining)", len(self._clients))

        return ws

    def _init_detectors(self) -> None:
        """Initialize tripwire + CFAR detectors (same as PipelineEngine.start)."""
        cfg = self.config
        dsp = cfg.dsp
        rx = cfg.sdr.rx_a

        frames_per_sec = rx.sample_rate_hz / dsp.fft_size

        tw = dsp.tripwire
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

        self._detection_count = 0
        logger.info("Detection overlay enabled (tripwire + CFAR)")

    async def _broadcast_loop(self) -> None:
        """Read IQ → compute PSD → broadcast to all connected clients."""
        num_samples = self.config.dsp.fft_size
        frame_interval = 1.0 / self.fps

        while True:
            t_start = time.monotonic()

            try:
                iq = await self.source.read(num_samples)
                freq_hz, power_dbm = compute_psd_from_config(iq, self.config)
            except Exception:
                logger.exception("PSD computation failed")
                await asyncio.sleep(frame_interval)
                continue

            self._frame_count += 1

            # Compute frame stats
            peak_idx = int(np.argmax(power_dbm))
            peak_freq_mhz = float(freq_hz[peak_idx] / 1e6)
            peak_power_dbm = float(power_dbm[peak_idx])
            noise_floor_dbm = float(np.median(power_dbm))

            msg_data: dict = {
                "type": "psd",
                "power_dbm": np.round(power_dbm, 2).tolist(),
                "frame": self._frame_count,
                "peak_freq_mhz": round(peak_freq_mhz, 3),
                "peak_power_dbm": round(peak_power_dbm, 1),
                "noise_floor_dbm": round(noise_floor_dbm, 1),
            }

            # Run detectors if enabled
            if self.enable_detections and self._tripwire and self._cfar:
                dets: list[Detection] = []
                dets.extend(self._tripwire.process(power_dbm, freq_hz))
                dets.extend(self._cfar.process(power_dbm, freq_hz))
                if dets:
                    self._detection_count += len(dets)
                    msg_data["detections"] = [
                        {
                            "freq_mhz": round(d.freq_hz / 1e6, 3),
                            "bandwidth_mhz": round(d.bandwidth_hz / 1e6, 3),
                            "power_dbm": round(d.power_dbm, 1),
                            "snr_db": round(d.snr_db, 1),
                            "bin_start": d.bin_start,
                            "bin_end": d.bin_end,
                        }
                        for d in dets
                    ]
                    msg_data["detection_count"] = self._detection_count

            msg = json.dumps(msg_data)

            # Broadcast to all clients, remove dead ones
            dead = set()
            for ws in self._clients:
                try:
                    await ws.send_str(msg)
                except (ConnectionError, RuntimeError):
                    dead.add(ws)
            self._clients -= dead

            # Accurate FPS throttle
            elapsed = time.monotonic() - t_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
