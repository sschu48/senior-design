"""Tests for src.dsp.classifiers."""

from __future__ import annotations

import pytest

from src.dsp.classifiers import elrs, ocusync, wifi
from src.pipeline.contracts import Burst, Candidate, ChannelRole, SignalFamily


def _burst(
    *,
    bandwidth_hz: float,
    duration_s: float = 0.002,
    peak_dbm: float = -55.0,
    snr_db: float = 20.0,
) -> Burst:
    half = bandwidth_hz / 2
    center = 2.437e9
    return Burst(
        burst_id="b-1",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=duration_s,
        freq_lo_hz=center - half,
        freq_hi_hz=center + half,
        peak_power_dbm=peak_dbm,
        snr_db=snr_db,
        bin_start=100,
        bin_end=200,
        frame_start_index=0,
        frame_end_index=10,
    )


def _candidate(
    *,
    bandwidth_hz: float,
    duration_s: float = 0.002,
    bursts: tuple[Burst, ...] | None = None,
    duty_cycle: float | None = None,
    hop_rate_hz: float | None = None,
) -> Candidate:
    if bursts is None:
        bursts = (_burst(bandwidth_hz=bandwidth_hz, duration_s=duration_s),)
    return Candidate(
        candidate_id="c-1",
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=duration_s,
        center_freq_hz=2.437e9,
        bandwidth_hz=bandwidth_hz,
        bursts=bursts,
        duty_cycle=duty_cycle,
        hop_rate_hz=hop_rate_hz,
    )


class TestELRS:
    def test_matches_typical_elrs_burst(self):
        cand = _candidate(bandwidth_hz=800e3, duration_s=0.002)
        cls = elrs.classify(cand)
        assert cls is not None
        assert cls.protocol == SignalFamily.FHSS
        assert cls.confidence >= 0.5

    def test_hop_evidence_raises_confidence(self):
        bursts = tuple(_burst(bandwidth_hz=800e3, duration_s=0.002) for _ in range(4))
        cand = _candidate(bandwidth_hz=800e3, bursts=bursts, hop_rate_hz=300.0)
        cls = elrs.classify(cand)
        assert cls is not None
        assert cls.confidence > 0.7

    def test_rejects_too_wide(self):
        cand = _candidate(bandwidth_hz=10e6, duration_s=0.002)
        assert elrs.classify(cand) is None

    def test_rejects_long_burst(self):
        cand = _candidate(bandwidth_hz=800e3, duration_s=0.050)
        assert elrs.classify(cand) is None


class TestOcuSync:
    def test_matches_10mhz_ofdm(self):
        cand = _candidate(bandwidth_hz=10e6, duration_s=0.001, duty_cycle=0.6)
        cls = ocusync.classify(cand)
        assert cls is not None
        assert cls.protocol == SignalFamily.OFDM
        assert cls.confidence >= 0.6

    def test_rejects_narrow(self):
        cand = _candidate(bandwidth_hz=800e3)
        assert ocusync.classify(cand) is None

    def test_rejects_wifi_width(self):
        cand = _candidate(bandwidth_hz=20e6)
        assert ocusync.classify(cand) is None


class TestWiFi:
    def test_matches_20mhz_continuous(self):
        cand = _candidate(bandwidth_hz=20e6, duty_cycle=0.5)
        cls = wifi.classify(cand)
        assert cls is not None
        assert cls.protocol == SignalFamily.WIFI
        assert cls.confidence >= 0.7

    def test_rejects_narrow(self):
        cand = _candidate(bandwidth_hz=800e3)
        assert wifi.classify(cand) is None
