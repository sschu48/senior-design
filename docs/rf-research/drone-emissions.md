# Drone RF Emission Profiles — Research Notes

## 1. Emission Types at 2.4 GHz

Consumer drones emit on 2.4 GHz for multiple purposes. Each emission type has
different characteristics relevant to detection.

### Control Uplink (Operator → Drone)
| Parameter | Typical Value |
|---|---|
| Direction | Ground → Air |
| Power (EIRP) | +20 to +26 dBm |
| Modulation | FHSS or DSSS |
| Bandwidth | 1–10 MHz (protocol dependent) |
| Duty Cycle | Continuous during flight |
| Notes | Harder to detect from drone's perspective — signal comes from controller |

### Video Downlink (Drone → Operator)
| Parameter | Typical Value |
|---|---|
| Direction | Air → Ground |
| Power (EIRP) | +20 to +27 dBm |
| Modulation | OFDM (DJI OcuSync), 802.11 (WiFi drones) |
| Bandwidth | 10–40 MHz |
| Duty Cycle | Continuous during video streaming |
| Notes | **Strongest emission from the drone itself.** Best detection target. |

### Telemetry / RemoteID Beacon
| Parameter | Typical Value |
|---|---|
| Direction | Air → All (broadcast) |
| Power (EIRP) | +10 to +20 dBm |
| Modulation | 802.11 Beacon (WiFi) or BLE 5 |
| Bandwidth | 20 MHz (WiFi) or 2 MHz (BLE) |
| Duty Cycle | Periodic: 1 beacon/sec (RID), every 600ms (DJI DroneID) |
| Notes | **Easiest to identify** — contains drone serial, GPS, operator position |

---

## 2. Protocol-Specific Emission Profiles

### DJI OcuSync 2/3 (Mavic 3, Mini 3 Pro, Air 2S, etc.)

DJI's proprietary link protocol. Dominates the consumer drone market (~70%+ share).

| Parameter | Value | Source |
|---|---|---|
| Frequency Bands | 2.4 GHz (2.400–2.483 GHz), 5.8 GHz (5.725–5.850 GHz) | DJI specs |
| Bandwidth | ~10 MHz video channel | Measured (proto17) |
| Modulation | OFDM | Confirmed (NDSS 2023) |
| FHSS | Yes, hops within band | Observed |
| Max EIRP | +23 dBm (2.4 GHz), +26 dBm (5.8 GHz) | FCC filings |
| Video Latency | 120–200ms | DJI specs |
| Range | Up to 15 km (OcuSync 3) | DJI specs (line of sight) |

**DJI DroneID Emission (critical for SENTINEL):**
| Parameter | Value |
|---|---|
| Type | Dedicated OFDM broadcast (NOT WiFi) |
| Frequency | 2.3995–2.4595 GHz (2.4 band) or 5.7565–5.7965 GHz (5.8 band) |
| Bandwidth | 10 MHz (15.36 MHz with guard carriers) |
| Subcarriers | 600 data carriers per OFDM symbol |
| Modulation per subcarrier | QPSK |
| Burst structure | 9 OFDM symbols per burst |
| Sync sequence | Zadoff-Chu (symbols 4 and 6, root indices 600 and 147) |
| Burst interval | ~600 ms |
| Encryption | **None** — transmitted in the clear |
| Required sample rate | 15.36 MSPS minimum, 30.72 MSPS recommended |

**DroneID Decoded Fields:**
- Serial number
- Drone GPS: latitude, longitude, altitude
- Home point (takeoff location)
- Operator/controller GPS position
- Velocity (North, East, Up)
- Yaw/heading
- Product type (drone model identifier)
- UUID
- Flight state

**Open-Source Decoders:**
- `proto17/dji_droneid` — MATLAB/Octave, reference implementation
- `RUB-SysSec/DroneSecurity` — Full SDR receiver, NDSS 2023 paper
- `anarkiwi/samples2djidroneid` — Python, decodes from .cf32 IQ captures
- `alphafox02/antsdr_dji_droneid` — Embedded ARM decoder for AntSDR

