"""SENTINEL configuration loader.

Reads config.yaml and returns frozen dataclasses for type-safe access.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


def _project_root() -> Path:
    """Walk up from this file to find the repo root (contains config.yaml)."""
    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / "config.yaml").exists():
            return parent
    raise FileNotFoundError("config.yaml not found in any parent directory")


# ---------------------------------------------------------------------------
# Frozen dataclasses — immutable after creation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SystemConfig:
    name: str
    version: str
    log_level: str
    log_format: str
    log_file: str


@dataclass(frozen=True)
class RxChannelConfig:
    antenna: str
    center_freq_hz: float
    sample_rate_hz: float
    bandwidth_hz: float
    gain_db: float
    agc: bool


@dataclass(frozen=True)
class SDRConfig:
    device: str
    driver: str
    rx_a: RxChannelConfig
    rx_b: RxChannelConfig


# --- DSP sub-configs ---

@dataclass(frozen=True)
class TripwireConfig:
    threshold_db: float
    noise_floor_window_sec: float
    min_trigger_duration_ms: float


@dataclass(frozen=True)
class CFARConfig:
    type: str
    guard_cells: int
    reference_cells: int
    threshold_factor_db: float
    min_detection_bw_hz: float


@dataclass(frozen=True)
class RemoteIDDecoderConfig:
    enabled: bool
    channel: int


@dataclass(frozen=True)
class DJIDroneIDDecoderConfig:
    enabled: bool
    min_sample_rate_hz: float
    sync_threshold: float


@dataclass(frozen=True)
class DecoderConfig:
    remote_id: RemoteIDDecoderConfig
    dji_droneid: DJIDroneIDDecoderConfig


@dataclass(frozen=True)
class DSPConfig:
    fft_size: int
    window: str
    overlap: float
    dc_offset_window: int
    tripwire: TripwireConfig
    cfar: CFARConfig
    decoder: DecoderConfig


# --- Antenna configs ---

@dataclass(frozen=True)
class YagiConfig:
    gain_dbi: float
    beamwidth_deg: float
    polarization: str
    connector: str
    max_input_power_w: float


@dataclass(frozen=True)
class OmniConfig:
    gain_dbi: float
    type: str


@dataclass(frozen=True)
class MountConfig:
    type: str
    azimuth_min_deg: float
    azimuth_max_deg: float
    azimuth_speed_deg_per_sec: float
    elevation_enabled: bool
    elevation_deg: float
    control_interface: str
    serial_port: str
    serial_baud: int


@dataclass(frozen=True)
class AntennaConfig:
    yagi: YagiConfig
    omni: OmniConfig
    mount: MountConfig


# --- Top-level sections ---

@dataclass(frozen=True)
class ScanConfig:
    default_mode: str
    scan_speed_deg_per_sec: float
    cue_timeout_sec: float
    track_oscillation_deg: float
    track_lost_timeout_sec: float


@dataclass(frozen=True)
class DetectionConfig:
    min_snr_db: float
    min_confidence: float
    bearing_exclusion_zones: list


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool
    format: str
    pre_trigger_sec: float
    post_trigger_sec: float
    output_dir: str
    sigmf_metadata: bool


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    websocket_path: str


@dataclass(frozen=True)
class SentinelConfig:
    system: SystemConfig
    sdr: SDRConfig
    dsp: DSPConfig
    antenna: AntennaConfig
    scan: ScanConfig
    detection: DetectionConfig
    capture: CaptureConfig
    server: ServerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_rx_channel(raw: dict) -> RxChannelConfig:
    """Build RxChannelConfig with explicit numeric casts.

    PyYAML treats scientific notation without a sign (e.g. ``2.437e9``)
    as a string.  We cast the numeric fields to float here.
    """
    return RxChannelConfig(
        antenna=raw["antenna"],
        center_freq_hz=float(raw["center_freq_hz"]),
        sample_rate_hz=float(raw["sample_rate_hz"]),
        bandwidth_hz=float(raw["bandwidth_hz"]),
        gain_db=float(raw["gain_db"]),
        agc=bool(raw["agc"]),
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(path: str | Path | None = None) -> SentinelConfig:
    """Load config.yaml and return a frozen SentinelConfig.

    Parameters
    ----------
    path : str or Path, optional
        Explicit path to config.yaml.  When *None*, auto-discovers from
        the project root.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist at the resolved path.
    """
    if path is None:
        path = _project_root() / "config.yaml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    # --- system ---
    sys_raw = raw["system"]
    system = SystemConfig(
        name=sys_raw["name"],
        version=sys_raw["version"],
        log_level=sys_raw["log_level"],
        log_format=sys_raw["log_format"],
        log_file=sys_raw["log_file"],
    )

    # --- sdr ---
    sdr_raw = raw["sdr"]
    sdr = SDRConfig(
        device=sdr_raw["device"],
        driver=sdr_raw["driver"],
        rx_a=_parse_rx_channel(sdr_raw["rx_a"]),
        rx_b=_parse_rx_channel(sdr_raw["rx_b"]),
    )

    # --- dsp ---
    dsp_raw = raw["dsp"]

    tripwire_raw = dsp_raw["tripwire"]
    tripwire = TripwireConfig(
        threshold_db=float(tripwire_raw["threshold_db"]),
        noise_floor_window_sec=float(tripwire_raw["noise_floor_window_sec"]),
        min_trigger_duration_ms=float(tripwire_raw["min_trigger_duration_ms"]),
    )

    cfar_raw = dsp_raw["cfar"]
    cfar = CFARConfig(
        type=cfar_raw["type"],
        guard_cells=int(cfar_raw["guard_cells"]),
        reference_cells=int(cfar_raw["reference_cells"]),
        threshold_factor_db=float(cfar_raw["threshold_factor_db"]),
        min_detection_bw_hz=float(cfar_raw["min_detection_bw_hz"]),
    )

    dec_raw = dsp_raw["decoder"]
    rid_raw = dec_raw["remote_id"]
    djid_raw = dec_raw["dji_droneid"]
    decoder = DecoderConfig(
        remote_id=RemoteIDDecoderConfig(
            enabled=bool(rid_raw["enabled"]),
            channel=int(rid_raw["channel"]),
        ),
        dji_droneid=DJIDroneIDDecoderConfig(
            enabled=bool(djid_raw["enabled"]),
            min_sample_rate_hz=float(djid_raw["min_sample_rate_hz"]),
            sync_threshold=float(djid_raw["sync_threshold"]),
        ),
    )

    dsp = DSPConfig(
        fft_size=int(dsp_raw["fft_size"]),
        window=dsp_raw["window"],
        overlap=float(dsp_raw["overlap"]),
        dc_offset_window=int(dsp_raw["dc_offset_window"]),
        tripwire=tripwire,
        cfar=cfar,
        decoder=decoder,
    )

    # --- antenna ---
    ant_raw = raw["antenna"]
    yagi_raw = ant_raw["yagi"]
    yagi = YagiConfig(
        gain_dbi=float(yagi_raw["gain_dbi"]),
        beamwidth_deg=float(yagi_raw["beamwidth_deg"]),
        polarization=yagi_raw["polarization"],
        connector=yagi_raw["connector"],
        max_input_power_w=float(yagi_raw["max_input_power_w"]),
    )

    omni_raw = ant_raw["omni"]
    omni = OmniConfig(
        gain_dbi=float(omni_raw["gain_dbi"]),
        type=omni_raw["type"],
    )

    mount_raw = ant_raw["mount"]
    mount = MountConfig(
        type=mount_raw["type"],
        azimuth_min_deg=float(mount_raw["azimuth_min_deg"]),
        azimuth_max_deg=float(mount_raw["azimuth_max_deg"]),
        azimuth_speed_deg_per_sec=float(mount_raw["azimuth_speed_deg_per_sec"]),
        elevation_enabled=bool(mount_raw["elevation_enabled"]),
        elevation_deg=float(mount_raw["elevation_deg"]),
        control_interface=mount_raw["control_interface"],
        serial_port=mount_raw["serial_port"],
        serial_baud=int(mount_raw["serial_baud"]),
    )

    antenna = AntennaConfig(yagi=yagi, omni=omni, mount=mount)

    # --- scan ---
    scan_raw = raw["scan"]
    scan = ScanConfig(
        default_mode=scan_raw["default_mode"],
        scan_speed_deg_per_sec=float(scan_raw["scan_speed_deg_per_sec"]),
        cue_timeout_sec=float(scan_raw["cue_timeout_sec"]),
        track_oscillation_deg=float(scan_raw["track_oscillation_deg"]),
        track_lost_timeout_sec=float(scan_raw["track_lost_timeout_sec"]),
    )

    # --- detection ---
    det_raw = raw["detection"]
    detection = DetectionConfig(
        min_snr_db=float(det_raw["min_snr_db"]),
        min_confidence=float(det_raw["min_confidence"]),
        bearing_exclusion_zones=list(det_raw.get("bearing_exclusion_zones", [])),
    )

    # --- capture ---
    cap_raw = raw["capture"]
    capture = CaptureConfig(
        enabled=bool(cap_raw["enabled"]),
        format=cap_raw["format"],
        pre_trigger_sec=float(cap_raw["pre_trigger_sec"]),
        post_trigger_sec=float(cap_raw["post_trigger_sec"]),
        output_dir=cap_raw["output_dir"],
        sigmf_metadata=bool(cap_raw["sigmf_metadata"]),
    )

    # --- server ---
    srv_raw = raw["server"]
    server = ServerConfig(
        host=srv_raw["host"],
        port=int(srv_raw["port"]),
        websocket_path=srv_raw["websocket_path"],
    )

    return SentinelConfig(
        system=system,
        sdr=sdr,
        dsp=dsp,
        antenna=antenna,
        scan=scan,
        detection=detection,
        capture=capture,
        server=server,
    )
