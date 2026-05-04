"""BearingStage — BearingRequest → tuple[BearingEstimate, ...].

Stage 3: cued **swept** bearing estimation on the Yagi channel. When a
classification arrives, BearingStage opens a per-candidate sweep that
accumulates ``(azimuth, peak_yagi_power_in_band)`` samples across frames.
Once enough samples are collected, the stage finds the peak by binning
azimuths and emits a ``BearingEstimate``. The pipeline drives the
antenna's ``BEARING_SEARCH`` mode during this period and transitions to
``ALARM`` → ``TRACK`` on the emitted estimate.

This implementation matches ``docs/ARCHITECTURE_V2.md`` §4.3 Stage 3:
"Rotator sweep over the candidate's frequency band, bearing estimate from
peak Yagi RSSI in the candidate's specific FFT bins."
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.dsp import cfar
from src.pipeline.contracts import (
    BearingEstimate,
    BearingRequest,
    Candidate,
    Classification,
    SpectrogramFrame,
)
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


def _pad_slice(psd: np.ndarray, pad: int) -> np.ndarray:
    if pad <= 0 or psd.size == 0:
        return psd
    left = np.full(pad, psd[0])
    right = np.full(pad, psd[-1])
    return np.concatenate([left, psd, right])


@dataclass
class _ActiveSweep:
    """In-progress per-candidate bearing measurement."""

    candidate_id: str
    candidate: Candidate
    classification: Classification
    band_lo_hz: float
    band_hi_hz: float
    azimuths_deg: list[float] = field(default_factory=list)
    powers_dbm: list[float] = field(default_factory=list)
    snrs_db: list[float] = field(default_factory=list)


class BearingStage(Stage[BearingRequest, tuple[BearingEstimate, ...]]):
    name = "bearing"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config
        self._cfar_cfg = config.dsp.cfar
        self._bearing_cfg = config.dsp.bearing
        self._active: dict[str, _ActiveSweep] = {}

    def reset(self) -> None:
        self._active = {}

    @property
    def active_candidate_ids(self) -> tuple[str, ...]:
        return tuple(self._active.keys())

    async def process(
        self, request: BearingRequest
    ) -> tuple[BearingEstimate, ...]:
        # 1. Open new sweeps for newly classified candidates.
        for cls in request.classifications:
            if cls.candidate_id in self._active:
                continue
            cand = cls.candidate
            self._active[cls.candidate_id] = _ActiveSweep(
                candidate_id=cls.candidate_id,
                candidate=cand,
                classification=cls,
                band_lo_hz=cand.center_freq_hz - cand.bandwidth_hz / 2,
                band_hi_hz=cand.center_freq_hz + cand.bandwidth_hz / 2,
            )

        # 2. Sample current azimuth into every active sweep.
        completed: list[BearingEstimate] = []
        for cid in list(self._active.keys()):
            sweep = self._active[cid]
            power, snr = self._measure_band(
                request.yagi_spectrogram, sweep.band_lo_hz, sweep.band_hi_hz
            )
            sweep.azimuths_deg.append(float(request.azimuth_deg))
            sweep.powers_dbm.append(float(power))
            sweep.snrs_db.append(float(snr))

            if len(sweep.azimuths_deg) >= self._bearing_cfg.min_sweep_samples:
                completed.append(self._finalize(sweep))
                del self._active[cid]

        return tuple(completed)

    # ---- internals ------------------------------------------------------

    def _measure_band(
        self,
        yagi_spec: SpectrogramFrame,
        band_lo_hz: float,
        band_hi_hz: float,
    ) -> tuple[float, float]:
        """Return (peak_power_dbm, peak_snr_db) within the band.

        Slices the Yagi PSD over the candidate's frequency range, runs CA-CFAR
        for a local noise estimate, and returns the in-band peak power and
        its SNR over the local noise.
        """
        psd = yagi_spec.latest_psd_dbm
        freq_hz = yagi_spec.freq_hz

        bin_lo = int(np.searchsorted(freq_hz, band_lo_hz))
        bin_hi = int(np.searchsorted(freq_hz, band_hi_hz))
        bin_lo = max(0, min(bin_lo, psd.size - 1))
        bin_hi = max(bin_lo + 1, min(bin_hi, psd.size))
        slice_psd = psd[bin_lo:bin_hi]

        if slice_psd.size == 0:
            return float("-inf"), float("-inf")

        pad = self._cfar_cfg.reference_cells + self._cfar_cfg.guard_cells
        padded = _pad_slice(slice_psd, pad)
        result = cfar.apply(
            padded,
            guard_cells=self._cfar_cfg.guard_cells,
            reference_cells=self._cfar_cfg.reference_cells,
            threshold_factor_db=self._cfar_cfg.threshold_factor_db,
        )
        snr_unpadded = result.snr_db[pad : pad + slice_psd.size]
        peak_idx = int(np.argmax(slice_psd))
        return float(slice_psd[peak_idx]), float(snr_unpadded[peak_idx])

    def _finalize(self, sweep: _ActiveSweep) -> BearingEstimate:
        """Find peak azimuth bucket and emit a BearingEstimate."""
        azimuths = np.asarray(sweep.azimuths_deg, dtype=np.float64)
        powers = np.asarray(sweep.powers_dbm, dtype=np.float64)
        snrs = np.asarray(sweep.snrs_db, dtype=np.float64)

        bin_deg = self._bearing_cfg.azimuth_bin_deg
        # Bucket azimuths and aggregate the max power per bucket.
        bucket_keys = np.round(azimuths / bin_deg).astype(np.int64)
        unique_buckets, inverse = np.unique(bucket_keys, return_inverse=True)
        bucketed_powers = np.full(unique_buckets.size, -np.inf)
        for i in range(azimuths.size):
            bidx = inverse[i]
            if powers[i] > bucketed_powers[bidx]:
                bucketed_powers[bidx] = powers[i]

        peak_bucket_idx = int(np.argmax(bucketed_powers))
        peak_azimuth = float(unique_buckets[peak_bucket_idx]) * bin_deg
        peak_power = float(bucketed_powers[peak_bucket_idx])

        # Confidence — peak SNR scaled and prominence vs. mean power.
        peak_snr_db = float(np.max(snrs))
        excess = peak_snr_db - self._cfar_cfg.threshold_factor_db
        snr_term = np.clip(excess / 30.0, 0.0, 1.0)

        valid_powers = bucketed_powers[np.isfinite(bucketed_powers)]
        if valid_powers.size > 1:
            mean_power = float(np.mean(valid_powers))
            prominence = max(0.0, peak_power - mean_power)
            prom_term = float(np.clip(prominence / 20.0, 0.0, 1.0))
        else:
            prom_term = 0.0

        confidence = float(np.clip(0.5 * snr_term + 0.5 * prom_term, 0.0, 1.0))

        return BearingEstimate(
            candidate_id=sweep.candidate_id,
            bearing_deg=peak_azimuth,
            confidence=confidence,
            peak_power_dbm=peak_power,
            sweep_powers_dbm=powers.copy(),
            sweep_azimuths_deg=azimuths.copy(),
        )