**Source:** Schiller et al., "Drone Security and the Mysterious Case of DJI's DroneID,"
NDSS 2023. https://www.ndss-symposium.org/ndss-paper/drone-security-and-the-mysterious-case-of-djis-droneid/

---

### FAA Remote ID (ASTM F3411-22a)

Mandatory for all drones since September 2023. Broadcasts identity and position.

**Broadcast Methods:**
| Method | Frequency | Range | Data Limit |
|---|---|---|---|
| Bluetooth 4.x Legacy Advertising | 2.402/2.426/2.480 GHz | ~100m | 31 bytes/ad |
| Bluetooth 5.x Long Range (Coded PHY S=8) | 2.402/2.426/2.480 GHz | ~1 km | Extended ads |
| **WiFi Beacon** | **2.437 GHz (Ch 6)** or 5.745 GHz (Ch 149) | ~1 km | Full frame |
| WiFi NAN | 2.437 GHz (Ch 6) or 5.745 GHz (Ch 149) | ~1 km | Service discovery |

**WiFi Beacon is our primary target.** It's the easiest to capture with SDR and
has the longest range. The data is in vendor-specific information elements (IEs)
within standard 802.11 beacon frames.

**Broadcast Intervals:**
| Message Type | Interval |
|---|---|
| Location | Every 1 second |
| Basic ID | Every 3 seconds |
| System (operator position) | Every 3 seconds |

**Message Types and Key Fields:**

| Message | Fields |
|---|---|
| Basic ID | Serial number (ANSI/CTA-2063-A), UA type (multirotor, helicopter, etc.) |
| Location | Lat, lon, altitude (baro + WGS84), heading, speed (H + V), timestamp, accuracy |
| System | Operator lat/lon, operator altitude, area count/radius, timestamp |
| Self-ID | Free-text operation description |
| Operator ID | Operator registration number |
| Authentication | Cryptographic signature (optional) |

**Decoding:**
- Library: `opendroneid-core-c` (C, with Python wrappable via ctypes/cffi)
- GitHub: https://github.com/opendroneid/opendroneid-core-c
- Android receiver app: https://github.com/opendroneid
- WiFi beacon parsing: standard 802.11 frame parsing (Scapy in Python)

**Detection approach for SENTINEL:**
1. Capture raw IQ at 2.437 GHz from B210
2. Demodulate 802.11 frames (or use WiFi monitor mode on a cheap dongle as supplemental)
3. Parse beacon frame IEs for RemoteID vendor-specific data
4. Decode with opendroneid-core-c

---

### WiFi-Controlled Drones (802.11)

Many hobby/toy drones and some DJI models (Spark, Tello, Phantom 3/4) create WiFi
access points for control and video.

| Parameter | Value |
|---|---|
| Protocol | 802.11b/g/n |
| Frequency | 2.412–2.462 GHz (Channels 1–11) |
| Bandwidth | 20 MHz (802.11g/n), 40 MHz (802.11n) |
| EIRP | +15 to +20 dBm |
| Beacon Interval | Every 100ms (standard AP behavior) |

**Detection approach:**
- Capture WiFi beacon frames
- Extract SSID and BSSID (MAC address)
- Match MAC OUI against known drone manufacturer prefixes

**Known Drone OUI Prefixes (MAC address first 3 bytes):**

| OUI | Manufacturer |
|---|---|
| `60:60:1F` | DJI (camera WiFi interface) |
| `04:A8:5A` | DJI |
| `0C:9A:E6` | DJI |
| `34:D2:62` | DJI |
| `48:1C:B9` | DJI |
| `4C:43:F6` | DJI |
| `58:B8:58` | DJI |
| `88:29:85` | DJI |
| `8C:58:23` | DJI |
| `E4:7A:2C` | DJI |
| `A0:14:3D` | Parrot |
| `90:03:B7` | Parrot |
| `00:12:1C` | Parrot (legacy) |
| `00:26:7E` | Parrot |
| `A4:CF:12` | Skydio |
| `E8:3E:B6` | Autel Robotics |

*Note: This list is not exhaustive. OUI databases update frequently.
Cross-reference with https://maclookup.app or IEEE OUI registry.*

---

