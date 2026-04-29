"""Tests for USRPSource.

Hardware tests (marked @pytest.mark.hardware) require a USRP B210 connected.
Run with: make test-hardware
"""

import numpy as np
import pytest

from src.sdr.capture import USRPDualSource, USRPSource
from src.sdr.config import RxChannelConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rx_config() -> RxChannelConfig:
    """RX config matching the B210 defaults in config.yaml."""
    return RxChannelConfig(
        antenna="RX2",
        center_freq_hz=2.437e9,
        sample_rate_hz=30.72e6,
        bandwidth_hz=30.72e6,
        gain_db=40.0,
        agc=False,
    )


# ===========================================================================
# Unconditional tests (no hardware required)
# ===========================================================================

class TestUSRPSourceFields:
    """Verify dataclass construction without touching hardware."""

    def test_fields_set(self):
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg, device_args="serial=ABC", channel=1)
        assert source.channel_config is cfg
        assert source.device_args == "serial=ABC"
        assert source.channel == 1
        assert source.recv_timeout == 1.0

    def test_defaults(self):
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg)
        assert source.device_args == ""
        assert source.channel == 0

    async def test_read_before_start_raises(self):
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg)
        with pytest.raises(RuntimeError, match="not started"):
            await source.read(1024)


class TestUSRPDualSourceFields:
    """Verify dual-source construction without touching hardware."""

    def test_fields_set(self):
        rx_a = make_rx_config()
        rx_b = make_rx_config()
        source = USRPDualSource(
            rx_a_config=rx_a,
            rx_b_config=rx_b,
            device_args="serial=ABC",
            channels=(0, 1),
        )

        assert source.rx_a_config is rx_a
        assert source.rx_b_config is rx_b
        assert source.device_args == "serial=ABC"
        assert source.channels == (0, 1)

    async def test_read_before_start_raises(self):
        cfg = make_rx_config()
        source = USRPDualSource(rx_a_config=cfg, rx_b_config=cfg)
        with pytest.raises(RuntimeError, match="not started"):
            await source.read(1024)


# ===========================================================================
# Hardware tests (require USRP B210 connected)
# ===========================================================================

@pytest.mark.hardware
class TestUSRPHardware:
    """Live B210 tests. Skipped unless -m hardware is specified."""

    async def test_start_stop(self):
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg)

        await source.start()
        assert source._running is True
        assert source._streamer is not None

        await source.stop()
        assert source._running is False
        assert source._streamer is None

    async def test_read_samples(self):
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg)

        await source.start()
        try:
            iq = await source.read(4096)
            assert iq.dtype == np.complex64
            assert iq.shape == (4096,)
            # Samples should not be all zeros (antenna picks up noise)
            assert np.any(iq != 0)
        finally:
            await source.stop()

    async def test_read_multiple(self):
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg)

        await source.start()
        try:
            iq1 = await source.read(2048)
            iq2 = await source.read(2048)
            assert iq1.shape == (2048,)
            assert iq2.shape == (2048,)
            # Successive reads should return different data
            assert not np.array_equal(iq1, iq2)
        finally:
            await source.stop()

    async def test_large_read(self):
        """Read more samples than max_samps_per_chunk to exercise the loop."""
        cfg = make_rx_config()
        source = USRPSource(channel_config=cfg)

        await source.start()
        try:
            # 32k samples should require multiple recv() calls
            iq = await source.read(32768)
            assert iq.shape == (32768,)
            assert iq.dtype == np.complex64
        finally:
            await source.stop()
