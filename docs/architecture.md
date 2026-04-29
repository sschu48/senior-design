# SENTINEL — System Architecture v1.0

## Overview

SENTINEL is a single-station passive RF drone detection system operating at 2.4 GHz.
It detects, identifies, and estimates bearing to consumer drones by receiving their
RF emissions — no active transmission, no interference, no detectability.

**Detection range target:** 200 yards (183m) open sky
**Identification method:** RF energy detection + RemoteID / DJI DroneID decoding
**Bearing estimation:** Peak signal strength via directional antenna (AoA)

---

## Hardware Architecture

### Block Diagram

```
           ┌──────────────────────────────────────────────────┐
           │              ANTENNA SYSTEM                       │
           │                                                   │
           │   ┌────────────┐         ┌────────────────────┐  │
           │   │   Omni     │         │     Yagi 12dBi     │  │
           │   │  Dipole    │         │   (directional)    │  │
           │   │  2.4 GHz   │         │     2.4 GHz        │  │
           │   └─────┬──────┘         └────────┬───────────┘  │
           │         │                         │               │
           │         │                    ┌────┴────┐          │
           │         │                    │Pan Servo│          │
           │         │                    │(az only)│          │
           │         │                    └────┬────┘          │
           └─────────┼─────────────────────────┼──────────────┘
                     │                         │
           ┌─────────┼─────────────────────────┼──────────────┐
           │         │       RF FRONT END      │               │
           │   ┌─────┴──────┐         ┌────────┴───────────┐  │
           │   │  BPF + LNA │         │    BPF + LNA       │  │
           │   │  2.4 GHz   │         │    2.4 GHz         │  │
           │   │  ~20dB gain│         │    ~20dB gain       │  │
           │   │  <1.5dB NF │         │    <1.5dB NF       │  │
           │   └─────┬──────┘         └────────┬───────────┘  │
           └─────────┼─────────────────────────┼──────────────┘
                     │                         │
           ┌─────────┼─────────────────────────┼──────────────┐
           │         │      USRP B210          │               │
           │   ┌─────┴──────┐         ┌────────┴───────────┐  │
           │   │    RX-A    │         │      RX-B          │  │
           │   │  (omni)    │         │     (yagi)         │  │
           │   │ 30.72 MSPS │         │   30.72 MSPS       │  │
           │   │ 30 MHz BW  │         │   30 MHz BW        │  │
           │   └─────┬──────┘         └────────┬───────────┘  │
           │         │        USB 3.0          │               │
           └─────────┼─────────────────────────┼──────────────┘
                     │                         │
                     └────────────┬────────────┘
                                  │
           ┌──────────────────────┴───────────────────────────┐
           │               HOST COMPUTER                       │
           │                                                   │
           │  ┌─────────────────────────────────────────────┐ │
           │  │           DSP PIPELINE (Python)              │ │
           │  │                                              │ │
           │  │  Omni Path (RX-A):          Yagi Path (RX-B):│ │
           │  │  IQ → FFT → Energy Det     IQ → DC Remove   │ │
           │  │  → Band Presence Alert     → FFT/PSD Welch  │ │
           │  │  → Cue Yagi Slew           → CFAR Threshold │ │
           │  │                            → Feature Extract │ │
           │  │                            → Pkt Decode      │ │
           │  │                            → Bearing Est     │ │
           │  └──────────────────┬──────────────────────────┘ │
           │                     │                             │
           │  ┌──────────────────┴──────────────────────────┐ │
           │  │         TRACKER / STATE MACHINE              │ │
           │  │  Detection → Track Init → Kalman Filter      │ │
           │  │  Scan ↔ Track mode handoff                   │ │
           │  └──────────────────┬──────────────────────────┘ │
           │                     │                             │
           │  ┌──────────────────┴──────────────────────────┐ │
           │  │          DATA LAYER                          │ │
           │  │  SQLite: detections, tracks, signatures      │ │
           │  │  Raw IQ: triggered capture to disk           │ │
           │  │  JSON logs: every detection event            │ │
           │  └──────────────────┬──────────────────────────┘ │
           │                     │ WebSocket                   │
           └─────────────────────┼────────────────────────────┘
                                 │
           ┌─────────────────────┴────────────────────────────┐
           │            RADAR APP (Frontend)                    │
           │                                                   │
           │  PPI Radar Display     Spectrum Waterfall         │
           │  Bearing Indicator     Detection Log / Alerts     │
           │  System Status         Drone ID Info Panel        │
           └──────────────────────────────────────────────────┘
```

