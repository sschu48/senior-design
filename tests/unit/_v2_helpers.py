"""Shared helpers for V2 stage and pipeline tests.

The V2 pipeline is heavily config-driven, so most tests start from a base
``SentinelConfig`` loaded from config.yaml and tweak only the parameters
the test cares about. ``make_v2_test_config()`` returns a config tuned for
fast convergence on synthetic IQ — small FFT, fast EMA, no gating.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from src.pipeline.contracts import (
    ChannelRole,
    DualIQFrame,
    IQChannelFrame,
    SpectrogramFrame,
)
from src.sdr.config import SentinelConfig, load_config


def make_v2_test_config() -> SentinelConfig:
    """Return a SentinelConfig tuned for synthetic-IQ stage tests."""
    base = load_config()

    spec = dataclasses.replace(
        base.dsp.spectrogram,
        history_ms=50.0,
        fft_size=256,  # small for fast tests
    )
    burst = dataclasses.replace(
        base.dsp.burst,
        threshold_db=8.0,
        noise_floor_window_sec=0.05,
        min_burst_duration_ms=0.0,
        min_bandwidth_hz=0.0,
    )
    cluster = dataclasses.replace(base.dsp.cluster, max_time_gap_ms=20.0)
    classifier = dataclasses.replace(base.dsp.classifier, min_confidence=0.3)
    bearing = dataclasses.replace(
        base.dsp.bearing,
        # Tight sweep requirement so tests complete a bearing in a few frames.
        min_sweep_samples=3,
        azimuth_bin_deg=2.0,
    )

    new_dsp = dataclasses.replace(
        base.dsp,
        spectrogram=spec,
        burst=burst,
        cluster=cluster,
        classifier=classifier,
        bearing=bearing,
    )
    return dataclasses.replace(base, dsp=new_dsp)


def make_iq_channel_frame(
    *,
    role: ChannelRole = ChannelRole.OMNI,
    channel_index: int = 0,
    frame_index: int = 0,
    timestamp_s: float = 0.0,
    sample_rate_hz: float = 30.72e6,
    center_freq_hz: float = 2.437e9,
    num_samples: int = 512,
    iq: np.ndarray | None = None,
    azimuth_deg: float | None = 0.0,
) -> IQChannelFrame:
    if iq is None:
        iq = np.zeros(num_samples, dtype=np.complex64)
    return IQChannelFrame(
        role=role,
        channel_index=channel_index,
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        sample_rate_hz=sample_rate_hz,
        center_freq_hz=center_freq_hz,
        antenna_port="RX2" if role == ChannelRole.OMNI else "TX/RX",
        iq=iq,
        azimuth_deg=azimuth_deg,
    )


def make_dual_iq_frame(
    *,
    frame_index: int = 0,
    timestamp_s: float = 0.0,
    num_samples: int = 512,
    omni_iq: np.ndarray | None = None,
    yagi_iq: np.ndarray | None = None,
) -> DualIQFrame:
    rx_a = make_iq_channel_frame(
        role=ChannelRole.OMNI,
        channel_index=0,
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        num_samples=num_samples,
        iq=omni_iq,
    )
    rx_b = make_iq_channel_frame(
        role=ChannelRole.YAGI,
        channel_index=1,
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        num_samples=num_samples,
        iq=yagi_iq,
    )
    return DualIQFrame(
        frame_index=frame_index,
        timestamp_s=timestamp_s,
        rx_a=rx_a,
        rx_b=rx_b,
    )


def make_spectrogram_frame(
    *,
    role: ChannelRole = ChannelRole.OMNI,
    frame_index: int = 0,
    timestamp_s: float = 0.0,
    sample_rate_hz: float = 30.72e6,
    center_freq_hz: float = 2.437e9,
    fft_size: int = 256,
    psd_dbm: np.ndarray | None = None,
    azimuth_deg: float | None = 0.0,
) -> SpectrogramFrame:
    if psd_dbm is None:
        psd_dbm = np.full(fft_size, -90.0)
    bin_width = sample_rate_hz / fft_size
    freq_hz = center_freq_hz + (np.arange(fft_size) - fft_size / 2) * bin_width
    return SpectrogramFrame(
        role=role,
        frame_index=frame_index,
        sample_rate_hz=sample_rate_hz,
        center_freq_hz=center_freq_hz,
        timestamps_s=np.array([timestamp_s], dtype=np.float64),
        freq_hz=freq_hz,
        power_dbm=psd_dbm[np.newaxis, :].astype(np.float64),
        azimuth_deg=azimuth_deg,
    )
