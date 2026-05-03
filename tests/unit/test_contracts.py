"""Tests for pipeline data contracts."""

import numpy as np
import pytest

from src.pipeline.contracts import (
    BearingEstimate,
    Burst,
    Candidate,
    ChannelRole,
    Classification,
    DetectionVerdict,
    DualIQFrame,
    IQChannelFrame,
    PSDFrame,
    RFEvent,
    SignalFamily,
    SpectrogramFrame,
    TrackedEmitter,
    VerdictLabel,
)


def _iq(role: ChannelRole, channel_index: int = 0) -> IQChannelFrame:
    return IQChannelFrame(
        role=role,
        channel_index=channel_index,
        frame_index=3,
        timestamp_s=10.0,
        sample_rate_hz=30.72e6,
        center_freq_hz=2.437e9,
        antenna_port="RX2" if role == ChannelRole.OMNI else "TX/RX",
        iq=np.ones(1024, dtype=np.complex64),
        azimuth_deg=45.0,
    )


def _event(event_id: str = "evt-1") -> RFEvent:
    return RFEvent(
        event_id=event_id,
        role=ChannelRole.YAGI,
        start_time_s=10.0,
        end_time_s=10.2,
        center_freq_hz=2.437e9,
        bandwidth_hz=10e6,
        peak_power_dbm=-65.0,
        snr_db=24.0,
        family=SignalFamily.OFDM,
        source="cfar",
        bin_start=100,
        bin_end=180,
        duty_cycle=0.2,
        persistence_score=0.6,
        features={"burst_period_s": 0.6},
    )


class TestIQChannelFrame:
    def test_duration_from_sample_count(self):
        frame = _iq(ChannelRole.OMNI)
        assert frame.num_samples == 1024
        assert frame.duration_sec == pytest.approx(1024 / 30.72e6)

    def test_rejects_real_samples(self):
        with pytest.raises(TypeError, match="complex"):
            IQChannelFrame(
                role=ChannelRole.OMNI,
                channel_index=0,
                frame_index=0,
                timestamp_s=0.0,
                sample_rate_hz=1.0,
                center_freq_hz=2.4e9,
                antenna_port="RX2",
                iq=np.ones(8, dtype=np.float32),
            )


class TestDualIQFrame:
    def test_channels_are_addressable_by_role(self):
        omni = _iq(ChannelRole.OMNI, channel_index=0)
        yagi = _iq(ChannelRole.YAGI, channel_index=1)
        frame = DualIQFrame(
            frame_index=3,
            timestamp_s=10.0,
            rx_a=omni,
            rx_b=yagi,
        )

        assert frame.channels == (omni, yagi)
        assert frame.by_role(ChannelRole.OMNI) is omni
        assert frame.by_role(ChannelRole.YAGI) is yagi

    def test_rejects_swapped_roles(self):
        with pytest.raises(ValueError, match="rx_a"):
            DualIQFrame(
                frame_index=0,
                timestamp_s=0.0,
                rx_a=_iq(ChannelRole.YAGI, channel_index=0),
                rx_b=_iq(ChannelRole.OMNI, channel_index=1),
            )


class TestPSDFrame:
    def test_spectrum_properties(self):
        freq = np.linspace(2.42e9, 2.45e9, 8)
        power = np.array([-90, -89, -80, -70, -88, -87, -86, -85], dtype=float)
        frame = PSDFrame(
            role=ChannelRole.YAGI,
            frame_index=1,
            timestamp_s=1.0,
            sample_rate_hz=30.72e6,
            center_freq_hz=2.437e9,
            freq_hz=freq,
            power_dbm=power,
        )

        assert frame.bin_width_hz == pytest.approx(30.72e6 / 8)
        assert frame.peak_power_dbm == -70.0
        assert frame.peak_freq_hz == pytest.approx(freq[3])
        assert frame.median_noise_dbm == pytest.approx(np.median(power))

    def test_rejects_shape_mismatch(self):
        with pytest.raises(ValueError, match="same shape"):
            PSDFrame(
                role=ChannelRole.YAGI,
                frame_index=1,
                timestamp_s=1.0,
                sample_rate_hz=30.72e6,
                center_freq_hz=2.437e9,
                freq_hz=np.ones(8),
                power_dbm=np.ones(7),
            )