---

## USRP B210 Configuration

### Key Specs
| Parameter | Value |
|---|---|
| RFIC | Analog Devices AD9361 |
| RX Channels | 2 (simultaneous MIMO) |
| Freq Range | 70 MHz – 6 GHz |
| ADC Resolution | 12-bit |
| SISO Bandwidth | 56 MHz (61.44 MSPS) |
| **MIMO Bandwidth** | **30.72 MHz per channel (30.72 MSPS)** |
| Interface | USB 3.0 |
| Noise Figure | < 8 dB (internal, no external LNA) |
| Max RX Input | -15 dBm (protect with attenuator if needed) |
| Power | ~4W at MIMO 30.72 MSPS (external 5.9V supply recommended) |

### Channel Assignment
| Channel | Antenna | Role | Center Freq | Bandwidth |
|---|---|---|---|---|
| RX-A | Omni dipole | Tripwire — detect RF presence | 2.437 GHz (Ch 6) | 30.72 MHz |
| RX-B | Yagi directional | AoA — bearing estimation + decode | 2.437 GHz (Ch 6) | 30.72 MHz |

**Center frequency rationale:** 2.437 GHz (WiFi Ch 6) is the FAA RemoteID default
broadcast channel. Centering here with 30 MHz bandwidth covers 2.422–2.452 GHz,
capturing RemoteID beacons and DJI DroneID bursts. This also overlaps with common
FHSS hop channels in the band center.

**Bandwidth trade-off:** 30.72 MHz in MIMO does NOT cover the full 83.5 MHz ISM band.
We accept this trade-off because:
1. RemoteID and DJI DroneID concentrate in the band center
2. FHSS drones will hop into our 30 MHz window multiple times per second
3. The omni tripwire only needs energy detection, not full band coverage
4. Future: can sweep center freq to scan different portions of the band

### Software Interface
- **Primary:** UHD (USRP Hardware Driver) via Python `uhd` bindings
- **Fallback:** SoapySDR via `SoapyUHD` bridge
- **IQ Format:** 32-bit float complex (numpy complex64)

---

## RF Front End

### External LNA (Required)

The B210's internal noise figure (~8 dB) is inadequate for weak signal detection
in the crowded 2.4 GHz band. An external filtered LNA is required on both RX paths.

**Requirements:**
| Parameter | Spec |
|---|---|
| Frequency | 2.4 – 2.5 GHz |
| Gain | 18-22 dB |
| Noise Figure | < 1.5 dB |
| OIP3 | > +20 dBm (to handle strong nearby WiFi) |
| Filtering | Integrated SAW BPF preferred |
| Power | 3.3-5V DC (bias tee or external) |

**Cascaded noise figure with LNA:**
```
NF_system = NF_lna + (NF_sdr - 1) / G_lna
         = 1.5 dB + (8 dB - 1) / 100   (20dB gain = 100x)
         = 1.5 dB + 0.07 dB
         ≈ 1.57 dB  ← massive improvement over 8 dB alone
```

**Candidates:**
- Qorvo QPL9547 eval board (2.4 GHz LNA + filter)
- NooElec Lana (wideband, 20dB gain, <1dB NF — needs external BPF)
- Custom: Mini-Circuits PMA3-83LNW+ with SAW filter

### Bandpass Filter (if LNA has no integrated filter)

Required to reject out-of-band signals before the LNA to prevent intermod and saturation.

| Parameter | Spec |
|---|---|
| Passband | 2.400 – 2.500 GHz |
| Insertion Loss | < 2 dB |
| Rejection | > 40 dB at 2.3 / 2.6 GHz |

---

## Link Budget — 200 Yard Detection

