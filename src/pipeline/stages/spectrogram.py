"""SpectrogramStage — DualIQFrame → DualSpectrogramFrame.

Computes one PSD row per channel (omni + Yagi) per frame using Welch via
``src.dsp.spectrum``. The emitted ``SpectrogramFrame`` carries a single
time row; downstream stages that need rolling history maintain it
themselves (e.g. the dashboard).
"""

from __future__ import annotations

import numpy as np

from src.dsp.spectrum import compute_psd, remove_dc_offset
from src.pipeline.contracts import (
    DualIQFrame,
    DualSpectrogramFrame,
    IQChannelFrame,
    SpectrogramFrame,
)
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


class SpectrogramStage(Stage[DualIQFrame, DualSpectrogramFrame]):
    """Compute paired omni/Yagi PSDs per IQ frame."""

    name = "spectrogram"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config

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
