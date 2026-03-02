"""IQ sample sources for SENTINEL.

Provides an abstract IQSource interface and concrete implementations:
- SyntheticSource: software-generated IQ for testing without hardware
- USRPSource: placeholder for USRP B210 (not yet implemented)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Literal

import numpy as np


# ---------------------------------------------------------------------------
# Signal definition
# ---------------------------------------------------------------------------

@dataclass
class SignalDef:
    """Describes an injected signal in the synthetic source.

    Parameters
    ----------
    freq_offset_hz : float
        Offset from center frequency (can be negative).
    bandwidth_hz : float
        Signal bandwidth.  Ignored for ``tone`` type.
    power_dbm : float
        Signal power in dBm.
    signal_type : {"tone", "wideband"}
        ``tone`` — single complex sinusoid.
        ``wideband`` — sum of random-phase subcarriers (OFDM-like).
    num_subcarriers : int
        Number of subcarriers for ``wideband`` type.  Ignored for ``tone``.
    """

    freq_offset_hz: float
    bandwidth_hz: float = 0.0
    power_dbm: float = -50.0
    signal_type: Literal["tone", "wideband"] = "tone"
    num_subcarriers: int = 64


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class IQSource(abc.ABC):
    """Abstract IQ sample source."""

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @abc.abstractmethod
    async def read(self, num_samples: int) -> np.ndarray:
        """Return *num_samples* complex64 IQ samples."""
        ...


# ---------------------------------------------------------------------------
# Synthetic source
# ---------------------------------------------------------------------------

def _dbm_to_linear_amplitude(dbm: float) -> float:
    """Convert power in dBm to linear voltage amplitude (assuming 1-ohm)."""
    # P_watts = 10^((dBm - 30) / 10)
    # amplitude = sqrt(P_watts)
    return np.sqrt(10.0 ** ((dbm - 30.0) / 10.0))


@dataclass
class SyntheticSource(IQSource):
    """Generates IQ samples from configurable noise + signals.

    Phase-continuous across successive ``read()`` calls via a running
    sample index counter.  Deterministic when *seed* is set.

    Parameters
    ----------
    sample_rate_hz : float
        Sample rate in Hz.
    noise_power_dbm : float
        AWGN noise floor power in dBm.
    signals : list[SignalDef]
        Signals to inject on top of the noise floor.
    seed : int or None
        RNG seed for reproducible output.
    """

    sample_rate_hz: float = 30.72e6
    noise_power_dbm: float = -90.0
    signals: list[SignalDef] = field(default_factory=list)
    seed: int | None = None

    # internal state (not constructor args)
    _running: bool = field(default=False, init=False, repr=False)
    _sample_index: int = field(default=0, init=False, repr=False)
    _rng: np.random.Generator = field(
        default=None, init=False, repr=False  # type: ignore[assignment]
    )
    # pre-computed subcarrier phases per wideband signal (keyed by id)
    _wideband_phases: dict[int, np.ndarray] = field(
        default_factory=dict, init=False, repr=False
    )

    async def start(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self._sample_index = 0
        self._wideband_phases = {}

        # Pre-generate random subcarrier phases for each wideband signal
        for i, sig in enumerate(self.signals):
            if sig.signal_type == "wideband":
                self._wideband_phases[i] = self._rng.uniform(
                    0, 2 * np.pi, size=sig.num_subcarriers
                )

        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def read(self, num_samples: int) -> np.ndarray:
        if not self._running:
            raise RuntimeError("Source not started — call start() first")

        t_indices = np.arange(
            self._sample_index, self._sample_index + num_samples, dtype=np.float64
        )

        # --- AWGN noise floor ---
        noise_amplitude = _dbm_to_linear_amplitude(self.noise_power_dbm)
        # complex noise: each component has variance = amplitude^2 / 2
        noise_std = noise_amplitude / np.sqrt(2.0)
        iq = self._rng.normal(0, noise_std, num_samples) + 1j * self._rng.normal(
            0, noise_std, num_samples
        )

        # --- Inject signals ---
        for i, sig in enumerate(self.signals):
            if sig.signal_type == "tone":
                amp = _dbm_to_linear_amplitude(sig.power_dbm)
                phase = 2.0 * np.pi * sig.freq_offset_hz / self.sample_rate_hz * t_indices
                iq += amp * np.exp(1j * phase)

            elif sig.signal_type == "wideband":
                # Sum of subcarriers spread across bandwidth
                amp_per_sc = _dbm_to_linear_amplitude(sig.power_dbm) / np.sqrt(
                    sig.num_subcarriers
                )
                bw = sig.bandwidth_hz
                freqs = np.linspace(
                    sig.freq_offset_hz - bw / 2,
                    sig.freq_offset_hz + bw / 2,
                    sig.num_subcarriers,
                )
                phases = self._wideband_phases[i]
                for k in range(sig.num_subcarriers):
                    phase_k = (
                        2.0 * np.pi * freqs[k] / self.sample_rate_hz * t_indices
                        + phases[k]
                    )
                    iq += amp_per_sc * np.exp(1j * phase_k)

        self._sample_index += num_samples
        return iq.astype(np.complex64)


# ---------------------------------------------------------------------------
# USRP B210 source (placeholder)
# ---------------------------------------------------------------------------

class USRPSource(IQSource):
    """USRP B210 IQ source — not yet implemented.

    Will use UHD Python API when hardware is available.
    """

    async def start(self) -> None:
        raise NotImplementedError("USRPSource requires UHD — not yet implemented")

    async def stop(self) -> None:
        raise NotImplementedError("USRPSource requires UHD — not yet implemented")

    async def read(self, num_samples: int) -> np.ndarray:
        raise NotImplementedError("USRPSource requires UHD — not yet implemented")
