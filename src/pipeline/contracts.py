"""Shared data contracts for the SENTINEL RF pipeline.

These types define the handoff points between SDR capture, PSD generation,
event tracking, bearing estimation, and classification.  They intentionally
hold data only; behavior belongs in the DSP, antenna, and scoring modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

import numpy as np


class ChannelRole(str, Enum):
    """Logical receive role for one B210 channel."""

    OMNI = "omni"
    YAGI = "yagi"


class SignalFamily(str, Enum):
    """Protocol or modulation family inferred from RF behavior."""

    UNKNOWN = "unknown"
    WIFI = "wifi"
    BLE = "ble"
    OFDM = "ofdm"
    FHSS = "fhss"
    CSS = "css"
    NARROWBAND = "narrowband"
    WIDEBAND = "wideband"


class VerdictLabel(str, Enum):
    """Final multi-evidence classification label."""

    DRONE_CONFIRMED = "DRONE_CONFIRMED"
    DRONE_LIKELY = "DRONE_LIKELY"
    UNKNOWN_RF = "UNKNOWN_RF"
    CLUTTER = "CLUTTER"


def _ensure_1d(name: str, values: np.ndarray) -> None:
    if not isinstance(values, np.ndarray):
        raise TypeError(f"{name} must be a numpy array")
    if values.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if values.size == 0:
        raise ValueError(f"{name} must not be empty")


def _ensure_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _ensure_probability(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")


@dataclass(frozen=True)
class IQChannelFrame:
    """One synchronized IQ block from a single receive channel."""

    role: ChannelRole
    channel_index: int
    frame_index: int
    timestamp_s: float
    sample_rate_hz: float
    center_freq_hz: float
    antenna_port: str
    iq: np.ndarray
    azimuth_deg: float | None = None
    elevation_deg: float | None = None

    def __post_init__(self) -> None:
        _ensure_1d("iq", self.iq)
        if not np.issubdtype(self.iq.dtype, np.complexfloating):
            raise TypeError("iq must contain complex samples")
        if self.channel_index < 0:
            raise ValueError("channel_index must be non-negative")
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        _ensure_positive("sample_rate_hz", self.sample_rate_hz)
        _ensure_positive("center_freq_hz", self.center_freq_hz)

    @property
    def num_samples(self) -> int:
        return int(self.iq.size)

    @property
    def duration_sec(self) -> float:
        return self.num_samples / self.sample_rate_hz


@dataclass(frozen=True)
class DualIQFrame:
    """Paired omni/Yagi IQ frame from a two-channel receive pass."""

    frame_index: int
    timestamp_s: float
    rx_a: IQChannelFrame
    rx_b: IQChannelFrame

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        if self.rx_a.role != ChannelRole.OMNI:
            raise ValueError("rx_a must carry the omni channel")
        if self.rx_b.role != ChannelRole.YAGI:
            raise ValueError("rx_b must carry the yagi channel")
        if self.rx_a.channel_index == self.rx_b.channel_index:
            raise ValueError("rx_a and rx_b must use different channel indices")
        if self.rx_a.num_samples != self.rx_b.num_samples:
            raise ValueError("rx_a and rx_b must have the same sample count")
        if self.rx_a.sample_rate_hz != self.rx_b.sample_rate_hz:
            raise ValueError("rx_a and rx_b must have the same sample rate")

    @property
    def channels(self) -> tuple[IQChannelFrame, IQChannelFrame]:
        return (self.rx_a, self.rx_b)

    def by_role(self, role: ChannelRole) -> IQChannelFrame:
        if role == ChannelRole.OMNI:
            return self.rx_a
        if role == ChannelRole.YAGI:
            return self.rx_b
        raise ValueError(f"Unsupported channel role: {role}")


@dataclass(frozen=True)
class PSDFrame:
    """Power spectrum for one receive channel and frame."""

    role: ChannelRole
    frame_index: int
    timestamp_s: float
    sample_rate_hz: float
    center_freq_hz: float
    freq_hz: np.ndarray
    power_dbm: np.ndarray
    azimuth_deg: float | None = None
    elevation_deg: float | None = None

    def __post_init__(self) -> None:
        _ensure_1d("freq_hz", self.freq_hz)
        _ensure_1d("power_dbm", self.power_dbm)
        if self.freq_hz.shape != self.power_dbm.shape:
            raise ValueError("freq_hz and power_dbm must have the same shape")
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        _ensure_positive("sample_rate_hz", self.sample_rate_hz)
        _ensure_positive("center_freq_hz", self.center_freq_hz)

    @property
    def bin_width_hz(self) -> float:
        return self.sample_rate_hz / self.power_dbm.size

    @property
    def peak_power_dbm(self) -> float:
        return float(np.max(self.power_dbm))

    @property
    def peak_freq_hz(self) -> float:
        return float(self.freq_hz[int(np.argmax(self.power_dbm))])

    @property
    def median_noise_dbm(self) -> float:
        return float(np.median(self.power_dbm))


@dataclass(frozen=True)
class RFEvent:
    """A time-frequency object produced from one or more detector hits."""

    event_id: str
    role: ChannelRole
    start_time_s: float
    end_time_s: float
    center_freq_hz: float
    bandwidth_hz: float
    peak_power_dbm: float
    snr_db: float
    family: SignalFamily = SignalFamily.UNKNOWN
    source: str = "detector"
    bin_start: int | None = None
    bin_end: int | None = None
    bearing_deg: float | None = None
    bearing_rate_deg_s: float | None = None
    duty_cycle: float | None = None
    burst_period_s: float | None = None
    hop_rate_hz: float | None = None
    persistence_score: float | None = None
    supporting_frames: int = 1
    features: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must not be empty")
        if self.end_time_s < self.start_time_s:
            raise ValueError("end_time_s must be >= start_time_s")
        _ensure_positive("center_freq_hz", self.center_freq_hz)
        _ensure_positive("bandwidth_hz", self.bandwidth_hz)
        if self.supporting_frames < 1:
            raise ValueError("supporting_frames must be at least 1")
        if self.bin_start is not None and self.bin_start < 0:
            raise ValueError("bin_start must be non-negative")
        if self.bin_end is not None and self.bin_end < 0:
            raise ValueError("bin_end must be non-negative")
        if (
            self.bin_start is not None
            and self.bin_end is not None
            and self.bin_end < self.bin_start
        ):
            raise ValueError("bin_end must be >= bin_start")
        _ensure_probability("duty_cycle", self.duty_cycle)
        _ensure_probability("persistence_score", self.persistence_score)
        if self.burst_period_s is not None:
            _ensure_positive("burst_period_s", self.burst_period_s)
        if self.hop_rate_hz is not None:
            _ensure_positive("hop_rate_hz", self.hop_rate_hz)
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def duration_sec(self) -> float:
        return self.end_time_s - self.start_time_s


@dataclass(frozen=True)
class TrackedEmitter:
    """A sequence of RF events believed to come from the same emitter."""

    track_id: str
    events: tuple[RFEvent, ...]
    current_bearing_deg: float | None = None
    bearing_rate_deg_s: float | None = None
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not self.track_id:
            raise ValueError("track_id must not be empty")
        if not self.events:
            raise ValueError("events must contain at least one RFEvent")
        object.__setattr__(self, "events", tuple(self.events))
        _ensure_probability("confidence", self.confidence)

    @property
    def start_time_s(self) -> float:
        return self.events[0].start_time_s

    @property
    def end_time_s(self) -> float:
        return self.events[-1].end_time_s

    @property
    def latest_event(self) -> RFEvent:
        return self.events[-1]


@dataclass(frozen=True)
class DetectionVerdict:
    """Classifier output for one event or tracked emitter."""

    label: VerdictLabel
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    event: RFEvent | None = None
    track: TrackedEmitter | None = None
    protocol: SignalFamily = SignalFamily.UNKNOWN

    def __post_init__(self) -> None:
        _ensure_probability("confidence", self.confidence)
        if self.event is None and self.track is None:
            raise ValueError("DetectionVerdict requires either event or track")
        object.__setattr__(self, "reasons", tuple(self.reasons))


# ---------------------------------------------------------------------------
# V2 pipeline message types
#
# These flow through the V2 staged DAG:
#   DualIQFrame -> SpectrogramFrame -> Burst -> Candidate
#                                                  |
#                            Classification <------+------> BearingEstimate
#                                                  |
#                                              RFEvent (existing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpectrogramFrame:
    """Rolling time-frequency buffer for one receive channel.

    The buffer is ordered oldest-first along axis 0 of ``power_dbm``.
    ``timestamps_s[-1]`` is the most recent frame and corresponds to
    ``frame_index``.
    """

    role: ChannelRole
    frame_index: int
    sample_rate_hz: float
    center_freq_hz: float
    timestamps_s: np.ndarray
    freq_hz: np.ndarray
    power_dbm: np.ndarray
    azimuth_deg: float | None = None
    elevation_deg: float | None = None

    def __post_init__(self) -> None:
        _ensure_1d("timestamps_s", self.timestamps_s)
        _ensure_1d("freq_hz", self.freq_hz)
        if self.power_dbm.ndim != 2:
            raise ValueError("power_dbm must be two-dimensional [time, freq]")
        if self.power_dbm.shape[0] != self.timestamps_s.size:
            raise ValueError("power_dbm rows must match timestamps_s length")
        if self.power_dbm.shape[1] != self.freq_hz.size:
            raise ValueError("power_dbm cols must match freq_hz length")
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        _ensure_positive("sample_rate_hz", self.sample_rate_hz)
        _ensure_positive("center_freq_hz", self.center_freq_hz)

    @property
    def num_frames(self) -> int:
        return int(self.timestamps_s.size)

    @property
    def num_bins(self) -> int:
        return int(self.freq_hz.size)

    @property
    def latest_psd_dbm(self) -> np.ndarray:
        return self.power_dbm[-1]

    @property
    def history_sec(self) -> float:
        if self.num_frames < 2:
            return 0.0
        return float(self.timestamps_s[-1] - self.timestamps_s[0])


@dataclass(frozen=True)
class Burst:
    """A single time-frequency object detected on the spectrogram.

    A Burst spans one or more contiguous frames in time and one or more
    contiguous bins in frequency. Replaces the V1 ``Detection`` dataclass,
    which was per-frame and lost time bounds.
    """

    burst_id: str
    role: ChannelRole
    start_time_s: float
    end_time_s: float
    freq_lo_hz: float
    freq_hi_hz: float
    peak_power_dbm: float
    snr_db: float
    bin_start: int
    bin_end: int
    frame_start_index: int
    frame_end_index: int

    def __post_init__(self) -> None:
        if not self.burst_id:
            raise ValueError("burst_id must not be empty")
        if self.end_time_s < self.start_time_s:
            raise ValueError("end_time_s must be >= start_time_s")
        if self.freq_hi_hz < self.freq_lo_hz:
            raise ValueError("freq_hi_hz must be >= freq_lo_hz")
        _ensure_positive("freq_lo_hz", self.freq_lo_hz)
        if self.bin_start < 0 or self.bin_end < 0:
            raise ValueError("bin indices must be non-negative")
        if self.bin_end < self.bin_start:
            raise ValueError("bin_end must be >= bin_start")
        if self.frame_end_index < self.frame_start_index:
            raise ValueError("frame_end_index must be >= frame_start_index")

    @property
    def duration_sec(self) -> float:
        return self.end_time_s - self.start_time_s

    @property
    def bandwidth_hz(self) -> float:
        return self.freq_hi_hz - self.freq_lo_hz

    @property
    def center_freq_hz(self) -> float:
        return 0.5 * (self.freq_lo_hz + self.freq_hi_hz)


@dataclass(frozen=True)
class Candidate:
    """A clustered group of Bursts treated as one candidate emission.

    Carries the structural features Stage 2 (classifier) needs:
    bandwidth, duration, bursts in the cluster, optional hop rate and
    duty cycle.
    """

    candidate_id: str
    role: ChannelRole
    start_time_s: float
    end_time_s: float
    center_freq_hz: float
    bandwidth_hz: float
    bursts: tuple[Burst, ...]
    hop_rate_hz: float | None = None
    duty_cycle: float | None = None
    features: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        if not self.bursts:
            raise ValueError("bursts must contain at least one Burst")
        if self.end_time_s < self.start_time_s:
            raise ValueError("end_time_s must be >= start_time_s")
        _ensure_positive("center_freq_hz", self.center_freq_hz)
        _ensure_positive("bandwidth_hz", self.bandwidth_hz)
        _ensure_probability("duty_cycle", self.duty_cycle)
        if self.hop_rate_hz is not None:
            _ensure_positive("hop_rate_hz", self.hop_rate_hz)
        object.__setattr__(self, "bursts", tuple(self.bursts))
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def duration_sec(self) -> float:
        return self.end_time_s - self.start_time_s

    @property
    def num_bursts(self) -> int:
        return len(self.bursts)


@dataclass(frozen=True)
class Classification:
    """Stage 2 output: protocol guess for one Candidate."""

    candidate: Candidate
    protocol: SignalFamily
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    features: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_probability("confidence", self.confidence)
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id


@dataclass(frozen=True)
class BearingEstimate:
    """Stage 3 output: cued bearing for one Candidate.

    ``sweep_powers_dbm`` and ``sweep_azimuths_deg`` are optional and intended
    for visualization / debugging — the bearing decision is summarized in
    ``bearing_deg`` and ``confidence``.
    """

    candidate_id: str
    bearing_deg: float
    confidence: float
    peak_power_dbm: float
    sweep_powers_dbm: np.ndarray | None = None
    sweep_azimuths_deg: np.ndarray | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must not be empty")
        _ensure_probability("confidence", self.confidence)
        if self.sweep_powers_dbm is not None:
            _ensure_1d("sweep_powers_dbm", self.sweep_powers_dbm)
        if self.sweep_azimuths_deg is not None:
            _ensure_1d("sweep_azimuths_deg", self.sweep_azimuths_deg)
        if (
            self.sweep_powers_dbm is not None
            and self.sweep_azimuths_deg is not None
            and self.sweep_powers_dbm.shape != self.sweep_azimuths_deg.shape
        ):
            raise ValueError(
                "sweep_powers_dbm and sweep_azimuths_deg must have the same shape"
            )


# ---------------------------------------------------------------------------
# Composite inputs for stages that need more than one upstream message.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DualSpectrogramFrame:
    """Paired omni/Yagi spectrograms for one frame.

    Output of SpectrogramStage. BurstStage consumes ``omni``; the ``yagi``
    field is held by the Pipeline and handed to BearingStage when cued.
    """

    frame_index: int
    timestamp_s: float
    omni: SpectrogramFrame
    yagi: SpectrogramFrame

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        if self.omni.role != ChannelRole.OMNI:
            raise ValueError("omni must carry the OMNI role")
        if self.yagi.role != ChannelRole.YAGI:
            raise ValueError("yagi must carry the YAGI role")


@dataclass(frozen=True)
class BearingRequest:
    """Input to BearingStage: yagi spectrogram + candidates to localize."""

    yagi_spectrogram: SpectrogramFrame
    classifications: tuple[Classification, ...]
    azimuth_deg: float

    def __post_init__(self) -> None:
        if self.yagi_spectrogram.role != ChannelRole.YAGI:
            raise ValueError("yagi_spectrogram must carry the YAGI role")
        object.__setattr__(self, "classifications", tuple(self.classifications))


@dataclass(frozen=True)
class FuseRequest:
    """Input to FuseStage: classifications joined with bearings + frame metadata."""

    frame_index: int
    timestamp_s: float
    classifications: tuple[Classification, ...]
    bearings: tuple[BearingEstimate, ...]

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        object.__setattr__(self, "classifications", tuple(self.classifications))
        object.__setattr__(self, "bearings", tuple(self.bearings))
