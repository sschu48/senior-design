"""Shared synthetic signal definitions for SENTINEL.

Used by the dashboard, spectrum analyzer, pipeline runner, and bench test
to generate consistent test signals without copy-pasting.
"""

from src.sdr.capture import SignalDef

# DJI OcuSync-like: wideband OFDM centered +5 MHz from center freq
DJI_SIGNAL = SignalDef(
    freq_offset_hz=5e6,
    bandwidth_hz=10e6,
    power_dbm=-55.0,
    signal_type="wideband",
    num_subcarriers=128,
)

# RC control link: narrow tone at -8 MHz
RC_TONE = SignalDef(
    freq_offset_hz=-8e6,
    bandwidth_hz=0.0,
    power_dbm=-60.0,
    signal_type="tone",
)

# ELRS-like: narrow spread spectrum at +12 MHz
ELRS_SIGNAL = SignalDef(
    freq_offset_hz=12e6,
    bandwidth_hz=500e3,
    power_dbm=-65.0,
    signal_type="wideband",
    num_subcarriers=16,
)

# Default set for synthetic testing
DEFAULT_SIGNALS = [DJI_SIGNAL, RC_TONE, ELRS_SIGNAL]
