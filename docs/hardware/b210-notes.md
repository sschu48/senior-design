# USRP B210 — Hardware Notes

## Key Specs for SENTINEL

| Parameter | Value |
|---|---|
| RFIC | Analog Devices AD9361 |
| FPGA | Xilinx Spartan 6 XC6SLX150 |
| RX Channels | 2 (simultaneous MIMO) |
| TX Channels | 2 (not used for SENTINEL — passive only) |
| Freq Range | 70 MHz – 6 GHz |
| ADC Resolution | 12-bit |
| Max Sample Rate | 61.44 MSPS (per channel) |
| SISO Bandwidth | 56 MHz instantaneous |
| **MIMO Bandwidth** | **30.72 MHz per channel** (USB 3.0 bottleneck) |
| Interface | USB 3.0 SuperSpeed |
| Noise Figure | < 8 dB (internal, varies with frequency) |
| IIP3 | -20 dBm (at typical NF) |
| RX Gain Range | 0 – 76 dB |
| Max RX Input | **-15 dBm** (DO NOT exceed — damage risk) |
| Freq Accuracy | ±2.0 ppm (TCXO) |
| Wideband SFDR | 78 dBc |
| Phase Noise | 1.0° RMS @ 3.5 GHz, 1.5° RMS @ 6 GHz |
| Power (MIMO) | ~4W at 30.72 MSPS |
| Dimensions | 9.7 x 15.5 x 1.5 cm |
| Weight | 350g |

## Channel Assignment for SENTINEL

| Port | Channel | Antenna | Role |
|---|---|---|---|
| RX2 | RX-A | Omni dipole | Tripwire — omnidirectional energy detection |
| TX/RX | RX-B | Yagi directional | AoA — bearing estimation + packet decode |

**Note:** The TX/RX port is shared between TX and RX. Since SENTINEL is passive-only
(no transmission), we use TX/RX as a second RX port. This is the standard B210 MIMO
configuration for 2-channel receive.

## MIMO Bandwidth Limitation

In 2x2 MIMO mode, total USB 3.0 throughput is shared between both channels.
The AD9361 can do 56 MHz per chain, but USB 3.0 sustained throughput limits
the aggregate to approximately 61.44 MSPS total → 30.72 MSPS per channel.

**Impact for SENTINEL:**
- 30.72 MHz bandwidth covers 2.422 – 2.452 GHz (centered at 2.437 GHz)
- This captures WiFi Ch 6 (RemoteID default) + DJI DroneID (10 MHz)
- Does NOT cover the full 83.5 MHz ISM band simultaneously
- FHSS drones will hop into this window multiple times per second — acceptable

## USB 3.0 Requirements

- **Host controller matters.** Intel Series 7/8/9 chipset USB controllers recommended.
- Avoid USB hubs — connect B210 directly to host USB 3.0 port.
- Check for overruns: `uhd_usrp_probe` and monitor `O` characters in UHD output.
- If using laptop: verify USB 3.0 port is not shared with other high-bandwidth devices.

## Power Supply

- **MIMO operation requires external power** — USB bus power alone is insufficient.
- Use Ettus-provided 5.9V DC supply or equivalent (5V 4A minimum).
- Power connector: barrel jack on B210 board.

## Software Setup

```bash
# Install UHD
sudo apt install libuhd-dev uhd-host python3-uhd

# Download firmware images
sudo uhd_images_downloader

# Verify B210 detected
uhd_usrp_probe

# Test streaming (SISO)
uhd_rx_cfile -f 2.437e9 -r 30.72e6 -g 40 -N 30720000 test.cf32

# Test MIMO streaming
# (requires UHD Python API or GNU Radio — see src/sdr/)
```

## Input Protection

The B210 maximum safe input power is **-15 dBm**.

With our RF front end (worst-case: WiFi AP at 1m, +20 dBm EIRP, FSPL @ 1m ≈ 40 dB):

| Config | Antenna Gain | LNA | Cable Loss | Received | Safe? |
|---|---|---|---|---|---|
| PCB Yagi (testing) | +12 dBi | +20 dB | -1 dB | +11 dBm | **NO** |
| L-com HG2415Y (final) | +15 dBi | +20 dB | -1 dB | +14 dBm | **NO** |

Both exceed -15 dBm safe input by **26–29 dB** in this scenario.

**Mitigation:**
- Use LNA with built-in input protection / limiter
- Or add a 10 dB attenuator pad between LNA and B210 (reduces margin but protects hardware)
- Or use LNA with bypass/shutdown when strong signals detected
- **Never operate without the BPF — out-of-band signals can also damage the ADC**