### ExpressLRS (ELRS) — RC Control Link

Open-source long-range RC protocol, popular in FPV racing and custom builds.

| Parameter | Value |
|---|---|
| Frequency | 2.4 GHz (and 900 MHz variant) |
| Modulation | LoRa (CSS — Chirp Spread Spectrum) |
| Bandwidth | ~500 kHz per channel |
| Hop Rate | 100–500 Hz (very fast FHSS) |
| EIRP | +10 to +27 dBm (depending on module/region) |
| Packet Rate | 50–1000 Hz |

**Detection signature:**
- Very narrow bandwidth (~500 kHz) compared to WiFi (20 MHz)
- Extremely fast hop rate — appears as a "moving dot" across spectrum
- Distinctive chirp modulation visible in spectrogram

---

### FrSky ACCST/ACCESS — RC Control Link

Popular RC control protocol.

| Parameter | Value |
|---|---|
| Frequency | 2.408–2.475 GHz |
| Modulation | FHSS |
| Channels | 47 hop channels |
| Bandwidth | ~1 MHz per channel |
| Hop Rate | ~9 ms dwell per channel |
| EIRP | +20 dBm |

---

### Spektrum DSM2/DSMX — RC Control Link

| Parameter | Value |
|---|---|
| Frequency | 2.408–2.475 GHz |
| Modulation | DSSS (DSM2) / FHSS (DSMX) |
| Channels | 2 (DSM2) / 23 (DSMX) |
| Bandwidth | ~5 MHz per channel |
| Hop Rate | ~11 ms dwell (DSMX) |
| EIRP | +18 dBm |

---

## 3. Link Budget Analysis

### Scenario: DJI Mavic 3 at 200 yards (183m), 2.4 GHz

```
TRANSMITTER
  DJI OcuSync 3 EIRP:          +23 dBm

PATH LOSS
  Free Space Path Loss:
    FSPL = 20·log10(d) + 20·log10(f) + 20·log10(4π/c)
         = 20·log10(183) + 20·log10(2.44e9) - 147.55
         = 45.25 + 187.75 - 147.55
         = 85.45 dB

  Additional margins:
    Polarization mismatch:       -3 dB (worst case, linear vs linear)
    Ground reflection fading:    -6 dB (conservative, 2-ray model)
    Total path loss:             -94.45 dB

RECEIVER (two configs)
                          PCB Yagi (test)    L-com (final)
  Yagi gain:              +12 dBi            +15 dBi
  External LNA gain:      +20 dB             +20 dB
  Cable loss:             -1 dB              -1 dB

RECEIVED POWER
  PCB Yagi:  Pr = +23 - 94.45 + 12 + 20 - 1 = -40.45 dBm
  L-com:     Pr = +23 - 94.45 + 15 + 20 - 1 = -37.45 dBm

NOISE FLOOR
  kTB = -174 + 10·log10(30.72e6) = -174 + 74.87 = -99.13 dBm
  System NF (with ext LNA):      +1.57 dB
  Noise floor:                   -97.56 dBm

SNR AT RECEIVER
  PCB Yagi:  SNR = -40.45 - (-97.56) = 57.1 dB
  L-com:     SNR = -37.45 - (-97.56) = 60.1 dB

DETECTION THRESHOLD
  Required SNR for detection:    10 dB (CFAR with Pd=0.9, Pfa=1e-6)

MARGIN
  PCB Yagi:  57.1 - 10 = 47.1 dB margin
  L-com:     60.1 - 10 = 50.1 dB margin

MAXIMUM DETECTION RANGE (theoretical, free space)
  PCB Yagi:  path budget = 23 + 12 + 20 - 1 - (-97.56) - 10 = 141.56 dB → ~117 km
  L-com:     path budget = 23 + 15 + 20 - 1 - (-97.56) - 10 = 144.56 dB → ~165 km

  Practical limit (with fading, clutter, multipath): ~2-5 km
```

### Scenario: Toy WiFi Drone at 200 yards (183m)

```
  EIRP:           +15 dBm (lower power)
  Received:       -48.45 dBm
  SNR:            49.1 dB
  Margin:         39.1 dB — still very detectable
```

