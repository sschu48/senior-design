"""Rolling time-frequency buffer for one channel.

Implements the rolling spectrogram buffer described in
``docs/ARCHITECTURE_V2.md`` §4.1. ``SpectrogramStage`` keeps one per role
(omni / Yagi). The buffer holds the most recent N PSD rows in a circular
array and exposes both fast-path single-frame access (``latest``) and an
oldest-first ``snapshot`` for downstream feature extraction or
visualization.

This is a primitive — pure state on the instance, no Stage subclassing,
no pipeline coupling. Snapshots are returned as **copies** so they are
safe to retain across pipeline cycles.
"""

from __future__ import annotations

import numpy as np


class SpectrogramBuffer:
    """Circular per-bin PSD buffer with timestamps."""

    def __init__(self, num_bins: int, max_frames: int) -> None:
        if num_bins < 1:
            raise ValueError("num_bins must be >= 1")
        if max_frames < 1:
            raise ValueError("max_frames must be >= 1")
        self._num_bins = num_bins
        self._max_frames = max_frames
        self._timestamps = np.zeros(max_frames, dtype=np.float64)
        self._power = np.full((max_frames, num_bins), -np.inf, dtype=np.float64)
        self._size = 0
        self._head = 0  # next write index

    @property
    def num_bins(self) -> int:
        return self._num_bins

    @property
    def max_frames(self) -> int:
        return self._max_frames

    @property
    def size(self) -> int:
        """Number of rows currently filled (≤ max_frames)."""
        return self._size

    @property
    def is_full(self) -> bool:
        return self._size >= self._max_frames

    def append(self, timestamp_s: float, psd_dbm: np.ndarray) -> None:
        """Append one PSD row, evicting the oldest if full."""
        if psd_dbm.shape != (self._num_bins,):
            raise ValueError(
                f"psd_dbm must have shape ({self._num_bins},), got {psd_dbm.shape}"
            )
        self._timestamps[self._head] = float(timestamp_s)
        self._power[self._head] = psd_dbm
        self._head = (self._head + 1) % self._max_frames
        self._size = min(self._size + 1, self._max_frames)

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(timestamps, power_dbm)`` as oldest-first copies."""
        if self._size == 0:
            return (
                np.empty(0, dtype=np.float64),
                np.empty((0, self._num_bins), dtype=np.float64),
            )
        if self._size < self._max_frames:
            return (
                self._timestamps[: self._size].copy(),
                self._power[: self._size].copy(),
            )
        # Buffer is full and circular; head points at the oldest row.
        ts = np.concatenate(
            [self._timestamps[self._head:], self._timestamps[: self._head]]
        )
        pwr = np.concatenate(
            [self._power[self._head:], self._power[: self._head]]
        )
        return ts, pwr

    def latest(self) -> tuple[float, np.ndarray] | None:
        """Return ``(timestamp, psd_copy)`` for the most recent row, or None."""
        if self._size == 0:
            return None
        idx = (self._head - 1) % self._max_frames
        return float(self._timestamps[idx]), self._power[idx].copy()

    def reset(self) -> None:
        self._size = 0
        self._head = 0


__all__ = ["SpectrogramBuffer"]
