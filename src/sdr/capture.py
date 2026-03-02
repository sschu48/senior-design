"""IQ sample sources for SENTINEL.

Provides an abstract IQSource interface and concrete implementations:
- SyntheticSource: software-generated IQ for testing without hardware
- USRPSource: USRP B210 IQ source via UHD Python API
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from src.sdr.config import RxChannelConfig

logger = logging.getLogger("sentinel.sdr")


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
# USRP B210 source
# ---------------------------------------------------------------------------

# Maximum consecutive recv timeouts before raising an error
_MAX_TIMEOUT_RETRIES = 3


@dataclass
class USRPSource(IQSource):
    """USRP B210 IQ source via UHD Python API.

    Configures a single RX channel and streams complex64 IQ samples.
    The blocking UHD ``recv()`` call is wrapped in ``asyncio.to_thread()``
    to avoid stalling the event loop.

    Parameters
    ----------
    channel_config : RxChannelConfig
        RX channel parameters (freq, rate, gain, antenna, bandwidth).
    device_args : str
        UHD device arguments (e.g. ``"serial=31E345B"``).  Empty string
        means auto-detect.
    channel : int
        RX channel index on the USRP (0 or 1 for B210).
    recv_timeout : float
        Timeout in seconds for each ``recv()`` call.
    """

    channel_config: RxChannelConfig
    device_args: str = ""
    channel: int = 0
    recv_timeout: float = 1.0

    # Internal state
    _usrp: object = field(default=None, init=False, repr=False)
    _streamer: object = field(default=None, init=False, repr=False)
    _recv_buf: np.ndarray | None = field(default=None, init=False, repr=False)
    _metadata: object = field(default=None, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    async def start(self) -> None:
        import uhd

        cfg = self.channel_config
        ch = self.channel

        # Create USRP device
        self._usrp = uhd.usrp.MultiUSRP(self.device_args)
        usrp = self._usrp

        # Configure RX chain
        usrp.set_rx_rate(cfg.sample_rate_hz, ch)
        usrp.set_rx_freq(uhd.types.TuneRequest(cfg.center_freq_hz), ch)
        usrp.set_rx_gain(cfg.gain_db, ch)
        usrp.set_rx_antenna(cfg.antenna, ch)
        usrp.set_rx_bandwidth(cfg.bandwidth_hz, ch)

        logger.info(
            "USRP configured: %.3f GHz, %.2f MSPS, %.1f dB gain, ant=%s, ch=%d",
            cfg.center_freq_hz / 1e9,
            cfg.sample_rate_hz / 1e6,
            cfg.gain_db,
            cfg.antenna,
            ch,
        )

        # Create RX streamer
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [ch]
        self._streamer = usrp.get_rx_stream(stream_args)

        # Allocate recv buffer (max chunk the streamer can handle per call)
        max_samps = self._streamer.get_max_num_samps()
        self._recv_buf = np.zeros(max_samps, dtype=np.complex64)
        self._metadata = uhd.types.RXMetadata()

        # Start continuous streaming
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        cmd.stream_now = True
        self._streamer.issue_stream_cmd(cmd)

        self._running = True
        logger.info("USRP streaming started")

    async def stop(self) -> None:
        if self._streamer is not None:
            import uhd

            cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
            self._streamer.issue_stream_cmd(cmd)

        self._running = False
        self._streamer = None
        self._usrp = None
        self._recv_buf = None
        logger.info("USRP streaming stopped")

    async def read(self, num_samples: int) -> np.ndarray:
        if not self._running:
            raise RuntimeError("Source not started — call start() first")
        return await asyncio.to_thread(self._read_sync, num_samples)

    def _read_sync(self, num_samples: int) -> np.ndarray:
        """Blocking read that fills exactly *num_samples* complex64 values."""
        import uhd

        output = np.zeros(num_samples, dtype=np.complex64)
        offset = 0
        timeouts = 0

        while offset < num_samples:
            remaining = num_samples - offset
            # Recv into our chunk buffer (up to max_samps at a time)
            chunk_size = min(remaining, len(self._recv_buf))
            buf = self._recv_buf[:chunk_size]

            n_recv = self._streamer.recv(buf, self._metadata, self.recv_timeout)
            error = self._metadata.error_code

            if error == uhd.types.RXMetadataErrorCode.none:
                output[offset:offset + n_recv] = buf[:n_recv]
                offset += n_recv
                timeouts = 0

            elif error == uhd.types.RXMetadataErrorCode.overflow:
                # Overflow = dropped samples.  Log and continue.
                logger.warning("USRP overflow (dropped samples)")
                if n_recv > 0:
                    output[offset:offset + n_recv] = buf[:n_recv]
                    offset += n_recv

            elif error == uhd.types.RXMetadataErrorCode.timeout:
                timeouts += 1
                if timeouts >= _MAX_TIMEOUT_RETRIES:
                    raise RuntimeError(
                        f"USRP recv timed out {_MAX_TIMEOUT_RETRIES} times consecutively"
                    )
                logger.warning("USRP recv timeout (%d/%d)", timeouts, _MAX_TIMEOUT_RETRIES)

            else:
                raise RuntimeError(f"USRP recv error: {self._metadata.strerror()}")

        return output
