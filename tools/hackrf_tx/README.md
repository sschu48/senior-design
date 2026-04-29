# HackRF TX — Dummy Drone Signal Generator

Replay or synthesize drone-shaped RF signals through a HackRF One to test the
SENTINEL receive pipeline without flying a real drone.

> **Why this exists.** ESP32 WiFi beacons are easy to deploy but emit at 0.2%
> duty cycle on a fixed 802.11 PHY — they don't look like drone signals.
> HackRF can emit DJI DroneID-shaped OFDM bursts, OcuSync-style continuous
> wideband, or replay actual captured drone IQ. That makes it a real
> functional test of CFAR + Welch + (eventually) the OFDM/DroneID decoders.

```
   ┌────────── Raspberry Pi 4 ──────────┐
   │                                    │
   │  python -m tools.hackrf_tx ────────┼─── USB 3.0 ──→ [HackRF One] ──→ SMA dipole
   │  (loops .cs8 file via              │                    │
   │   hackrf_transfer -R)              │                ((( drone-shaped RF )))
   │                                    │
   └────────────────────────────────────┘                       ↓
                                                       [Yagi → BPF → B210]
                                                              ↓
                                                          SENTINEL
```

---

## Quick start

```bash
# 1. Verify HackRF is connected
hackrf_info

# 2. List built-in profiles
python -m tools.hackrf_tx --list-profiles

# 3. Smoke-test with a CW tone (no transmission, just generates the .cs8)
python -m tools.hackrf_tx --profile tone_2437 --dry-run

# 4. Actually transmit the default profile (DJI DroneID mimic)
python -m tools.hackrf_tx
```

---

## Hardware setup (no attenuator)

```
[Raspberry Pi 4, 4GB] ── USB 3.0 ── [HackRF One] ── SMA ── [2.4 GHz dipole]
        │
        └── USB-C ── [20 Ah battery]
```

| Component | Notes |
|---|---|
| Raspberry Pi 4 (4GB) | needs USB 3.0 for HackRF throughput |
| HackRF One | $320 from Great Scott Gadgets / Adafruit |
| 2.4 GHz dipole | any RP-SMA dipole; ~+2 dBi |
| USB-C battery (20 Ah) | for portable field use |

