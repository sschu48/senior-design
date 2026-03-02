# SENTINEL — Linux (Ubuntu) Setup Guide

## Prerequisites

- **OS:** Ubuntu 22.04 or 24.04 (other Debian-based distros may work with adjustments)
- **USB:** USB 3.0 port (required for B210 MIMO throughput)
- **Disk:** ~2 GB free (UHD images, node_modules, venv, IQ captures)
- **User:** Must have `sudo` access

## Quick Start

Clone and run the automated setup:

```bash
git clone https://github.com/sschu48/senior-design.git
cd senior-design
bash scripts/setup-ubuntu.sh
```

The script is idempotent — safe to run again if interrupted or if you need to update.

After setup:

```bash
source .venv/bin/activate
make test          # verify Python environment
make run-radar     # start the radar app on http://localhost:3000
```

## What the Setup Script Does

1. **System packages** — python3, python3-venv, python3-uhd, build-essential, libuhd-dev, libusb, etc.
2. **Node.js 20 LTS** — via NodeSource (Express 5 requires Node >= 18)
3. **Python venv** — `.venv` with `--system-site-packages` + `pip install -e ".[dev,hardware]"`
4. **Radar app** — `npm install` in `radar-app/`
5. **UHD/B210** — FPGA image download, udev rules
6. **Serial access** — adds user to `dialout` group
7. **Directories** — creates `data/samples/`, `data/signatures/`, `logs/`, `docs/test-logs/`
8. **Verification** — runs pytest to confirm

## Manual Setup (Step by Step)

If you prefer to set things up manually or are on a non-Ubuntu system.

### System packages

```bash
sudo apt-get update
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    build-essential libuhd-dev uhd-host python3-uhd \
    libusb-1.0-0-dev git curl
```

### Node.js 20

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version  # should be v20.x
```

### Python venv

The venv **must** use `--system-site-packages` because the UHD Python bindings (`import uhd`) come from the system package `python3-uhd` — they are not available on PyPI.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev,hardware]"
```

### Radar app

```bash
cd radar-app
npm install
cd ..
```

### B210 FPGA images

```bash
sudo uhd_images_downloader --types b2xx
```

### udev rules

```bash
# Find the rules file (path varies by Ubuntu version)
sudo cp /usr/lib/uhd/utils/uhd-usrp.rules /etc/udev/rules.d/
# or: sudo cp /usr/share/uhd/utils/uhd-usrp.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### Serial port access

```bash
sudo usermod -aG dialout $USER
# Log out and back in for this to take effect
```

## B210 Verification

With the B210 connected via USB 3.0:

```bash
uhd_usrp_probe
```

Expected output includes:

```
-- UHD Device 0
-- Device: B-Series Device
-- Mboard: B210
-- RX Channel: 0 (A:RX2)
-- RX Channel: 1 (A:TX/RX)
```

Quick Python check inside the venv:

```bash
python -c "import uhd; usrp = uhd.usrp.MultiUSRP(); print(usrp.get_mboard_name())"
```

## Running the Project

```bash
# Activate venv (or use make targets which handle this automatically)
source .venv/bin/activate

# Run tests
make test              # full suite
make test-quick        # skip slow/field tests

# Start radar app (Express + Socket.IO)
make run-radar         # http://localhost:3000

# Launch spectrum analyzer
make spectrum

# See all available commands
make help
```

## Troubleshooting

### "No UHD Devices Found"

1. Check USB connection: `lsusb | grep Ettus` (should show `2500:0020`)
2. Verify FPGA images: `sudo uhd_images_downloader --types b2xx`
3. Check udev rules are installed (see above)
4. Try unplugging and re-plugging the B210
5. Ensure USB 3.0 port (USB 2.0 works but MIMO will fail with overruns)

### USB overruns (`O` characters in output)

The B210 in MIMO mode streams 30.72 MS/s per channel. USB overruns mean the host can't keep up.

- Ensure USB 3.0 connection (check `lsusb -t` for speed)
- Reduce sample rate in `config.yaml` (try 15.36 MS/s)
- Close other USB-heavy devices
- Check CPU load — DSP might be backlogging the pipeline

### "Permission denied" on serial port (`/dev/ttyUSB0`)

```bash
# Add yourself to dialout
sudo usermod -aG dialout $USER
# Then log out and back in (or reboot)

# Quick test without logout:
sudo chmod 666 /dev/ttyUSB0  # temporary, resets on reboot
```

### `import uhd` fails inside the venv

The UHD Python bindings come from the system package `python3-uhd`, not PyPI. The venv must have access to system packages.

```bash
# Check venv was created correctly
cat .venv/pyvenv.cfg | grep system-site-packages
# Should show: include-system-site-packages = true

# If not, recreate the venv:
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ".[dev,hardware]"
```

### Node version too old

Express 5 requires Node >= 18. Check your version:

```bash
node --version
```

If it's too old, install Node 20 via NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

## Hardware Gotchas

### B210 input power limit

The B210 ADC max safe input is **-15 dBm**. With an external LNA (~20 dB gain) and the Yagi antenna (12–15 dBi), a nearby WiFi AP at close range can exceed this.

**Mitigation:** Install an input limiter or switchable attenuator between the LNA output and the B210 SMA input.

### MIMO bandwidth

MIMO mode = **30.72 MHz per channel** (not 56 MHz). This is a USB 3.0 throughput limitation. The 30.72 MHz bandwidth covers 2.422–2.452 GHz when centered on WiFi Ch 6 (2.437 GHz). This is sufficient for DJI DroneID (10 MHz) and standard WiFi (20 MHz).

### External DC blocking

If your LNA is DC-powered through the coax (bias-tee), ensure the B210's bias-tee is **disabled** unless you specifically need it. An unexpected DC path can damage front-end components.

Check B210 bias-tee status:

```python
import uhd
usrp = uhd.usrp.MultiUSRP()
# B210 does not have a software-controllable bias-tee by default
# External bias-tee injectors are the safe approach
```
