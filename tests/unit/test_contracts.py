"""Tests for pipeline data contracts."""

import numpy as np
import pytest

from src.pipeline.contracts import (
    ChannelRole,
    DetectionVerdict,
    DualIQFrame,
    IQChannelFrame,
    PSDFrame,
    RFEvent,
    SignalFamily,
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