```
Target: Consumer drone at 200 yards (183m), 2.4 GHz

TRANSMIT SIDE
  Drone EIRP (WiFi/RemoteID):       +20 dBm
  Drone EIRP (DJI OcuSync):         +23 dBm

PROPAGATION
  Free Space Path Loss (183m, 2.4GHz):
    FSPL = 20·log10(183) + 20·log10(2.4e9) + 20·log10(4π/c)
         = 45.3 + 187.6 - 147.6
         = 85.3 dB                           → -85.3 dB

    Note: this assumes free space. Real-world includes:
    - Ground reflection: ±6 dB fading
    - Atmospheric: negligible at 183m
    - Obstruction: not modeled (open sky assumption)

RECEIVE SIDE
  Yagi antenna gain:                 +12 dBi
  External LNA gain:                 +20 dB
  Cable loss (1m coax):              -1 dB
  LNA noise figure:                  1.5 dB

RECEIVED POWER
  WiFi:    +20 - 85.3 + 12 + 20 - 1 = -34.3 dBm
  OcuSync: +23 - 85.3 + 12 + 20 - 1 = -31.3 dBm

SDR SENSITIVITY
  B210 noise floor @ 30 MHz BW:
    kTB = -174 dBm/Hz + 10·log10(30.72e6) = -174 + 74.9 = -99.1 dBm
    + NF_system (1.57 dB) = -97.5 dBm
    + 10 dB SNR requirement = -87.5 dBm detection threshold

MARGIN
  WiFi:    -34.3 - (-87.5) = +53.2 dB margin
  OcuSync: -31.3 - (-87.5) = +56.2 dB margin

CONCLUSION: 200 yards is easily achievable. System is sensitivity-limited
at approximately 2-3 km in open sky (before clutter dominates).
```

---

## Antenna System (v1 — Azimuth Only)

### Yagi Antenna
| Parameter | Spec |
|---|---|
| Type | 12-element Yagi-Uda |
| Frequency | 2.4 GHz |
| Gain | ~12 dBi |
| 3dB Beamwidth | ~35° (H-plane) |
| Polarization | Linear (horizontal mount) |
| Connector | N-type female → SMA adapter → B210 |

### Mount
| Parameter | Spec |
|---|---|
| Axis | Azimuth only (v1) |
| Range | 0–360° continuous |
| Motor | Servo or stepper (TBD based on speed/torque needs) |
| Control | GPIO / serial from host computer |
| Speed Target | ≥ 30°/sec (full sweep in 12 sec) |
| Position Feedback | Encoder or servo PWM readback |

### Omni Antenna
| Parameter | Spec |
|---|---|
| Type | Vertical dipole or ground plane |
| Frequency | 2.4 GHz |
| Gain | ~2 dBi |
| Pattern | Omnidirectional (azimuth), ~60° elevation |
| Mount | Fixed, above yagi pivot point |

### Scan Modes

**SCAN (default):** Continuous azimuth sweep. Yagi rotates 360° at constant speed.
Dwell time per beamwidth = 35° / 30°/sec ≈ 1.2 sec. Full revolution = 12 sec.
Sufficient to catch DJI DroneID bursts (every 600ms) and RemoteID beacons (every 1s).

**CUE:** Omni tripwire detects energy spike. Yagi slews to that azimuth sector.
Faster than waiting for the sweep to come around. Reduces time-to-detect.

**TRACK:** Signal acquired on yagi. System stops sweep and oscillates ±15° around
peak bearing to maintain lock. Kalman filter smooths bearing estimate.

**IDLE:** System parked. No motor activity. Both RX channels still active for
passive monitoring.

```
State Machine:

  IDLE ──(user start)──→ SCAN
  SCAN ──(omni cue)────→ CUE
  SCAN ──(yagi detect)─→ TRACK
  CUE  ──(yagi detect)─→ TRACK
  CUE  ──(timeout)─────→ SCAN
  TRACK ──(signal lost)─→ SCAN
  any  ──(user stop)───→ IDLE
```

---

## DSP Pipeline

### Stage 1: IQ Acquisition
- Source: USRP B210 via UHD Python API
- Format: numpy complex64 arrays
- Buffer: ring buffer, ~1 sec depth per channel
- Threading: asyncio producer/consumer pattern

### Stage 2: DC Offset Removal
- Method: subtract running mean from IQ stream
- Window: 1024 samples
- Applied to both omni and yagi paths

### Stage 3: Power Spectral Density (PSD)
- Method: Welch's method (scipy.signal.welch)
- FFT size: 2048 points (14.9 kHz resolution @ 30.72 MSPS)
- Window: Hanning
- Overlap: 50%
- Output: power vs frequency array, updated every ~67ms

