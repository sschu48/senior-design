"""Cell-Averaging Constant False Alarm Rate (CA-CFAR) primitive.

The CFAR kernel is a sliding-window template with N reference cells on each
side, G guard cells bracketing the cell-under-test (CUT), and a zeroed CUT
position. The local noise estimate at each bin is the linear-power average
over reference cells; a bin exceeds threshold when its linear power is more
than ``threshold_factor_db`` above that local average.

V2 uses CFAR inside ``BearingStage`` only — running it on the cued Yagi
spectrogram over a candidate's frequency range — rather than as a standalone
top-level detector.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CFARResult:
    """Per-bin CFAR decision plus noise estimate and SNR."""

    detection_mask: np.ndarray
    noise_estimate_dbm: np.ndarray
    snr_db: np.ndarray


def build_kernel(guard_cells: int, reference_cells: int) -> np.ndarray:
    """Build a normalized CA-CFAR averaging kernel.

    The returned kernel has length ``2 * (reference_cells + guard_cells + 1) - 1``
    with reference cells on each side, guard cells set to zero, and the CUT
    set to zero. Sums to one.
    """
    if guard_cells < 0:
        raise ValueError("guard_cells must be >= 0")
    if reference_cells < 1:
        raise ValueError("reference_cells must be >= 1")

    half_len = reference_cells + guard_cells + 1
    kernel = np.zeros(2 * half_len - 1)
    center = half_len - 1
    for i in range(reference_cells):
        kernel[center - guard_cells - 1 - i] = 1.0
        kernel[center + guard_cells + 1 + i] = 1.0
    kernel /= 2 * reference_cells
    return kernel


def apply(
    psd_dbm: np.ndarray,
    *,
    guard_cells: int,
    reference_cells: int,
    threshold_factor_db: float,
) -> CFARResult:
    """Apply CA-CFAR across one PSD frame.

    Operates on linear power (10**(dBm/10)). Returns:
    - per-bin detection mask
    - per-bin local noise estimate in dBm
    - per-bin SNR (psd - noise) in dB
    """
    if psd_dbm.ndim != 1:
        raise ValueError("psd_dbm must be 1D")

    psd_linear = 10.0 ** (psd_dbm / 10.0)
    kernel = build_kernel(guard_cells, reference_cells)
    noise_linear = np.convolve(psd_linear, kernel, mode="same")

    threshold_linear = 10.0 ** (threshold_factor_db / 10.0)
    detection_mask = psd_linear > (noise_linear * threshold_linear)

    noise_dbm = 10.0 * np.log10(np.maximum(noise_linear, 1e-30))
    snr_db = psd_dbm - noise_dbm

    return CFARResult(
        detection_mask=detection_mask,
        noise_estimate_dbm=noise_dbm,
        snr_db=snr_db,
    )


__all__ = ["CFARResult", "build_kernel", "apply"]
