"""BurstStage — SpectrogramFrame → tuple[Burst, ...].

Stage 1 of the V2 detector. Tracks contiguous bin runs across frames using
a protected EMA noise floor; emits a ``Burst`` when an active run goes quiet
for at least one frame. Bursts span the time interval over which the run
was active, with bin range covering its whole life (we extend the bin range
when later frames widen the active group).

Honest scope for Phase 1:
- Per-frame contiguous-bin grouping; no sub-bin localization.
- Run matching is by bin overlap. Splits / merges are not tracked — if a
  run merges with another, we keep the larger; if it splits, we close it
  and start two new runs. This is good enough for the protocols in scope.
- Operates on the omni channel only; the cued Yagi path uses CFAR in
  ``BearingStage``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.dsp.noise_floor import ProtectedNoiseFloor, alpha_for_window_frames
from src.pipeline.contracts import Burst, ChannelRole, SpectrogramFrame
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


@dataclass
class _ActiveRun:
    """In-progress (not yet closed) burst tracked across frames."""

    bin_start: int
    bin_end: int
    frame_start_index: int
    time_start_s: float
    last_seen_frame_index: int
    last_seen_time_s: float
    peak_power_dbm: float
    peak_bin: int
    peak_snr_db: float


class BurstStage(Stage[SpectrogramFrame, tuple[Burst, ...]]):
    """Detect time-frequency bursts on the omni channel."""

    name = "burst"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config

        spec = config.dsp.spectrogram
        burst = config.dsp.burst
        rx = config.sdr.rx_a

        self.fft_size = spec.fft_size
        self.threshold_db = burst.threshold_db
        self.min_burst_duration_s = burst.min_burst_duration_ms / 1000.0
        self.min_bandwidth_hz = burst.min_bandwidth_hz
        self.bin_width_hz = rx.sample_rate_hz / self.fft_size
        self.min_burst_bins = max(
            1, int(np.ceil(self.min_bandwidth_hz / self.bin_width_hz))
        )

        # EMA window: convert seconds → frames using the approximate frame rate.
        # frame_dt ~ 2 * fft_size / sample_rate (Welch with 50% overlap).
        approx_frame_rate = rx.sample_rate_hz / (2 * self.fft_size)
        nf_frames = max(1, int(burst.noise_floor_window_sec * approx_frame_rate))
        alpha = alpha_for_window_frames(nf_frames)

        self._noise_floor = ProtectedNoiseFloor(
            num_bins=self.fft_size,
            alpha=alpha,
            threshold_db=self.threshold_db,
        )
        self._active_runs: list[_ActiveRun] = []
        self._next_burst_id = 1

    def reset(self) -> None:
        self._noise_floor.reset()
        self._active_runs = []
        self._next_burst_id = 1

    async def process(self, spec: SpectrogramFrame) -> tuple[Burst, ...]:
        if spec.role != ChannelRole.OMNI:
            raise ValueError("BurstStage operates on the omni channel only")

        psd = spec.latest_psd_dbm
        floor = self._noise_floor.update(psd)
        above = psd > (floor + self.threshold_db)
        current_runs = _contiguous_runs(above)

        time_s = float(spec.timestamps_s[-1])
        frame_index = spec.frame_index

        closed = self._reconcile(current_runs, psd, floor, time_s, frame_index, spec)
        return tuple(closed)

    def flush(self, *, time_s: float, frame_index: int, spec: SpectrogramFrame) -> tuple[Burst, ...]:
        """Close any still-active runs (used at end-of-stream / tests)."""
        bursts = []
        for run in self._active_runs:
            burst = self._maybe_build_burst(run, spec)
            if burst is not None:
                bursts.append(burst)
        self._active_runs = []
        return tuple(bursts)

    # ---- internals ------------------------------------------------------

    def _reconcile(
        self,
        current_runs: list[tuple[int, int]],
        psd: np.ndarray,
        floor: np.ndarray,
        time_s: float,
        frame_index: int,
        spec: SpectrogramFrame,
    ) -> list[Burst]:
        matched: set[int] = set()

        for cs, ce in current_runs:
            best_idx = self._best_match(cs, ce, exclude=matched)
            peak_idx = cs + int(np.argmax(psd[cs : ce + 1]))
            peak_power = float(psd[peak_idx])
            peak_snr = float(psd[peak_idx] - floor[peak_idx])

            if best_idx is not None:
                run = self._active_runs[best_idx]
                run.bin_start = min(run.bin_start, cs)
                run.bin_end = max(run.bin_end, ce)
                run.last_seen_frame_index = frame_index
                run.last_seen_time_s = time_s
                if peak_power > run.peak_power_dbm:
                    run.peak_power_dbm = peak_power
                    run.peak_bin = peak_idx
                    run.peak_snr_db = peak_snr
                matched.add(best_idx)
            else:
                self._active_runs.append(
                    _ActiveRun(
                        bin_start=cs,
                        bin_end=ce,
                        frame_start_index=frame_index,
                        time_start_s=time_s,
                        last_seen_frame_index=frame_index,
                        last_seen_time_s=time_s,
                        peak_power_dbm=peak_power,
                        peak_bin=peak_idx,
                        peak_snr_db=peak_snr,
                    )
                )
                matched.add(len(self._active_runs) - 1)

        # Close active runs that did not match anything this frame.
        survivors: list[_ActiveRun] = []
        closed: list[Burst] = []
        for i, run in enumerate(self._active_runs):
            if i in matched:
                survivors.append(run)
                continue
            burst = self._maybe_build_burst(run, spec)
            if burst is not None:
                closed.append(burst)
        self._active_runs = survivors
        return closed

    def _best_match(self, cs: int, ce: int, *, exclude: set[int]) -> int | None:
        """Return the index of the active run with the largest bin overlap."""
        best_idx: int | None = None
        best_overlap = 0
        for i, run in enumerate(self._active_runs):
            if i in exclude:
                continue
            overlap = max(0, min(ce, run.bin_end) - max(cs, run.bin_start) + 1)
            if overlap > best_overlap:
                best_overlap = overlap
                best_idx = i
        return best_idx

    def _maybe_build_burst(
        self, run: _ActiveRun, spec: SpectrogramFrame
    ) -> Burst | None:
        duration = run.last_seen_time_s - run.time_start_s
        num_bins = run.bin_end - run.bin_start + 1
        bw_hz = num_bins * self.bin_width_hz
        if duration < self.min_burst_duration_s:
            return None
        if bw_hz < self.min_bandwidth_hz:
            return None

        burst_id = f"burst-{self._next_burst_id}"
        self._next_burst_id += 1

        # spec.freq_hz holds bin centers; the burst's frequency *bounds* are
        # the outer edges of the first and last bins so single-bin bursts
        # carry the full bin width as their bandwidth.
        half_bin = self.bin_width_hz / 2.0
        freq_lo = float(spec.freq_hz[run.bin_start] - half_bin)
        freq_hi = float(spec.freq_hz[run.bin_end] + half_bin)

        return Burst(
            burst_id=burst_id,
            role=ChannelRole.OMNI,
            start_time_s=run.time_start_s,
            end_time_s=run.last_seen_time_s,
            freq_lo_hz=freq_lo,
            freq_hi_hz=freq_hi,
            peak_power_dbm=run.peak_power_dbm,
            snr_db=run.peak_snr_db,
            bin_start=run.bin_start,
            bin_end=run.bin_end,
            frame_start_index=run.frame_start_index,
            frame_end_index=run.last_seen_frame_index,
        )


def _contiguous_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return [(bin_start, bin_end_inclusive), ...] of contiguous True runs."""
    runs: list[tuple[int, int]] = []
    in_run = False
    start = 0
    for i, v in enumerate(mask):
        if v and not in_run:
            start = i
            in_run = True
        elif not v and in_run:
            runs.append((start, i - 1))
            in_run = False
    if in_run:
        runs.append((start, len(mask) - 1))
    return runs
