"""SpectrogramStage — DualIQFrame → DualSpectrogramFrame.

Computes one PSD row per channel (omni + Yagi) per frame using Welch via
``src.dsp.spectrum``, and maintains a rolling history buffer per role
(``SpectrogramBuffer``) per ``docs/ARCHITECTURE_V2.md`` §4.1.

The emitted ``SpectrogramFrame`` carries the latest single time row;
downstream consumers that need history call ``buffer_for(role).snapshot()``
to get a copy of the rolling window. Keeping the per-frame message
single-row avoids 30+ MB copies on every emit while still exposing the
full buffer when needed (visualization, future cyclostationary / ML
features).
"""

from __future__ import annotations

import numpy as np

from src.dsp.spectrogram_buffer import SpectrogramBuffer
from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.pipeline.contracts import (
    ChannelRole,
    DualIQFrame,
    DualSpectrogramFrame,
    IQChannelFrame,
    SpectrogramFrame,
)
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


class SpectrogramStage(Stage[DualIQFrame, DualSpectrogramFrame]):
    """Compute paired omni/Yagi PSDs per IQ frame and maintain rolling buffers."""

    name = "spectrogram"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config

        spec_cfg = config.dsp.spectrogram
        rx = config.sdr.rx_a
        # Welch with overlap drives ~ sample_rate / (2 * fft_size) frames/sec.
        approx_frame_rate = rx.sample_rate_hz / (2 * spec_cfg.fft_size)
        history_frames = max(
            1, int(round(spec_cfg.history_ms * 1e-3 * approx_frame_rate))
        )

        self._buffers: dict[ChannelRole, SpectrogramBuffer] = {
            ChannelRole.OMNI: SpectrogramBuffer(
                num_bins=spec_cfg.fft_size, max_frames=history_frames
            ),
            ChannelRole.YAGI: SpectrogramBuffer(
                num_bins=spec_cfg.fft_size, max_frames=history_frames
            ),
        }

    @property
    def history_frames(self) -> int:
        return self._buffers[ChannelRole.OMNI].max_frames

    def buffer_for(self, role: ChannelRole) -> SpectrogramBuffer:
        """Return the rolling buffer for the given channel role.

        Snapshots are copies — safe to retain across pipeline cycles.
        """
        return self._buffers[role]

    def reset(self) -> None:
        for buf in self._buffers.values():
            buf.reset()

    async def process(self, frame: DualIQFrame) -> DualSpectrogramFrame:
        return DualSpectrogramFrame(
            frame_index=frame.frame_index,
            timestamp_s=frame.timestamp_s,
            omni=self._compute(frame.rx_a),
            yagi=self._compute(frame.rx_b),
        )

    def _compute(self, channel: IQChannelFrame) -> SpectrogramFrame:
        dsp = self.config.dsp
        fft_size = dsp.spectrogram.fft_size

        iq = remove_dc_offset(channel.iq, window=dsp.dc_offset_window)
        freq_hz, power_dbm = compute_psd(
            iq,
            sample_rate=channel.sample_rate_hz,
            fft_size=fft_size,
            window=dsp.window,
            overlap=dsp.overlap,
            center_freq=channel.center_freq_hz,
        )

        self._buffers[channel.role].append(channel.timestamp_s, power_dbm)

        return SpectrogramFrame(
            role=channel.role,
            frame_index=channel.frame_index,
            sample_rate_hz=channel.sample_rate_hz,
            center_freq_hz=channel.center_freq_hz,
            timestamps_s=np.array([channel.timestamp_s], dtype=np.float64),
            freq_hz=freq_hz,
            power_dbm=power_dbm[np.newaxis, :],
            azimuth_deg=channel.azimuth_deg,
            elevation_deg=channel.elevation_deg,
        )