### Scenario: BLE RemoteID at 200 yards (183m)

```
  EIRP:           +10 dBm (BLE typical)
  BW:             2 MHz (BLE channel)
  Received:       -53.45 dBm (with yagi)
  Noise floor:    -174 + 63 + 1.57 = -109.43 dBm (2 MHz BW)
  SNR:            56 dB — detectable as energy, but BLE decode is hard with SDR
```

---

## 4. 2.4 GHz Band Environment — Clutter Sources

The 2.4 GHz ISM band is shared with many non-drone emitters. Understanding the
clutter environment is critical for reducing false alarms.

| Source | Frequency | Bandwidth | Behavior | Distinguishing Feature |
|---|---|---|---|---|
| WiFi AP (router) | 2.412–2.462 GHz | 20/40 MHz | Continuous, stationary | Fixed bearing, always present |
| Bluetooth | 2.402–2.480 GHz | 1 MHz hops | Fast FHSS, short range | Very fast hop, low power |
| Microwave oven | 2.45 GHz center | ~20 MHz | Broadband noise, intermittent | No modulation structure, periodic on/off |
| Baby monitor | 2.4 GHz | Varies | Continuous when active | Fixed bearing, low power |
| Zigbee/Thread | 2.405–2.480 GHz | 2 MHz | Short bursts | Very low power, short range |
| Garage door opener | 2.4 GHz | Narrow | Very short burst | Rare, easily filtered by duration |

### Clutter Mitigation Strategy

1. **Spatial filtering:** Yagi rejects ~20 dB from off-axis directions. Stationary
   clutter sources can be mapped and excluded by bearing.
2. **Temporal filtering:** Drone signals appear and disappear. Stationary WiFi APs
   are persistent. Track signal persistence to discriminate.
3. **Spectral fingerprinting:** Each protocol has a distinct spectral signature
   (bandwidth, modulation, hop pattern). Rule-based classifier separates them.
4. **CFAR adaptive threshold:** Local noise floor adapts to environment. Only
   signals exceeding the *local* noise floor trigger detection.
5. **Bearing exclusion zones:** Known WiFi AP bearings can be masked in software
   to eliminate persistent false alarms at deployment site.

---

## 5. Key References

### Papers
- Schiller et al., "Drone Security and the Mysterious Case of DJI's DroneID,"
  NDSS 2023. (DJI DroneID protocol reverse engineering)
- Nguyen et al., "Matthan: Drone Presence Detection by Identifying Physical
  Signatures in the Drone's RF Communication," MobiSys 2017. (RF fingerprinting)
- Ezuma et al., "Detection and Classification of UAVs Using RF Fingerprints in
  the Presence of Wi-Fi and Bluetooth Interference," IEEE Access 2020.
- ASTM F3411-22a, "Standard Specification for Remote ID and Tracking"

### Open-Source Projects
- https://github.com/opendroneid/opendroneid-core-c (RemoteID decoder)
- https://github.com/proto17/dji_droneid (DJI DroneID MATLAB decoder)
- https://github.com/RUB-SysSec/DroneSecurity (DJI DroneID SDR receiver)
- https://github.com/anarkiwi/samples2djidroneid (Python DJI DroneID decoder)
- https://github.com/cemaxecuter/WarDragon (integrated drone detection platform)

### Hardware Datasheets
- Ettus USRP B210: https://www.ettus.com/all-products/ub210-kit/
  - Local: `docs/hardware/datasheets/USRP_B210_datasheet.md`
- AD9361 RFIC: https://www.analog.com/en/products/ad9361.html
- L-com HG2415Y-RSP (final Yagi): `docs/hardware/datasheets/L-com_HG2415Y-RSP_datasheet.md`
- PCB Yagi B0BWM9X2HJ (test Yagi): `docs/hardware/datasheets/PCB_Yagi_B0BWM9X2HJ_datasheet.md`

### Community Resources
- RTL-SDR Blog drone detection articles: https://www.rtl-sdr.com/tag/drones/
- GNU Radio wiki: https://wiki.gnuradio.org/
- SigMF metadata standard: https://github.com/gnuradio/SigMF
