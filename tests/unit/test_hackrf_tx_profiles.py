"""Tests for tools.hackrf_tx.profiles — IQ resolution and caching."""

import numpy as np
import pytest

from tools.hackrf_tx.config import HackRFTxProfileConfig
from tools.hackrf_tx.profiles import resolve_profile_iq
from tools.hackrf_tx.synth import cs8_to_cf32


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> HackRFTxProfileConfig:
    defaults = dict(
        name="unit_test",
        description="",
        iq_source="synth_tone",
        iq_file="",
        center_freq_hz=2.437e9,
        sample_rate_hz=2e6,
        bandwidth_hz=0.0,
        burst_duration_s=0.001,
        period_s=0.0,
        num_subcarriers=0,
    )
    defaults.update(overrides)
    return HackRFTxProfileConfig(**defaults)


# ---------------------------------------------------------------------------
# Synthesized profiles
# ---------------------------------------------------------------------------

class TestSynthResolution:
    def test_tone_creates_cs8_file(self, tmp_path):
        prof = _make_profile(name="tone", iq_source="synth_tone")
        path = resolve_profile_iq(prof, cache_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".cs8"
        # 1 ms @ 2 MS/s = 2000 cs8 pairs = 4000 bytes
        assert path.stat().st_size == 4000

    def test_ofdm_burst_creates_cs8_with_period_padding(self, tmp_path):
        prof = _make_profile(
            name="ofdm_burst",
            iq_source="synth_ofdm_burst",
            sample_rate_hz=10e6,
            bandwidth_hz=8e6,
            burst_duration_s=0.001,
            period_s=0.010,
            num_subcarriers=64,
        )
        path = resolve_profile_iq(prof, cache_dir=tmp_path)
        # 10 ms @ 10 MS/s = 100k cs8 pairs = 200k bytes
        assert path.stat().st_size == 200_000

    def test_ofdm_continuous_no_padding(self, tmp_path):
        prof = _make_profile(
            name="ocusync",
            iq_source="synth_ofdm_continuous",
            sample_rate_hz=10e6,
            bandwidth_hz=8e6,
            burst_duration_s=0.005,
            period_s=0.0,
            num_subcarriers=64,
        )
        path = resolve_profile_iq(prof, cache_dir=tmp_path)
        # 5 ms @ 10 MS/s = 50k cs8 pairs = 100k bytes
        assert path.stat().st_size == 100_000

    def test_cache_reuse(self, tmp_path):
        prof = _make_profile(name="cached", iq_source="synth_tone")
        p1 = resolve_profile_iq(prof, cache_dir=tmp_path)
        mtime1 = p1.stat().st_mtime_ns

        # Second call should not regenerate.
        p2 = resolve_profile_iq(prof, cache_dir=tmp_path)
        assert p2 == p1
        assert p2.stat().st_mtime_ns == mtime1

    def test_force_regenerate(self, tmp_path):
        prof = _make_profile(name="regen", iq_source="synth_tone")
        p1 = resolve_profile_iq(prof, cache_dir=tmp_path)
        p2 = resolve_profile_iq(prof, cache_dir=tmp_path, force_regenerate=True)
        # Same path, but should have been re-written. Just verify it's still valid.
        assert p2 == p1
        assert p2.stat().st_size > 0


# ---------------------------------------------------------------------------
# File-based profiles (replay)
# ---------------------------------------------------------------------------

class TestFileResolution:
    def test_cs8_passthrough(self, tmp_path):
        # Pre-existing cs8 file should be returned as-is.
        src = tmp_path / "capture.cs8"
        np.zeros(100, dtype=np.int8).tofile(src)

        prof = _make_profile(name="replay", iq_source="file", iq_file=str(src))
        path = resolve_profile_iq(prof, cache_dir=tmp_path / "cache")
        assert path == src

    def test_cf32_converted_to_cs8_in_cache(self, tmp_path):
        # cf32 file should be converted into the cache as cs8.
        src = tmp_path / "capture.cf32"
        rng = np.random.default_rng(0)
        iq = (rng.normal(0, 0.3, 500) + 1j * rng.normal(0, 0.3, 500)).astype(np.complex64)
        iq /= np.max(np.abs(iq))
        iq *= 0.9
        iq.tofile(src)

        prof = _make_profile(name="replay", iq_source="file", iq_file=str(src))
        cache = tmp_path / "cache"
        path = resolve_profile_iq(prof, cache_dir=cache)
        assert path.suffix == ".cs8"
        assert path.parent == cache
        assert path.stat().st_size == 1000  # 500 samples × 2 bytes

        # cf32 → cs8 → cf32 preserves shape up to a constant scale factor.
        cs8 = np.fromfile(path, dtype=np.int8)
        recovered = cs8_to_cf32(cs8)
        alpha = float(np.real(np.vdot(recovered, iq) / np.vdot(recovered, recovered)))
        max_err = np.max(np.abs(recovered * alpha - iq))
        assert max_err < 0.02, f"residual {max_err:.4f}"

    def test_override_via_iq_file_argument(self, tmp_path):
        src = tmp_path / "override.cs8"
        np.zeros(50, dtype=np.int8).tofile(src)

        # Profile has empty iq_file but caller overrides.
        prof = _make_profile(name="replay", iq_source="file", iq_file="")
        path = resolve_profile_iq(prof, cache_dir=tmp_path / "cache", iq_file_override=str(src))
        assert path == src

    def test_missing_file_raises(self, tmp_path):
        prof = _make_profile(
            name="missing", iq_source="file",
            iq_file=str(tmp_path / "does_not_exist.cs8"),
        )
        with pytest.raises(FileNotFoundError):
            resolve_profile_iq(prof, cache_dir=tmp_path / "cache")

    def test_no_iq_file_set_raises(self, tmp_path):
        prof = _make_profile(name="empty", iq_source="file", iq_file="")
        with pytest.raises(ValueError, match="iq_file"):
            resolve_profile_iq(prof, cache_dir=tmp_path / "cache")