### Stage 4: Omni Tripwire (RX-A path)
- Compute total band power from PSD
- Compare against adaptive noise floor (rolling median, 10 sec window)
- Threshold: noise floor + 10 dB (configurable)
- On trigger: estimate coarse frequency of peak, command yagi slew to current bearing
  (omni is non-directional — "something is present" alert, not bearing)

**Note:** The omni cannot provide bearing. Its role is purely temporal — detect that
a signal appeared in the band, then tell the yagi "start looking harder."

### Stage 5: CFAR Detection (RX-B path)
- Method: Cell-Averaging CFAR (CA-CFAR) on yagi PSD
- Guard cells: 4
- Reference cells: 16
- Threshold factor: configurable (start at 10 dB above local noise)
- Output: list of detected frequency bins exceeding threshold

### Stage 6: Feature Extraction
For each CFAR detection:
- Center frequency
- Bandwidth (3dB and 10dB)
- Peak power (dBm relative)
- Burst duration (if transient)
- Repetition interval
- Hop pattern (if FHSS — multiple detections across frequency over time)
- Current yagi azimuth (bearing)
- Timestamp (GPS-disciplined if available, else NTP)

### Stage 7: Packet Decoding
Two parallel decode paths:

**RemoteID (ASTM F3411):**
- WiFi beacon frame capture from IQ
- Extract vendor-specific information elements
- Decode using opendroneid-core-c library (C with Python bindings)
- Fields: serial number, GPS position, altitude, speed, operator position

**DJI DroneID (OcuSync):**
- Detect OFDM bursts in IQ stream (Zadoff-Chu sync sequence detection)
- Demodulate QPSK subcarriers
- Decode DroneID payload (GPS, serial, home point, velocity)
- Reference: proto17/dji_droneid, RUB-SysSec/DroneSecurity
- Burst interval: ~600ms, bandwidth: 10 MHz

### Stage 8: Classification
Rule-based classifier (v1):
```
IF remoteID decoded     → DRONE (high confidence, type from packet)
IF dji_droneid decoded  → DRONE (high confidence, DJI, model from serial)
IF FHSS pattern matches → DRONE (medium confidence, type from hop signature)
IF WiFi beacon + drone OUI → DRONE (high confidence, manufacturer from MAC)
IF narrowband burst, periodic → UNKNOWN RF (flag for review)
ELSE → CLUTTER (ignore)
```

### Stage 9: Bearing Estimation
- Method: peak RSSI at known yagi azimuth position
- In TRACK mode: oscillate yagi ±15°, fit parabola to power vs angle
- Bearing resolution: limited by yagi beamwidth (~35°), improved by parabolic fit to ~5-10°
- Output: azimuth bearing in degrees, confidence based on SNR

---

## Data Layer

### SQLite Database Schema (prototype)

```sql
CREATE TABLE detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,          -- ISO 8601
    bearing_deg     REAL,                   -- yagi azimuth at detection
    power_dbm       REAL,                   -- received signal power
    snr_db          REAL,                   -- signal to noise ratio
    center_freq_hz  REAL,                   -- detected signal center frequency
    bandwidth_hz    REAL,                   -- detected signal bandwidth
    classification  TEXT,                   -- DRONE / UNKNOWN_RF / CLUTTER
    confidence      REAL,                   -- 0.0 - 1.0
    protocol        TEXT,                   -- REMOTE_ID / DJI_DRONEID / WIFI / FHSS / UNKNOWN
    drone_id        TEXT,                   -- serial number if decoded
    drone_lat       REAL,                   -- from decoded packet
    drone_lon       REAL,                   -- from decoded packet
    drone_alt_m     REAL,                   -- from decoded packet
    operator_lat    REAL,                   -- from decoded packet (RID/DJI)
    operator_lon    REAL,                   -- from decoded packet (RID/DJI)
    raw_features    TEXT                    -- JSON blob of all extracted features
);

CREATE TABLE tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created         TEXT NOT NULL,
    updated         TEXT NOT NULL,
    state           TEXT NOT NULL,          -- TENTATIVE / CONFIRMED / LOST
    bearing_deg     REAL,                   -- current filtered bearing
    bearing_rate    REAL,                   -- deg/sec
    classification  TEXT,
    drone_id        TEXT,
    detection_count INTEGER DEFAULT 0
);

CREATE TABLE track_detections (
    track_id        INTEGER REFERENCES tracks(id),
    detection_id    INTEGER REFERENCES detections(id),
    PRIMARY KEY (track_id, detection_id)
);
```

