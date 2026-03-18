"""Signal detectors for SENTINEL.

Provides TripwireDetector (energy-based) and CFARDetector (CA-CFAR) that
consume PSD frames and emit Detection objects when signals exceed the
adaptive noise floor.

Also provides ``create_detectors()`` factory and ``deduplicate()`` helper
used by the pipeline engine, dashboard server, and bench test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.sdr.config import SentinelConfig


# ---------------------------------------------------------------------------
# Detection output — shared by all detectors
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    """A single detection event from any detector.

    Attributes
    ----------
    freq_hz : float
        Center frequency of the detected signal (absolute Hz).
    bandwidth_hz : float
        Estimated bandwidth of the detection.
    power_dbm : float
        Peak power within the detection (dBm).
    snr_db : float
        Signal-to-noise ratio relative to the local noise floor (dB).
    bin_start : int
        First FFT bin index of the detection.
    bin_end : int
        Last FFT bin index of the detection (inclusive).
    """

    freq_hz: float
    bandwidth_hz: float
    power_dbm: float
    snr_db: float
    bin_start: int
    bin_end: int


# ---------------------------------------------------------------------------
# Tripwire energy detector
# ---------------------------------------------------------------------------

@dataclass
class TripwireDetector:
    """Threshold-based energy detector with a protected noise floor.

    Designed for the omni channel — answers "is there energy above the noise
    floor?" in the observed band.

    Uses a per-bin exponential moving average (EMA) noise floor that only
    updates at bins NOT currently above threshold. This prevents persistent
    signals from raising their own noise floor estimate.

    Parameters
    ----------
    threshold_db : float
        dB above the noise floor to trigger.
    noise_floor_alpha : float
        EMA smoothing factor for noise floor updates.  Smaller values make
        the floor adapt slower (more stable).  Derived from
        ``2 / (noise_floor_frames + 1)`` by default.
    noise_floor_frames : int
        Convenience param — converted to alpha via ``2 / (N + 1)``.
        Only used if noise_floor_alpha is not set explicitly.
    min_trigger_frames : int
        Minimum consecutive frames a detection must persist before being
        reported (duration gating).
    sample_rate_hz : float
        Sample rate for converting bin indices to Hz.
    center_freq_hz : float
        Center frequency for absolute frequency output.
    fft_size : int
        Number of FFT bins per PSD frame.
    """

    threshold_db: float = 10.0
    noise_floor_frames: int = 50
    min_trigger_frames: int = 3
    sample_rate_hz: float = 30.72e6
    center_freq_hz: float = 2.437e9
    fft_size: int = 2048

    # Internal state
    _trigger_counts: np.ndarray | None = field(default=None, init=False, repr=False)
    _noise_floor: np.ndarray | None = field(default=None, init=False, repr=False)
    _alpha: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._trigger_counts = np.zeros(self.fft_size, dtype=np.int32)
        self._noise_floor = None
        # EMA alpha: faster response with fewer frames
        self._alpha = 2.0 / (self.noise_floor_frames + 1)

    @property
    def noise_floor(self) -> np.ndarray | None:
        """Current per-bin noise floor estimate (dBm), or None if not yet available."""
        return self._noise_floor

    def _update_noise_floor(self, psd_dbm: np.ndarray) -> None:
        """Update protected per-bin noise floor via EMA.

        Only bins NOT currently above threshold get their noise floor
        updated.  This prevents persistent signals from contaminating the
        noise estimate at their own frequency bins.
        """
        if self._noise_floor is None:
            self._noise_floor = psd_dbm.copy()
            return

        # Determine which bins are "quiet" (below threshold)
        quiet = psd_dbm < (self._noise_floor + self.threshold_db)

        # EMA update only at quiet bins
        self._noise_floor[quiet] = (
            (1.0 - self._alpha) * self._noise_floor[quiet]
            + self._alpha * psd_dbm[quiet]
        )

    def _group_contiguous_bins(self, mask: np.ndarray) -> list[tuple[int, int]]:
        """Find contiguous runs of True in a boolean mask.

        Returns list of (start, end) with end inclusive.
        """
        groups = []
        in_group = False
        start = 0
        for i, val in enumerate(mask):
            if val and not in_group:
                start = i
                in_group = True
            elif not val and in_group:
                groups.append((start, i - 1))
                in_group = False
        if in_group:
            groups.append((start, len(mask) - 1))
        return groups

    def process(self, psd_dbm: np.ndarray, freq_hz: np.ndarray) -> list[Detection]:
        """Process one PSD frame and return any detections.

        Parameters
        ----------
        psd_dbm : np.ndarray
            Power spectral density in dBm, shape (fft_size,).
        freq_hz : np.ndarray
            Frequency axis in Hz, shape (fft_size,).

        Returns
        -------
        list[Detection]
            Detections that passed threshold + duration gating.
        """
        self._update_noise_floor(psd_dbm)

        if self._noise_floor is None:
            return []

        # Per-bin exceedance check
        exceedance = psd_dbm - self._noise_floor
        above_threshold = exceedance >= self.threshold_db

        # Update per-bin trigger counters
        self._trigger_counts[above_threshold] += 1
        self._trigger_counts[~above_threshold] = 0

        # Find bins that have been triggered long enough
        duration_mask = self._trigger_counts >= self.min_trigger_frames
        if not np.any(duration_mask):
            return []

        # Group contiguous triggered bins into detections
        groups = self._group_contiguous_bins(duration_mask)
        bin_width = self.sample_rate_hz / self.fft_size

        detections = []
        for start, end in groups:
            peak_idx = start + np.argmax(psd_dbm[start : end + 1])
            peak_power = psd_dbm[peak_idx]
            noise_at_peak = self._noise_floor[peak_idx]
            snr = peak_power - noise_at_peak

            center_freq = float(np.mean(freq_hz[start : end + 1]))
            bandwidth = (end - start + 1) * bin_width

            detections.append(
                Detection(
                    freq_hz=center_freq,
                    bandwidth_hz=bandwidth,
                    power_dbm=float(peak_power),
                    snr_db=float(snr),
                    bin_start=start,
                    bin_end=end,
                )
            )

        return detections

    def reset(self) -> None:
        """Clear all internal state."""
        self._trigger_counts = np.zeros(self.fft_size, dtype=np.int32)
        self._noise_floor = None


# ---------------------------------------------------------------------------
# CA-CFAR detector
# ---------------------------------------------------------------------------

@dataclass
class CFARDetector:
    """Cell-Averaging Constant False Alarm Rate detector.

    Designed for the directional Yagi channel. Compares each cell under test
    (CUT) against the mean of surrounding reference cells, rejecting
    detections below a minimum bandwidth.

    Parameters
    ----------
    guard_cells : int
        Number of guard cells on each side of the CUT.
    reference_cells : int
        Number of reference (training) cells on each side.
    threshold_factor_db : float
        dB above the local average to trigger.
    min_detection_bw_hz : float
        Reject detections narrower than this.
    sample_rate_hz : float
        Sample rate for bin-to-Hz conversion.
    center_freq_hz : float
        Center frequency for absolute frequency output.
    fft_size : int
        Number of FFT bins per PSD frame.
    """

    guard_cells: int = 4
    reference_cells: int = 16
    threshold_factor_db: float = 10.0
    min_detection_bw_hz: float = 100e3
    sample_rate_hz: float = 30.72e6
    center_freq_hz: float = 2.437e9
    fft_size: int = 2048

    def _build_kernel(self) -> np.ndarray:
        """Build the CA-CFAR averaging kernel.

        The kernel has reference_cells on each side, guard cells set to 0,
        and the CUT position set to 0.  Normalized so it sums to 1.
        """
        half_len = self.reference_cells + self.guard_cells + 1
        kernel_len = 2 * half_len - 1
        kernel = np.zeros(kernel_len)

        center = half_len - 1
        # Reference cells on each side
        for i in range(self.reference_cells):
            # Left reference cells
            kernel[center - self.guard_cells - 1 - i] = 1.0
            # Right reference cells
            kernel[center + self.guard_cells + 1 + i] = 1.0

        total_ref = 2 * self.reference_cells
        if total_ref > 0:
            kernel /= total_ref

        return kernel

    def _group_contiguous_bins(self, mask: np.ndarray) -> list[tuple[int, int]]:
        """Find contiguous runs of True in a boolean mask.

        Returns list of (start, end) with end inclusive.
        """
        groups = []
        in_group = False
        start = 0
        for i, val in enumerate(mask):
            if val and not in_group:
                start = i
                in_group = True
            elif not val and in_group:
                groups.append((start, i - 1))
                in_group = False
        if in_group:
            groups.append((start, len(mask) - 1))
        return groups

    def process(self, psd_dbm: np.ndarray, freq_hz: np.ndarray) -> list[Detection]:
        """Process one PSD frame through CA-CFAR and return detections.

        Parameters
        ----------
        psd_dbm : np.ndarray
            Power spectral density in dBm, shape (fft_size,).
        freq_hz : np.ndarray
            Frequency axis in Hz, shape (fft_size,).

        Returns
        -------
        list[Detection]
            Detections that passed CFAR threshold + minimum BW filter.
        """
        # Convert dBm to linear for averaging (CFAR operates in linear domain)
        psd_linear = 10.0 ** (psd_dbm / 10.0)

        kernel = self._build_kernel()

        # Compute local noise estimate via convolution (wrapping at edges)
        noise_estimate = np.convolve(psd_linear, kernel, mode="same")

        # Threshold in linear domain
        threshold_linear = 10.0 ** (self.threshold_factor_db / 10.0)
        detection_mask = psd_linear > (noise_estimate * threshold_linear)

        if not np.any(detection_mask):
            return []

        # Group contiguous detections
        groups = self._group_contiguous_bins(detection_mask)
        bin_width = self.sample_rate_hz / self.fft_size
        min_bins = self.min_detection_bw_hz / bin_width

        detections = []
        for start, end in groups:
            num_bins = end - start + 1
            # Reject detections below minimum bandwidth
            if num_bins < min_bins:
                continue

            peak_idx = start + np.argmax(psd_dbm[start : end + 1])
            peak_power = psd_dbm[peak_idx]

            # SNR: peak power vs local noise estimate (convert back to dB)
            noise_at_peak_linear = noise_estimate[peak_idx]
            noise_at_peak_db = 10.0 * np.log10(max(noise_at_peak_linear, 1e-30))
            snr = peak_power - noise_at_peak_db

            center_freq = float(np.mean(freq_hz[start : end + 1]))
            bandwidth = num_bins * bin_width

            detections.append(
                Detection(
                    freq_hz=center_freq,
                    bandwidth_hz=bandwidth,
                    power_dbm=float(peak_power),
                    snr_db=float(snr),
                    bin_start=start,
                    bin_end=end,
                )
            )

        return detections


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def deduplicate(detections: list[Detection]) -> list[Detection]:
    """Remove overlapping detections, keeping the one with higher SNR."""
    if len(detections) <= 1:
        return detections

    detections.sort(key=lambda d: d.snr_db, reverse=True)
    kept: list[Detection] = []

    for d in detections:
        overlaps = False
        for k in kept:
            if d.bin_start <= k.bin_end and d.bin_end >= k.bin_start:
                overlaps = True
                break
        if not overlaps:
            kept.append(d)

    return kept


def create_detectors(
    config: SentinelConfig,
) -> tuple[TripwireDetector, CFARDetector]:
    """Build TripwireDetector + CFARDetector from config.

    Centralizes the detector construction that was previously duplicated
    across the pipeline engine, dashboard server, and bench test.
    """
    dsp = config.dsp
    rx = config.sdr.rx_a
    frames_per_sec = rx.sample_rate_hz / dsp.fft_size

    tw = dsp.tripwire
    noise_floor_frames = max(2, int(tw.noise_floor_window_sec * frames_per_sec))
    min_trigger_frames = max(1, int(
        tw.min_trigger_duration_ms / 1000.0 * frames_per_sec
    ))

    tripwire = TripwireDetector(
        threshold_db=tw.threshold_db,
        noise_floor_frames=noise_floor_frames,
        min_trigger_frames=min_trigger_frames,
        sample_rate_hz=rx.sample_rate_hz,
        center_freq_hz=rx.center_freq_hz,
        fft_size=dsp.fft_size,
    )

    cf = dsp.cfar
    cfar = CFARDetector(
        guard_cells=cf.guard_cells,
        reference_cells=cf.reference_cells,
        threshold_factor_db=cf.threshold_factor_db,
        min_detection_bw_hz=cf.min_detection_bw_hz,
        sample_rate_hz=rx.sample_rate_hz,
        center_freq_hz=rx.center_freq_hz,
        fft_size=dsp.fft_size,
    )

    return tripwire, cfar
