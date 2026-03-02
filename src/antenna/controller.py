"""Antenna controller for SENTINEL pan/tilt mount.

Provides an abstract controller interface and a SimulatedController that
models physical slew rate, scan sweeps, cue-to-bearing, and track
oscillation — all without hardware.
"""

from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field


class ScanMode(enum.Enum):
    """Antenna operating modes."""

    IDLE = "IDLE"
    SCAN = "SCAN"
    CUE = "CUE"
    TRACK = "TRACK"


@dataclass
class AntennaState:
    """Snapshot of the current antenna state."""

    azimuth_deg: float
    elevation_deg: float
    mode: ScanMode
    target_azimuth_deg: float | None = None
    is_moving: bool = False


class AntennaController(abc.ABC):
    """Abstract base class for antenna controllers."""

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    @abc.abstractmethod
    def get_state(self) -> AntennaState: ...

    @abc.abstractmethod
    def set_mode(self, mode: ScanMode) -> None: ...

    @abc.abstractmethod
    def cue_to(self, azimuth_deg: float) -> None: ...

    @abc.abstractmethod
    def tick(self, dt: float) -> None:
        """Advance the controller by *dt* seconds."""
        ...


# ---------------------------------------------------------------------------
# Simulated controller
# ---------------------------------------------------------------------------

@dataclass
class SimulatedController(AntennaController):
    """Software-only antenna controller for testing.

    Models a pan-only mount with realistic slew rate limiting.

    Parameters
    ----------
    azimuth_min_deg : float
        Lower azimuth bound.
    azimuth_max_deg : float
        Upper azimuth bound.
    slew_rate_deg_per_sec : float
        Maximum angular velocity.
    scan_speed_deg_per_sec : float
        Speed during SCAN sweep.
    elevation_deg : float
        Fixed elevation angle.
    cue_timeout_sec : float
        Time in CUE mode before reverting to SCAN.
    track_oscillation_deg : float
        Half-width of oscillation around track bearing.
    track_lost_timeout_sec : float
        Time in TRACK without refresh before reverting to SCAN.
    """

    azimuth_min_deg: float = 0.0
    azimuth_max_deg: float = 360.0
    slew_rate_deg_per_sec: float = 60.0
    scan_speed_deg_per_sec: float = 30.0
    elevation_deg: float = 10.0
    cue_timeout_sec: float = 5.0
    track_oscillation_deg: float = 15.0
    track_lost_timeout_sec: float = 10.0

    # Internal state
    _azimuth: float = field(default=0.0, init=False, repr=False)
    _mode: ScanMode = field(default=ScanMode.IDLE, init=False, repr=False)
    _target_az: float | None = field(default=None, init=False, repr=False)
    _scan_direction: int = field(default=1, init=False, repr=False)  # +1 or -1
    _mode_timer: float = field(default=0.0, init=False, repr=False)
    _track_center: float = field(default=0.0, init=False, repr=False)
    _track_direction: int = field(default=1, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    def start(self) -> None:
        self._azimuth = self.azimuth_min_deg
        self._mode = ScanMode.IDLE
        self._target_az = None
        self._scan_direction = 1
        self._mode_timer = 0.0
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._mode = ScanMode.IDLE

    def get_state(self) -> AntennaState:
        is_moving = self._mode in (ScanMode.SCAN, ScanMode.CUE, ScanMode.TRACK)
        return AntennaState(
            azimuth_deg=self._azimuth,
            elevation_deg=self.elevation_deg,
            mode=self._mode,
            target_azimuth_deg=self._target_az,
            is_moving=is_moving,
        )

    def set_mode(self, mode: ScanMode) -> None:
        self._mode = mode
        self._mode_timer = 0.0
        if mode == ScanMode.SCAN:
            self._target_az = None

    def cue_to(self, azimuth_deg: float) -> None:
        """Switch to CUE mode and slew toward target bearing."""
        self._mode = ScanMode.CUE
        self._target_az = max(self.azimuth_min_deg, min(azimuth_deg, self.azimuth_max_deg))
        self._mode_timer = 0.0

    def start_track(self, azimuth_deg: float) -> None:
        """Switch to TRACK mode, oscillating around the given bearing."""
        self._mode = ScanMode.TRACK
        self._track_center = max(self.azimuth_min_deg, min(azimuth_deg, self.azimuth_max_deg))
        self._target_az = self._track_center
        self._track_direction = 1
        self._mode_timer = 0.0

    def refresh_track(self) -> None:
        """Reset the TRACK lost timer (call on each new detection)."""
        self._mode_timer = 0.0

    def _clamp(self, az: float) -> float:
        return max(self.azimuth_min_deg, min(az, self.azimuth_max_deg))

    def _move_toward(self, target: float, speed: float, dt: float) -> None:
        """Move azimuth toward target at given speed, respecting slew limit."""
        effective_speed = min(speed, self.slew_rate_deg_per_sec)
        max_step = effective_speed * dt
        diff = target - self._azimuth
        if abs(diff) <= max_step:
            self._azimuth = target
        else:
            self._azimuth += max_step * (1 if diff > 0 else -1)
        self._azimuth = self._clamp(self._azimuth)

    def tick(self, dt: float) -> None:
        """Advance simulation by *dt* seconds."""
        if not self._running:
            return

        self._mode_timer += dt

        if self._mode == ScanMode.IDLE:
            # Hold position
            return

        elif self._mode == ScanMode.SCAN:
            self._tick_scan(dt)

        elif self._mode == ScanMode.CUE:
            self._tick_cue(dt)

        elif self._mode == ScanMode.TRACK:
            self._tick_track(dt)

    def _tick_scan(self, dt: float) -> None:
        """Sweep back and forth across the azimuth range."""
        step = self.scan_speed_deg_per_sec * dt * self._scan_direction
        self._azimuth += step
        self._azimuth = self._clamp(self._azimuth)

        # Reverse at limits
        if self._azimuth >= self.azimuth_max_deg:
            self._scan_direction = -1
        elif self._azimuth <= self.azimuth_min_deg:
            self._scan_direction = 1

    def _tick_cue(self, dt: float) -> None:
        """Slew toward cue target, timeout to SCAN if target not reached."""
        if self._target_az is not None:
            self._move_toward(self._target_az, self.slew_rate_deg_per_sec, dt)

        # Timeout → revert to SCAN
        if self._mode_timer >= self.cue_timeout_sec:
            self.set_mode(ScanMode.SCAN)

    def _tick_track(self, dt: float) -> None:
        """Oscillate around track center, timeout to SCAN on signal loss."""
        # Compute oscillation bounds
        osc_min = self._clamp(self._track_center - self.track_oscillation_deg)
        osc_max = self._clamp(self._track_center + self.track_oscillation_deg)

        # Oscillate
        target = osc_max if self._track_direction == 1 else osc_min
        self._move_toward(target, self.scan_speed_deg_per_sec, dt)

        # Reverse at oscillation limits
        if self._azimuth >= osc_max:
            self._track_direction = -1
        elif self._azimuth <= osc_min:
            self._track_direction = 1

        # Lost timeout → revert to SCAN
        if self._mode_timer >= self.track_lost_timeout_sec:
            self.set_mode(ScanMode.SCAN)