class TestRFEvent:
    def test_duration_and_feature_freeze(self):
        event = _event()

        assert event.duration_sec == pytest.approx(0.2)
        assert event.features["burst_period_s"] == 0.6
        with pytest.raises(TypeError):
            event.features["x"] = 1

    def test_rejects_invalid_probability(self):
        with pytest.raises(ValueError, match="duty_cycle"):
            RFEvent(
                event_id="bad",
                role=ChannelRole.YAGI,
                start_time_s=0.0,
                end_time_s=1.0,
                center_freq_hz=2.437e9,
                bandwidth_hz=1e6,
                peak_power_dbm=-70.0,
                snr_db=10.0,
                duty_cycle=1.5,
            )


class TestTrackedEmitter:
    def test_latest_event_and_time_span(self):
        first = _event("evt-1")
        second = RFEvent(
            event_id="evt-2",
            role=ChannelRole.YAGI,
            start_time_s=10.4,
            end_time_s=10.6,
            center_freq_hz=2.437e9,
            bandwidth_hz=10e6,
            peak_power_dbm=-64.0,
            snr_db=25.0,
        )
        track = TrackedEmitter(
            track_id="trk-1",
            events=[first, second],
            current_bearing_deg=52.0,
            confidence=0.7,
        )

        assert track.events == (first, second)
        assert track.start_time_s == first.start_time_s
        assert track.end_time_s == second.end_time_s
        assert track.latest_event is second


class TestDetectionVerdict:
    def test_event_verdict(self):
        event = _event()
        verdict = DetectionVerdict(
            label=VerdictLabel.DRONE_LIKELY,
            confidence=0.75,
            reasons=["moving bearing", "ofdm burst cadence"],
            event=event,
            protocol=SignalFamily.OFDM,
        )

        assert verdict.reasons == ("moving bearing", "ofdm burst cadence")
        assert verdict.event is event
        assert verdict.protocol == SignalFamily.OFDM

    def test_requires_evidence_target(self):
        with pytest.raises(ValueError, match="event or track"):
            DetectionVerdict(
                label=VerdictLabel.UNKNOWN_RF,
                confidence=0.5,
            )


# ---------------------------------------------------------------------------
# V2 message types
# ---------------------------------------------------------------------------


def _spectrogram(num_frames: int = 4, num_bins: int = 8) -> SpectrogramFrame:
    timestamps = np.linspace(0.0, 0.1, num_frames)
    freq = np.linspace(2.42e9, 2.45e9, num_bins)
    power = np.full((num_frames, num_bins), -85.0)
    return SpectrogramFrame(
        role=ChannelRole.OMNI,
        frame_index=num_frames - 1,
        sample_rate_hz=30.72e6,
        center_freq_hz=2.437e9,
        timestamps_s=timestamps,
        freq_hz=freq,
        power_dbm=power,
    )


def _burst(burst_id: str = "b-1") -> Burst:
    return Burst(
        burst_id=burst_id,
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        freq_lo_hz=2.436e9,
        freq_hi_hz=2.4368e9,
        peak_power_dbm=-60.0,
        snr_db=18.0,
        bin_start=120,
        bin_end=140,
        frame_start_index=10,
        frame_end_index=12,
    )


def _candidate(candidate_id: str = "c-1") -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.020,
        center_freq_hz=2.4368e9,
        bandwidth_hz=800e3,
        bursts=(_burst("b-1"), _burst("b-2")),
        hop_rate_hz=500.0,
        duty_cycle=0.05,
        features={"hop_count": 10},
    )


class TestSpectrogramFrame:
    def test_basic_properties(self):
        frame = _spectrogram(num_frames=4, num_bins=8)
        assert frame.num_frames == 4
        assert frame.num_bins == 8
        assert frame.history_sec == pytest.approx(0.1)
        np.testing.assert_array_equal(frame.latest_psd_dbm, frame.power_dbm[-1])

    def test_rejects_2d_axis_mismatch(self):
        with pytest.raises(ValueError, match="rows must match"):
            SpectrogramFrame(
                role=ChannelRole.OMNI,
                frame_index=0,
                sample_rate_hz=30.72e6,
                center_freq_hz=2.437e9,
                timestamps_s=np.linspace(0.0, 0.1, 4),
                freq_hz=np.linspace(2.42e9, 2.45e9, 8),
                power_dbm=np.full((3, 8), -85.0),
            )

    def test_rejects_non_2d_power(self):
        with pytest.raises(ValueError, match="two-dimensional"):
            SpectrogramFrame(
                role=ChannelRole.OMNI,
                frame_index=0,
                sample_rate_hz=30.72e6,
                center_freq_hz=2.437e9,
                timestamps_s=np.linspace(0.0, 0.1, 4),
                freq_hz=np.linspace(2.42e9, 2.45e9, 8),
                power_dbm=np.full(8, -85.0),
            )


