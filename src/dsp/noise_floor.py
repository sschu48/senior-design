"""Protected exponential-moving-average noise-floor estimator.

Used by the V2 BurstStage to maintain a per-bin noise floor that is not
contaminated by the very signals the detector is looking for. The estimator
only updates bins whose current power is **below** ``noise_floor + threshold_db``;
bins that are flagged as signal hold their previous floor value.

This is the primitive form — pure state on the instance, no Stage subclassing,
no pipeline coupling — so it can be unit-tested directly and reused inside
any future detector.
"""

from __future__ import annotations

import numpy as np


def alpha_for_window_frames(window_frames: int) -> float:
    """EMA alpha equivalent to a simple moving average over N frames."""
    if window_frames < 1:
        raise ValueError("window_frames must be >= 1")
    return 2.0 / (window_frames + 1)


class ProtectedNoiseFloor:
    """Per-bin EMA noise floor that ignores bins currently above threshold.

    Parameters
    ----------
    num_bins : int
        Number of FFT bins.
    alpha : float
        EMA smoothing factor in (0, 1]. Smaller values adapt more slowly.
    threshold_db : float
        Bins ``threshold_db`` above the current floor are excluded from the
        EMA update — this is what prevents persistent emitters from training
        the estimator into their own power level.
    """

    def __init__(self, num_bins: int, alpha: float, threshold_db: float) -> None:
        if num_bins < 1:
            raise ValueError("num_bins must be >= 1")
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.num_bins = num_bins
        self.alpha = alpha
        self.threshold_db = threshold_db
        self._floor: np.ndarray | None = None

    @property
    def floor(self) -> np.ndarray | None:
        """Current per-bin noise floor (dBm), or None before the first update."""
        return None if self._floor is None else self._floor.copy()

    def reset(self) -> None:
        self._floor = None

    def update(self, psd_dbm: np.ndarray) -> np.ndarray:
        """Process one PSD frame and return a copy of the current floor.

        On the first call the floor is seeded from the input PSD.
        """
        if psd_dbm.shape != (self.num_bins,):
            raise ValueError(
                f"psd_dbm must have shape ({self.num_bins},), got {psd_dbm.shape}"
            )

        if self._floor is None:
            self._floor = psd_dbm.astype(np.float64, copy=True)
            return self._floor.copy()

        quiet = psd_dbm < (self._floor + self.threshold_db)
        self._floor = np.where(
            quiet,
            (1.0 - self.alpha) * self._floor + self.alpha * psd_dbm,
            self._floor,
        )
        return self._floor.copy()


__all__ = ["ProtectedNoiseFloor", "alpha_for_window_frames"]
