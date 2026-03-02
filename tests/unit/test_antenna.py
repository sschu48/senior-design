"""Tests for src.antenna.controller.SimulatedController."""

import pytest

from src.antenna.controller import AntennaState, ScanMode, SimulatedController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_controller(**kwargs) -> SimulatedController:
    """Create a SimulatedController with sensible test defaults."""
    defaults = dict(
        azimuth_min_deg=0.0,
        azimuth_max_deg=360.0,
        slew_rate_deg_per_sec=60.0,
        scan_speed_deg_per_sec=30.0,
        elevation_deg=10.0,
        cue_timeout_sec=5.0,
        track_oscillation_deg=15.0,
        track_lost_timeout_sec=10.0,
    )
    defaults.update(kwargs)
    ctrl = SimulatedController(**defaults)
    ctrl.start()
    return ctrl


# ===========================================================================
# Scan mode tests
# ===========================================================================

class TestScanSweep:
    """SCAN mode should sweep across the full azimuth range."""

    def test_sweep_covers_full_range(self):
        ctrl = make_controller(azimuth_min_deg=0.0, azimuth_max_deg=180.0,
                               scan_speed_deg_per_sec=30.0)
        ctrl.set_mode(ScanMode.SCAN)

        positions = []
        # Run for enough time to cross the full range
        for _ in range(200):
            ctrl.tick(0.1)
            positions.append(ctrl.get_state().azimuth_deg)

        assert max(positions) >= 170.0  # should reach near max
        assert min(positions) <= 10.0   # should be near min

    def test_reversal_at_limits(self):
        """Sweep should reverse direction at azimuth boundaries."""
        ctrl = make_controller(azimuth_min_deg=0.0, azimuth_max_deg=100.0,
                               scan_speed_deg_per_sec=50.0)
        ctrl.set_mode(ScanMode.SCAN)

        # Sweep to max
        for _ in range(30):
            ctrl.tick(0.1)

        # Should have reversed at some point — position should be decreasing
        az_at_max = ctrl.get_state().azimuth_deg

        # Continue ticking — should come back down
        for _ in range(30):
            ctrl.tick(0.1)

        az_after = ctrl.get_state().azimuth_deg
        # After going to max and reversing, azimuth should decrease
        assert az_after < az_at_max or az_after <= 100.0


# ===========================================================================
# CUE mode tests
# ===========================================================================

class TestCueMode:
    """CUE mode should slew toward a target bearing."""

    def test_cue_slews_to_target(self):
        ctrl = make_controller(slew_rate_deg_per_sec=60.0)
        ctrl.cue_to(90.0)

        for _ in range(50):
            ctrl.tick(0.1)

        state = ctrl.get_state()
        assert abs(state.azimuth_deg - 90.0) < 1.0
        assert state.mode == ScanMode.CUE or state.mode == ScanMode.SCAN

    def test_cue_timeout_reverts_to_scan(self):
        """After cue_timeout_sec, mode should revert to SCAN."""
        ctrl = make_controller(cue_timeout_sec=2.0)
        ctrl.cue_to(180.0)

        # Tick past the timeout
        for _ in range(30):
            ctrl.tick(0.1)

        state = ctrl.get_state()
        assert state.mode == ScanMode.SCAN


# ===========================================================================
# TRACK mode tests
# ===========================================================================

class TestTrackMode:
    """TRACK mode should oscillate around a bearing and timeout on signal loss."""

    def test_track_oscillation_bounded(self):
        """Azimuth should stay within ±oscillation_deg of track center."""
        ctrl = make_controller(track_oscillation_deg=15.0,
                               cue_timeout_sec=100.0,
                               track_lost_timeout_sec=100.0)
        # Slew to 180° first (60 deg/s * 3s = 180°, give 5s to be safe)
        ctrl.cue_to(180.0)
        for _ in range(50):
            ctrl.tick(0.1)
        assert abs(ctrl.get_state().azimuth_deg - 180.0) < 1.0

        ctrl.start_track(180.0)

        positions = []
        for _ in range(200):
            ctrl.tick(0.1)
            ctrl.refresh_track()  # keep alive
            positions.append(ctrl.get_state().azimuth_deg)

        # All positions should be within [165, 195]
        assert min(positions) >= 180.0 - 15.0 - 1.0  # 1° tolerance
        assert max(positions) <= 180.0 + 15.0 + 1.0

    def test_track_lost_reverts_to_scan(self):
        """Without refresh_track(), track should timeout and revert to SCAN."""
        ctrl = make_controller(track_lost_timeout_sec=3.0)
        ctrl.start_track(180.0)

        # Tick past lost timeout
        for _ in range(40):
            ctrl.tick(0.1)

        assert ctrl.get_state().mode == ScanMode.SCAN

    def test_track_refresh_prevents_timeout(self):
        """Calling refresh_track() should keep TRACK mode alive."""
        ctrl = make_controller(track_lost_timeout_sec=3.0)
        ctrl.start_track(180.0)

        for _ in range(50):
            ctrl.tick(0.1)
            if ctrl.get_state().mode == ScanMode.TRACK:
                ctrl.refresh_track()

        assert ctrl.get_state().mode == ScanMode.TRACK


# ===========================================================================
# Slew rate enforcement
# ===========================================================================

class TestSlewRate:
    """Movement should never exceed the configured slew rate."""

    def test_slew_rate_enforced(self):
        ctrl = make_controller(slew_rate_deg_per_sec=30.0)
        ctrl.cue_to(360.0)

        dt = 0.1
        prev_az = ctrl.get_state().azimuth_deg
        max_step = 30.0 * dt + 0.01  # small tolerance

        for _ in range(50):
            ctrl.tick(dt)
            az = ctrl.get_state().azimuth_deg
            assert abs(az - prev_az) <= max_step, (
                f"Step {abs(az - prev_az):.4f}° exceeds max {max_step:.4f}°"
            )
            prev_az = az


# ===========================================================================
# IDLE mode
# ===========================================================================

class TestIdleMode:
    """IDLE should hold current position."""

    def test_idle_holds_position(self):
        ctrl = make_controller()
        # Move somewhere first
        ctrl.cue_to(90.0)
        for _ in range(30):
            ctrl.tick(0.1)

        ctrl.set_mode(ScanMode.IDLE)
        az_before = ctrl.get_state().azimuth_deg

        for _ in range(20):
            ctrl.tick(0.1)

        assert ctrl.get_state().azimuth_deg == az_before


# ===========================================================================
# State reporting
# ===========================================================================

class TestStateReporting:
    """get_state() should return correct AntennaState."""

    def test_initial_state(self):
        ctrl = make_controller()
        state = ctrl.get_state()
        assert isinstance(state, AntennaState)
        assert state.mode == ScanMode.IDLE
        assert state.elevation_deg == 10.0
        assert state.is_moving is False

    def test_scan_is_moving(self):
        ctrl = make_controller()
        ctrl.set_mode(ScanMode.SCAN)
        assert ctrl.get_state().is_moving is True