class TestBurst:
    def test_derived_properties(self):
        b = _burst()
        assert b.duration_sec == pytest.approx(0.002)
        assert b.bandwidth_hz == pytest.approx(0.8e6)
        assert b.center_freq_hz == pytest.approx(2.4364e9)

    def test_rejects_negative_time_span(self):
        with pytest.raises(ValueError, match="end_time_s"):
            Burst(
                burst_id="bad",
                role=ChannelRole.OMNI,
                start_time_s=1.0,
                end_time_s=0.5,
                freq_lo_hz=2.436e9,
                freq_hi_hz=2.4368e9,
                peak_power_dbm=-60.0,
                snr_db=18.0,
                bin_start=0,
                bin_end=10,
                frame_start_index=0,
                frame_end_index=1,
            )

    def test_rejects_inverted_freq(self):
        with pytest.raises(ValueError, match="freq_hi_hz"):
            Burst(
                burst_id="bad",
                role=ChannelRole.OMNI,
                start_time_s=0.0,
                end_time_s=0.001,
                freq_lo_hz=2.4368e9,
                freq_hi_hz=2.436e9,
                peak_power_dbm=-60.0,
                snr_db=18.0,
                bin_start=0,
                bin_end=10,
                frame_start_index=0,
                frame_end_index=1,
            )


class TestCandidate:
    def test_freezes_bursts_and_features(self):
        c = _candidate()
        assert isinstance(c.bursts, tuple)
        assert c.num_bursts == 2
        assert c.duration_sec == pytest.approx(0.020)
        assert c.features["hop_count"] == 10
        with pytest.raises(TypeError):
            c.features["x"] = 1

    def test_requires_at_least_one_burst(self):
        with pytest.raises(ValueError, match="at least one"):
            Candidate(
                candidate_id="bad",
                role=ChannelRole.OMNI,
                start_time_s=0.0,
                end_time_s=0.1,
                center_freq_hz=2.437e9,
                bandwidth_hz=1e6,
                bursts=(),
            )

    def test_rejects_invalid_duty_cycle(self):
        with pytest.raises(ValueError, match="duty_cycle"):
            Candidate(
                candidate_id="bad",
                role=ChannelRole.OMNI,
                start_time_s=0.0,
                end_time_s=0.1,
                center_freq_hz=2.437e9,
                bandwidth_hz=1e6,
                bursts=(_burst(),),
                duty_cycle=1.5,
            )


class TestClassification:
    def test_basic(self):
        c = _candidate()
        cls = Classification(
            candidate=c,
            protocol=SignalFamily.FHSS,
            confidence=0.8,
            reasons=("burst width matches ELRS",),
            features={"hop_pattern": "elrs-2g4"},
        )
        assert cls.candidate_id == "c-1"
        assert cls.protocol == SignalFamily.FHSS
        assert cls.reasons == ("burst width matches ELRS",)
        with pytest.raises(TypeError):
            cls.features["x"] = 1

    def test_rejects_invalid_confidence(self):
        with pytest.raises(ValueError, match="confidence"):
            Classification(
                candidate=_candidate(),
                protocol=SignalFamily.UNKNOWN,
                confidence=1.5,
            )


class TestBearingEstimate:
    def test_optional_sweep(self):
        b = BearingEstimate(
            candidate_id="c-1",
            bearing_deg=128.0,
            confidence=0.6,
            peak_power_dbm=-55.0,
        )
        assert b.sweep_powers_dbm is None
        assert b.sweep_azimuths_deg is None

    def test_sweep_arrays_must_match_shape(self):
        with pytest.raises(ValueError, match="same shape"):
            BearingEstimate(
                candidate_id="c-1",
                bearing_deg=128.0,
                confidence=0.6,
                peak_power_dbm=-55.0,
                sweep_powers_dbm=np.zeros(8),
                sweep_azimuths_deg=np.zeros(7),
            )