### Raw IQ Capture
- Triggered on detection events (configurable)
- Format: `.cf32` (32-bit float complex interleaved I/Q)
- Filename: `YYYY-MM-DD_HHMMSS_<event_id>.cf32`
- Storage: `data/samples/`
- Also save `.sigmf-meta` sidecar (SigMF metadata standard)
- Retention: configurable, default keep all

### JSON Event Log
Every detection event emits a structured JSON log line:
```json
{
    "timestamp": "2026-03-01T14:30:00.123Z",
    "event": "detection",
    "bearing_deg": 127.5,
    "power_dbm": -42.3,
    "snr_db": 28.1,
    "classification": "DRONE",
    "protocol": "DJI_DRONEID",
    "drone_id": "1581F5FKD...",
    "confidence": 0.95
}
```

---

## Frontend — Radar App

### Existing Foundation
Express.js + Socket.IO server (`radar-app/server.js`) with real-time WebSocket push.
Currently serves simulated data. Will be connected to live DSP pipeline output.

### Display Components (v1)
1. **PPI Radar Sweep** — rotating sweep line showing current yagi azimuth, blips at detection bearings
2. **Detection Table** — live-updating list of detections with timestamp, bearing, classification, drone ID
3. **Spectrum View** — real-time PSD plot from yagi channel (power vs frequency)
4. **System Status** — antenna position, SDR status, scan mode, detection count
5. **Drone Detail Panel** — when RemoteID/DroneID decoded: serial, GPS position on map, operator position

### Data Flow
```
DSP Pipeline → WebSocket → Frontend
              (JSON events pushed at detection rate)

Frontend → REST API → DSP Pipeline
          (config changes, mode commands)
```

---

## Software Stack

| Layer | Technology |
|---|---|
| SDR Interface | UHD Python API (primary), SoapySDR (fallback) |
| DSP | numpy, scipy.signal |
| Packet Decode | opendroneid-core-c (Python bindings), custom DJI decoder |
| Async Pipeline | asyncio |
| Database | SQLite3 (stdlib) |
| Backend API | Express.js + Socket.IO (WebSocket + REST) |
| Frontend | Express + Socket.IO app, evolve to React later |
| Antenna Control | GPIO/serial via Python (RPi.GPIO or pyserial) |
| Config | YAML (config.yaml) |
| Logging | Python logging → JSON structured output |
| Testing | pytest + synthetic IQ fixtures |

---

## Phase Status

### Phase 1 Foundation

Current state:

1. **SDR capture pipeline** - single-RX synthetic/USRP path exists; dual-RX
   synthetic and USRP scaffolds are built.
2. **Basic FFT detection** - PSD computation and energy tripwire exist.
3. **CFAR detection** - CA-CFAR runs on the Yagi path.
4. **Antenna control scaffold** - simulated SCAN/CUE/TRACK behavior exists.
5. **Config system** - `config.yaml` drives hardware and DSP parameters.
6. **RF event contracts** - IQ, PSD, event, track, and verdict objects exist.

Not complete:

1. **RemoteID decoder integration** - not started.
2. **DJI DroneID decoder integration** - not started.
3. **Radar app integration** - not started.
4. **Real antenna hardware control** - not started.

### Phase 1.5 RF Validation

Active next phase before Phase 2 classifier work. The objective is to validate
the physical receive chain before making drone identity claims.

Required evidence gates:

1. Bench-validate B210 dual-RX channel agreement.
2. Measure noise floor and safe gain/attenuation settings.
3. Calibrate Yagi RSSI/SNR vs azimuth.
4. Add replayable IQ + metadata artifacts.
5. Add RF survey mode for local 2.4 GHz clutter mapping.

Detailed checkpoint:
[phase-1-5-rf-validation.md](phase-1-5-rf-validation.md)

---

## Open Questions

- [ ] Servo vs stepper for azimuth? (servo = simpler, stepper = more precise)
- [ ] Host computer: laptop, Raspberry Pi 5, or dedicated mini-PC? (USB 3.0 throughput matters)
- [ ] External GPS for precision timestamping?
- [ ] Weatherproofing requirements for outdoor deployment?
- [ ] Multi-station networking (future)?
- [ ] Budget constraints for LNA selection?