This setup deliberately has **no inline attenuator**. The runner's defaults
keep transmit power low enough to be safe for the B210 receiver at >=3 m
separation; see [Safety](#safety-no-attenuator-link-budget) for the math.

---

## Pi setup (Raspberry Pi OS / Ubuntu 22.04+)

```bash
# HackRF tools
sudo apt update
sudo apt install -y hackrf libhackrf-dev

# udev rule so non-root can talk to the device
sudo cp /usr/share/doc/libhackrf*/53-hackrf.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# Verify
hackrf_info
# → Found HackRF / Serial / Firmware Version: ...

# SENTINEL repo
git clone <this repo>
cd senior-design
make venv         # creates .venv with all deps
```

On macOS for development, replace the apt commands with:
```bash
brew install hackrf
```

---

## Usage

### List profiles

```bash
python -m tools.hackrf_tx --list-profiles
```

Built-in profiles (defined in `config.yaml` under `hackrf_tx.profiles`):

| Profile | Description | Use for |
|---|---|---|
| `dji_droneid`     | 10 MHz OFDM burst, 600 ms cadence | RemoteID / DroneID decoder validation, low-duty-cycle CFAR |
| `ocusync_video`   | Continuous 10 MHz OFDM           | Wideband OFDM detection (DJI video link mimic) |
| `tone_2437`       | Single CW tone at 2.437 GHz      | Pipeline smoke test — easiest signal to detect |
| `replay_capture`  | Replay a captured `.cf32` IQ file| Decoder validation against real drone bytes |

### Run the default (DJI DroneID mimic)

```bash
python -m tools.hackrf_tx
```

Output (one-time per profile, `.cs8` is cached for next run):
```
profile dji_droneid: synthesized synth_ofdm_burst → data/hackrf_tx_cache/dji_droneid.cs8 (6000000 samples, 0.600 s @ 10.00 MS/s)
============================================================
SENTINEL — Dummy Drone TX (HackRF One)
============================================================
  Profile:        dji_droneid
  Center freq:    2.4370 GHz
  ...
  TX VGA gain:    20 dB (range: 0–47)
  RF amp:         OFF
  Est. TX power:  -20.0 dBm
  RX separation:  3.0 m (assumed)
  At B210 input:  -59.7 dBm (margin to -15 dBm limit: +44.7 dB)
============================================================
  Transmitting — Ctrl-C to stop.
```

### Replay a captured IQ recording

```bash
# 1. Capture a real drone with the B210
make run-live --frames 600   # or however you record cf32
# IQ lands in data/samples/bench_YYYYMMDD_HHMMSS.cf32

# 2. Replay it through HackRF
python -m tools.hackrf_tx --profile replay_capture --iq data/samples/bench_20260429_140101.cf32
```

The runner converts cf32 → cs8 in the cache the first time it sees a file.

### Override gain (e.g. for range testing)

```bash
python -m tools.hackrf_tx --gain 35
```

Gain 0–47. The startup banner recalculates the link budget at the new gain
and warns if the projected B210 input crosses the safe limit.

### Force regenerate cached IQ

If you change profile parameters in `config.yaml` (bandwidth, num_subcarriers,
duration), the cache won't auto-invalidate — pass `--regenerate`:
```bash
python -m tools.hackrf_tx --regenerate
```

### Convert IQ formats standalone

```bash
python -m tools.hackrf_tx.convert_iq capture.cf32 capture.cs8
python -m tools.hackrf_tx.convert_iq --reverse capture.cs8 capture.cf32
```

---

## Safety (no-attenuator link budget)

The B210 has an absolute max RX input of **−15 dBm**. With a +12 dBi Yagi
and 2 dB BPF loss in front of it, the RF environment can quickly exceed that
in close-range tests. The runner's startup banner computes:

```
RX_at_B210 = TX_HackRF − FSPL(distance) + Yagi_gain − BPF_loss
```

Reference numbers at 2.437 GHz (Yagi = +12 dBi, BPF = 2 dB):

| TX gain | TX power | At 1 m | At 3 m | At 5 m | At 10 m |
|---|---|---|---|---|---|
| `--gain 0`   | −55 dBm | −85 | −94 | −99 | −105 |
| `--gain 20`  | −20 dBm | −50 | −60 | −64 |  −70 |
| `--gain 40`  |  −3 dBm | −33 | −43 | −47 |  −53 |
| `--gain 47`  |   0 dBm | −30 | −40 | −44 |  −50 |
| `--gain 47 + amp` | +14 dBm | **−16** | −26 | −30 | −36 |

**Rule of thumb:** without an attenuator, keep at least **3 m** between
HackRF and B210 antennas, and don't use the front-end amp.

The defaults in `config.yaml` reflect this:
- `tx_vga_gain_db: 20`         — about −20 dBm output
- `enable_amp: false`          — front-end amp off
- `min_rx_separation_m: 3.0`   — banner warning threshold

The amp is double-gated: both `config.enable_amp = true` *and* the CLI flag
`--allow-amp` are required to turn it on. If only one is set, the amp stays
off and you get a warning.

---

## How the cadence is implemented

Naively spawning `hackrf_transfer` per burst at 600 ms intervals adds
50–200 ms of subprocess overhead per call — terrible cadence accuracy.

Instead, this tool:

1. **Synthesizes the burst once** (e.g. 1 ms of OFDM with a Hann envelope
   for clean edges).
2. **Pads the burst to the full period** (599 ms of zeros).
3. **Writes the whole period as a `.cs8` file.**
4. **Runs `hackrf_transfer -t file.cs8 -R`** which loops the file forever.

Result: sample-accurate burst cadence with one subprocess invocation.

For continuous profiles (`ocusync_video`, `tone_2437`), the file is just
the burst with no padding and `period_s: 0`.

---

## Profile tuning

Edit `config.yaml` under `hackrf_tx.profiles.<name>`:

```yaml
dji_droneid:
  description: "DJI DroneID-style 10 MHz OFDM burst, 600ms cadence"
  iq_source: "synth_ofdm_burst"   # one of: synth_tone, synth_ofdm_burst,
                                  # synth_ofdm_continuous, file
  iq_file: ""                     # path if iq_source = file
  center_freq_hz: 2.437e9         # carrier
  sample_rate_hz: 10.0e6          # HackRF: 2-20 MS/s
  bandwidth_hz: 9.0e6             # synthesized signal width
  burst_duration_s: 0.001         # active burst length
  period_s: 0.600                 # full repeat period (0 = continuous)
  num_subcarriers: 600            # OFDM subcarrier count
```

After editing, run with `--regenerate` to refresh the cached `.cs8`:
```bash
python -m tools.hackrf_tx --regenerate
```

---

## Code map (for agents)

```
tools/hackrf_tx/
├── __init__.py            empty package marker
├── __main__.py            entry point: `python -m tools.hackrf_tx`
├── config.py              HackRFTxConfig + load_hackrf_tx_config(yaml_path)
├── synth.py               pure IQ synthesis (no I/O):
│                            synth_tone, synth_ofdm, synth_periodic_burst,
│                            cf32_to_cs8, cs8_to_cf32
├── profiles.py            resolve_profile_iq(profile, cache_dir, ...) →
│                            generates/locates .cs8 path on disk
├── convert_iq.py          standalone CLI for cf32 ↔ cs8 conversion
├── dummy_drone.py         main runner: loads config, resolves IQ,
│                            link-budget banner, spawns hackrf_transfer -R
└── README.md              this file
```

### Module responsibilities

- **synth.py** — Pure functions. No file I/O. No logging. Tests can call
  these directly with synthetic inputs.
- **config.py** — YAML → frozen dataclasses. All validation here.
  Parallel to `src/sdr/config.py` but intentionally separate so RX-pipeline
  schema isn't coupled to TX tooling.
- **profiles.py** — File I/O for caching. Decides whether to synthesize,
  convert from cf32, or pass through an existing cs8.
- **dummy_drone.py** — Subprocess management, link-budget calc, CLI.
  Public functions (`fspl_db`, `estimate_b210_input_dbm`, etc.) are tested
  in `tests/unit/test_hackrf_tx_dummy_drone.py`.

### Where to make common changes

| Goal | File |
|---|---|
| Add a new built-in profile | `config.yaml` (just YAML) |
| Add a new synthesis kind   | `synth.py` + dispatch in `profiles.py` |
| Tune the safety banner     | `dummy_drone.py::_print_banner` |
| Refine TX power model      | `dummy_drone.py::_HACKRF_OUTPUT_DBM_BY_VGA` |
| Change `hackrf_transfer` flags | `dummy_drone.py::_build_hackrf_cmd` |

### Tests

```bash
pytest tests/unit/test_hackrf_tx_*.py -v
```

Coverage:
- `test_hackrf_tx_synth.py` — tone freq, OFDM bandwidth, envelope shape, cf32↔cs8 round-trip
- `test_hackrf_tx_config.py` — YAML parse, validation, repo defaults safe
- `test_hackrf_tx_profiles.py` — synth/file resolution, cache reuse, format conversion
- `test_hackrf_tx_dummy_drone.py` — link-budget math, CLI parsing

All tests use synthetic inputs. No hardware needed.

---

## Limitations

The HackRF + this tool **cannot** replicate:

- **True DJI DroneID frame structure** (Zadoff-Chu sync, exact OFDM timing,
  encrypted payload). The synthesized OFDM is *spectrally* DroneID-shaped
  but won't pass a real DroneID decoder. For decoder validation, use
  `replay_capture` mode with an actual recorded burst, or generate via
  the open-source `RUB-SysSec/DroneSecurity` GNURadio flowgraphs.
- **Sub-millisecond FHSS hopping** (real ELRS hops at 50–500 Hz).
  `hackrf_transfer` is locked to a single carrier per invocation — for
  true FHSS you need GNURadio with frequency-tag streams.
- **TX power above ~+15 dBm.** HackRF caps at +10 to +15 dBm at 2.4 GHz
  with the front-end amp on. Real drone RC TXs run +20 to +30 dBm. To
  match real-world range, add an external 2.4 GHz amplifier (e.g.
  Mini-Circuits ZX60-272LN-S+).

For decoder validation against real DJI drones, the gold-standard test is
to capture a real DroneID burst with the B210, then loop-back via HackRF.
See `replay_capture` profile and the `--iq` flag.

---

## Troubleshooting

### "hackrf_transfer not found on PATH"
```bash
sudo apt install hackrf       # Pi/Ubuntu
brew install hackrf           # macOS
```

### "hackrf_open() failed" / "Resource busy"
Another process is using the HackRF. Find and kill it:
```bash
ps aux | grep hackrf
```
Or unplug/replug the HackRF.

### USB throughput errors on the Pi
HackRF needs **USB 3.0**. On the Pi 4, blue ports are USB 3.0; black are USB 2.0.
At 10 MS/s and above, USB 2.0 will drop samples.

### B210 sees nothing during transmission
Check the banner: if margin to −15 dBm is positive but very high (>50 dB),
the RX path may be receiving too little power. Either increase HackRF gain,
decrease distance, or check that the Yagi is pointed at the HackRF antenna.

### Cached IQ looks stale after editing config.yaml
Run with `--regenerate`:
```bash
python -m tools.hackrf_tx --regenerate
```
Or manually clear `data/hackrf_tx_cache/`.
