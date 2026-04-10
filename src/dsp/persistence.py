"""Persistence detector for SENTINEL.

Tracks per-frequency-bin duty cycle over a sliding window of frames.
Bins with high persistence (active in many recent frames) indicate
continuous transmitters like drone video downlinks. Low persistence
indicates bursty sources like WiFi beacons.
"""

from __future__ import annotations

import numpy as np


class PersistenceDetector:
    """Sliding-window persistence tracker for PSD bins.

    Parameters
    ----------
    num_bins : int
        Number of frequency bins (must match PSD output length).
    window_frames : int
        Number of recent frames to track. Larger = smoother but slower
        to respond to changes.
    threshold_db : float
        A bin is "active" if its power exceeds the per-frame noise floor
        by at least this many dB.
    """

    def __init__(
        self,
        num_bins: int,
        window_frames: int = 60,
        threshold_db: float = 6.0,
    ) -> None:
        self.num_bins = num_bins
        self.window_frames = window_frames
        self.threshold_db = threshold_db

        # Circular buffer: each row is a boolean mask of active bins for one frame
        self._buffer = np.zeros((window_frames, num_bins), dtype=np.bool_)
        self._write_idx = 0
        self._filled = 0  # how many frames have been written so far

    def update(self, power_dbm: np.ndarray) -> np.ndarray:
        """Process one PSD frame and return persistence values.

        Parameters
        ----------
        power_dbm : np.ndarray
            Power per bin in dBm, length ``num_bins``.

        Returns
        -------
        np.ndarray
            Persistence per bin in [0.0, 1.0], length ``num_bins``.
            1.0 = active in every frame of the window.
            0.0 = never active.
        """
        # Noise floor estimate: median of this frame (ignoring active bins)
        noise_floor = np.median(power_dbm)

        # Mark bins active if above noise floor + threshold
        active = power_dbm > (noise_floor + self.threshold_db)

        # Write into circular buffer
        self._buffer[self._write_idx] = active
        self._write_idx = (self._write_idx + 1) % self.window_frames
        self._filled = min(self._filled + 1, self.window_frames)

        # Persistence = fraction of filled frames where each bin was active
        if self._filled < self.window_frames:
            count = np.sum(self._buffer[:self._filled], axis=0)
        else:
            count = np.sum(self._buffer, axis=0)

        persistence = count.astype(np.float32) / self._filled

        return persistence

    def reset(self) -> None:
        """Clear all history."""
        self._buffer[:] = False
        self._write_idx = 0
        self._filled = 0
