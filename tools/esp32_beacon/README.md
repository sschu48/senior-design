# ESP32 Test Beacon

Controllable 2.4 GHz Wi-Fi beacon for bench-testing the SENTINEL detection pipeline.

## Hardware

Any ESP32 dev board (ESP32-DevKitC, NodeMCU-32S, etc.). Uses the built-in Wi-Fi radio as a known RF source.

## Build & Flash

Requires [PlatformIO](https://platformio.org/).

```bash
cd tools/esp32_beacon
pio run -t upload
pio device monitor -b 115200
```

## Test Profiles

| Profile | Behavior | Tests |
|---|---|---|
| `CONTINUOUS` | Fixed channel, beacon every 100ms | Baseline detection |
| `BURST` | On/off duty cycle (500ms/500ms) | Tripwire duration gating |
| `POWER_RAMP` | Sweep TX power 2–20 dBm in 2 dB steps | SNR sensitivity |
| `FHSS` | Hop through channels 1,6,11,3,9,13 | Multi-frequency detection |

## Serial Commands (115200 baud)

```
PROFILE CONTINUOUS    # Set test profile
CHANNEL 6             # Set WiFi channel (1-14)
POWER 12              # Set TX power (2-20 dBm)
START                 # Begin transmitting
STOP                  # Stop transmitting
STATUS                # Print current settings
```

All parameters can be changed at runtime without reflashing.

## LED Indicator

- **ON** — transmitting
- **OFF** — stopped
- **BLINK** — burst mode, off phase

## SSID Format

The beacon SSID encodes the profile and channel for easy identification in spectrum tools:

```
SENTINEL_CONT_CH6
SENTINEL_BURST_CH6
SENTINEL_RAMP_CH6
SENTINEL_FHSS_CH11
```

## Usage with SENTINEL

```bash
# 1. Flash ESP32, type START in serial monitor
# 2. Run bench test against it
make bench-test-live

# Or with specific parameters
python -m tools.bench_test --live --gain 25 --channel 6 --expect-freq 2.437e9
```
