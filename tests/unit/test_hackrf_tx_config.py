"""Tests for tools.hackrf_tx.config — yaml parse, validation, profile schema."""

import textwrap
from pathlib import Path

import pytest

from tools.hackrf_tx.config import IQ_SOURCE_TYPES, load_hackrf_tx_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PROFILE_YAML = textwrap.dedent("""
    hackrf_tx:
      device_serial: ""
      default_profile: "test_profile"
      tx_vga_gain_db: 20
      enable_amp: false
      min_rx_separation_m: 3.0
      cache_dir: "data/hackrf_tx_cache"
      profiles:
        test_profile:
          description: "Test profile"
          iq_source: "synth_tone"
          iq_file: ""
          center_freq_hz: 2.437e9
          sample_rate_hz: 2.0e6
          bandwidth_hz: 0.0
          burst_duration_s: 0.010
          period_s: 0.0
          num_subcarriers: 0
""")


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


# ---------------------------------------------------------------------------
# Repo config
# ---------------------------------------------------------------------------

class TestRepoConfig:
    def test_loads_default(self):
        cfg = load_hackrf_tx_config()
        assert cfg.default_profile in cfg.profiles
        assert 0 <= cfg.tx_vga_gain_db <= 47

    def test_amp_off_by_default(self):
        cfg = load_hackrf_tx_config()
        # Hard requirement for the no-attenuator setup: amp must be off in the
        # checked-in config so a fresh clone never auto-enables it.
        assert cfg.enable_amp is False

    def test_min_separation_at_least_one_meter(self):
        cfg = load_hackrf_tx_config()
        assert cfg.min_rx_separation_m >= 1.0

    def test_default_profile_has_safe_values(self):
        cfg = load_hackrf_tx_config()
        p = cfg.profiles[cfg.default_profile]
        assert 2e6 <= p.sample_rate_hz <= 20e6  # HackRF range
        assert 2.4e9 <= p.center_freq_hz <= 2.5e9  # 2.4 GHz ISM


# ---------------------------------------------------------------------------
# Custom config files
# ---------------------------------------------------------------------------

class TestCustomConfig:
    def test_minimal_valid(self, tmp_path):
        path = _write_config(tmp_path, _VALID_PROFILE_YAML)
        cfg = load_hackrf_tx_config(path)
        assert cfg.default_profile == "test_profile"
        assert "test_profile" in cfg.profiles

    def test_missing_section_raises(self, tmp_path):
        path = _write_config(tmp_path, "system:\n  name: foo\n")
        with pytest.raises(KeyError, match="hackrf_tx"):
            load_hackrf_tx_config(path)

    def test_empty_profiles_raises(self, tmp_path):
        body = textwrap.dedent("""
            hackrf_tx:
              device_serial: ""
              default_profile: "x"
              tx_vga_gain_db: 20
              enable_amp: false
              min_rx_separation_m: 3.0
              cache_dir: "/tmp"
              profiles: {}
        """)
        path = _write_config(tmp_path, body)
        with pytest.raises(ValueError, match="empty"):
            load_hackrf_tx_config(path)

    def test_default_profile_must_exist(self, tmp_path):
        body = _VALID_PROFILE_YAML.replace(
            'default_profile: "test_profile"',
            'default_profile: "nonexistent"',
        )
        path = _write_config(tmp_path, body)
        with pytest.raises(ValueError, match="default_profile"):
            load_hackrf_tx_config(path)

    def test_invalid_iq_source_rejected(self, tmp_path):
        body = _VALID_PROFILE_YAML.replace(
            'iq_source: "synth_tone"',
            'iq_source: "bogus_kind"',
        )
        path = _write_config(tmp_path, body)
        with pytest.raises(ValueError, match="iq_source"):
            load_hackrf_tx_config(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_hackrf_tx_config(tmp_path / "no_such_config.yaml")


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------

class TestSchemaConstants:
    def test_iq_source_types_complete(self):
        assert "synth_tone" in IQ_SOURCE_TYPES
        assert "synth_ofdm_burst" in IQ_SOURCE_TYPES
        assert "synth_ofdm_continuous" in IQ_SOURCE_TYPES
        assert "file" in IQ_SOURCE_TYPES
        assert len(IQ_SOURCE_TYPES) == 4
