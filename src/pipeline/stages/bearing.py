"""BearingStage — BearingRequest → tuple[BearingEstimate, ...].

Stage 3: cued bearing estimation using the Yagi channel. For each
classification, slices the Yagi PSD over the candidate's frequency range,
runs CA-CFAR over that slice for a local noise estimate, and reports the
peak power and SNR at the current antenna azimuth.

Phase 1 simplification: bearing is reported as the *current* antenna
azimuth, not a swept peak. Real swept peak-find is a multi-frame
operation driven by ``Pipeline._cue_antenna`` and is part of the Phase 2
hardware-validation work — but the contract is the same, so swapping in
real swept bearing is local to this stage.
"""

from __future__ import annotations

import numpy as np

from src.dsp import cfar
from src.pipeline.contracts import (
    BearingEstimate,
    BearingRequest,
    Classification,
    SpectrogramFrame,
)
from src.pipeline.stage import Stage
from src.sdr.config import SentinelConfig


# Slice-wide CFAR may need padding on each side to avoid edge artifacts.
# We pad by ``reference_cells + guard_cells`` bins on each side using the
# nearest in-slice samples.
def _pad_slice(psd: np.ndarray, pad: int) -> np.ndarray:
    if pad <= 0 or psd.size == 0:
        return psd
    left = np.full(pad, psd[0])
    right = np.full(pad, psd[-1])
    return np.concatenate([left, psd, right])


class BearingStage(Stage[BearingRequest, tuple[BearingEstimate, ...]]):
    name = "bearing"

    def __init__(self, config: SentinelConfig) -> None:
        super().__init__()
        self.config = config
        self._cfar_cfg = config.dsp.cfar

    async def process(
        self, request: BearingRequest
    ) -> tuple[BearingEstimate, ...]:
        if not request.classifications:
            return ()

        out: list[BearingEstimate] = []
        for cls in request.classifications:
            estimate = self._estimate_one(
                cls,
                request.yagi_spectrogram,
                request.azimuth_deg,
            )
            out.append(estimate)
        return tuple(out)

    def _estimate_one(
        self,
        cls: Classification,
        yagi_spec: SpectrogramFrame,
        azimuth_deg: float,
    ) -> BearingEstimate:
        candidate = cls.candidate
        psd = yagi_spec.latest_psd_dbm
        freq_hz = yagi_spec.freq_hz

        bin_lo = int(np.searchsorted(freq_hz, candidate.center_freq_hz - candidate.bandwidth_hz / 2))
        bin_hi = int(np.searchsorted(freq_hz, candidate.center_freq_hz + candidate.bandwidth_hz / 2))
        bin_lo = max(0, min(bin_lo, psd.size - 1))
        bin_hi = max(bin_lo + 1, min(bin_hi, psd.size))
        slice_psd = psd[bin_lo:bin_hi]

        if slice_psd.size == 0:
            return BearingEstimate(
                candidate_id=candidate.candidate_id,
                bearing_deg=float(azimuth_deg),
                confidence=0.0,
                peak_power_dbm=float("-inf"),
            )

        pad = self._cfar_cfg.reference_cells + self._cfar_cfg.guard_cells
        padded = _pad_slice(slice_psd, pad)
        result = cfar.apply(
            padded,
            guard_cells=self._cfar_cfg.guard_cells,
            reference_cells=self._cfar_cfg.reference_cells,
            threshold_factor_db=self._cfar_cfg.threshold_factor_db,
        )
        # Drop padding before reading SNR.
        snr_unpadded = result.snr_db[pad : pad + slice_psd.size]
        peak_idx = int(np.argmax(slice_psd))
        peak_power = float(slice_psd[peak_idx])
        peak_snr = float(snr_unpadded[peak_idx])

        # Confidence rises with SNR — saturate at 30 dB above CFAR threshold.
        excess = peak_snr - self._cfar_cfg.threshold_factor_db
        confidence = float(np.clip(excess / 30.0, 0.0, 1.0))

        return BearingEstimate(
            candidate_id=candidate.candidate_id,
            bearing_deg=float(azimuth_deg),
            confidence=confidence,
            peak_power_dbm=peak_power,
        )
