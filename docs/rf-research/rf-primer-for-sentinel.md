# RF Primer for SENTINEL

Status: living research note
Last updated: 2026-04-29

This note is meant to build intuition for SENTINEL's RF problem: detecting
consumer drone emissions passively around 2.4 GHz with a B210 SDR, an omni
antenna, and a directional Yagi.

It is not a replacement for field data. It is the mental model we use before
we measure.

## 1. The Short Version

SENTINEL is not a drone controller.

A drone controller usually knows the protocol, timing, hopping pattern,
modulation, coding, and packet structure. It can use matched filtering,
synchronization, error correction, and despreading to recover packets even when
the signal looks weak in a generic spectrum plot.

SENTINEL is initially a blind passive receiver. It looks for energy, burst
behavior, bandwidth, persistence, and bearing changes without the "answer key."
That is a harder problem.

The Yagi antenna helps, but it is not magic. It gives receive gain in one
direction and rejection elsewhere. It does not know which frequency hop matters,
which burst is coming next, whether the polarization matches, or whether the
signal is drone RF versus Wi-Fi clutter.

That is why Phase 1.5 exists: calibrate the receive chain before making drone
identity claims.

## 2. What SENTINEL Is Actually Measuring

The RF chain is:

```text
drone/controller/HackRF
    -> propagation through air
    -> antenna gain or loss
    -> cable/BPF/LNA/attenuator
    -> B210 analog front end
    -> ADC IQ samples
    -> FFT/PSD
    -> detector/event/tracker
```

At the detector level, we are not seeing "a drone." We are seeing evidence:

- Energy above local noise.
- A center frequency and bandwidth estimate.
- Burst timing or persistence.
- Yagi-vs-omni power difference.
- Bearing behavior as the Yagi rotates.
- Possibly decodable packets later.

The first honest labels are:

- `UNKNOWN_RF`
- `WIDEBAND_OFDM_LIKE`
- `NARROWBAND`
- `BURSTY`
- `PERSISTENT_WIFI_LIKE`

"Drone likely" should come only after multiple pieces agree.

## 3. Power Units: dBm, dB, dBi

RF engineers use logarithmic units because link budgets are big sums of gains
and losses.

| Unit | Meaning | Example |
|---|---|---|
| dBm | Absolute power referenced to 1 mW | 0 dBm = 1 mW |
| dB | Relative gain/loss | +6 dB is about 4x power |
| dBi | Antenna gain relative to isotropic | 12 dBi Yagi focuses energy |
| dBFS | Digital level relative to ADC full scale | SDR clipping concern |

Handy power anchors:

| Power | dBm |
|---|---|
| 1 W | +30 dBm |
| 100 mW | +20 dBm |
| 10 mW | +10 dBm |
| 1 mW | 0 dBm |
| 1 uW | -30 dBm |
| 1 nW | -60 dBm |
| 1 pW | -90 dBm |

Rules of thumb:

- +3 dB is about 2x power.
- +10 dB is 10x power.
- +20 dB is 100x power.
- Doubling distance adds about 6 dB free-space path loss.

## 4. Link Budget Basics

The first-order received power estimate is:

```text
P_rx_dBm =
    P_tx_dBm
  + G_tx_dBi
  - L_tx_dB
  - FSPL_dB
  + G_rx_dBi
  - L_rx_dB
```

Free-space path loss, with distance in km and frequency in MHz:

```text
FSPL_dB = 32.44 + 20*log10(distance_km) + 20*log10(freq_MHz)
```

At 2.437 GHz:

| Distance | Approx FSPL |
|---|---:|
| 1 m | 40 dB |
| 3 m | 50 dB |
| 10 m | 60 dB |
| 100 m | 80 dB |
| 1 km | 100 dB |

Example: HackRF tone test, conservative case.

```text
HackRF TX power estimate at --gain 0:  -55 dBm
Distance:                                3 m
FSPL at 2.437 GHz:                       about 50 dB
RX Yagi gain:                            +12 dBi
BPF/cable loss:                          -2 dB

Estimated B210 input:
  -55 - 50 + 12 - 2 = -95 dBm
```

That is weak but measurable in a narrowband test. With HackRF `--gain 20`, the
repo's current model estimates about -20 dBm TX, so the same 3 m setup lands
near -60 dBm at the B210 input through the Yagi path. That is much easier to
see, but it should still be treated carefully around LNAs and nearby antennas.

## 5. Noise Floor: Bandwidth Is the Trap

Thermal noise density at room temperature is about:

```text
-174 dBm/Hz
```

Receiver noise over bandwidth is roughly:

```text
Noise_dBm = -174 + 10*log10(B_hz) + NF_dB
```

If the B210 receive chain has about 8 dB noise figure, rough noise floors are:

| Measurement bandwidth | Thermal + 8 dB NF |
|---|---:|
| 3.75 kHz FFT bin | about -130 dBm |
| 200 kHz tone measurement band | about -113 dBm |
| 10 MHz wideband signal | about -96 dBm |
| 30.72 MHz full capture | about -91 dBm |

This is why a signal can be visible in one detector and invisible in another.
A narrow tone concentrates power. A 10 MHz signal spreads power. A protocol
receiver may integrate, correlate, decode, and error-correct. A blind energy
detector often needs a bigger margin.

## 6. Why The Controller Can Work At A Mile

This was the confusing part, and it is the right question.

A controller can receive a drone signal far away because it has several
advantages:

1. It is designed for that exact waveform.
2. It knows the expected channel bandwidth and packet timing.
3. It can synchronize to preambles or pilots.
4. It can use channel coding and error correction.
5. It may use diversity, MIMO, or optimized antennas.
6. It follows the hopping/timing behavior instead of sweeping blindly.
7. It can decode packets below what looks obvious on a generic PSD.

SENTINEL's first detector path does not have those advantages. It asks:

```text
Is there suspicious RF energy here, right now, from this direction?
```

That is useful, but it is less powerful than:

```text
Given this known protocol, recover this expected packet stream.
```

The upgrade path is therefore:

```text
energy detection
  -> event timing
  -> bandwidth and modulation features
  -> bearing behavior
  -> replayable captures
  -> protocol-specific decoding where possible
```

## 7. Why A Big Yagi Can Still Miss Things

A Yagi improves the receive link only when the RF setup is aligned with reality.

Common failure modes:

- The antenna points the wrong way during a short burst.
- The drone is hopping outside the tuned bandwidth.
- The signal is vertically polarized and the Yagi is horizontal, or vice versa.
- The Yagi pattern has side lobes and nulls that were never measured.
- Nearby Wi-Fi creates a higher local noise/interference floor.
- The B210 gain is too low, so weak signals vanish.
- The B210 gain is too high, so strong signals compress or clip.
- The signal lands near DC and DC-offset correction suppresses it.
- The detector's min-bandwidth or threshold gate rejects the signal.
- Multipath makes the strongest bearing different from the true bearing.

The HackRF bench harness intentionally catches some of these. For the CW tone
profile, `tools.hackrf_bench` tunes the B210 1 MHz low while measuring the
HackRF at 2.437 GHz. That keeps the tone away from DC so the smoke test is not
destroyed by DC removal.

## 8. What The HackRF Bench Tests Prove

The HackRF test environment gives us controlled RF evidence.

### Test 1: Conducted Cable

```text
HackRF -> 40-60 dB attenuation -> splitter -> B210 RX-A/RX-B
```

This proves:

- Both B210 channels work.
- The expected frequency appears where it should.
- RX-A/RX-B relative gain offset is measurable.
- The detector can see a known signal without multipath.

This does not prove range.

### Test 2: Low-Power Radiated Tone

```text
HackRF dipole -> air -> omni/Yagi -> B210
```

This proves:

- The antennas and RF front end can receive a real 2.4 GHz emitter.
- Yagi pointing changes the measured SNR.
- The site has some baseline noise/clutter level.

This does not prove drone classification.

### Test 3: Continuous OFDM-Like Signal

Use `ocusync_video`.

This proves:

- Wideband detection works better than the tone-only case.
- CFAR behavior can be evaluated on a 9 to 10 MHz signal.
- Yagi-vs-omni comparisons become closer to the target use case.

This still does not prove DJI decoding.

### Test 4: Bursty DroneID-Like Signal

Use `dji_droneid`.

This tests:

- Low duty cycle detection.
- Event persistence logic.
- Whether frame timing misses short bursts.

Misses here are expected early. A 1 ms burst every 600 ms is easy to miss if
the capture/PSD framing and detector timing are not matched to the burst.

## 9. How To Critique Drone RF Detection Papers

When reading RF drone detection papers, ask these questions before trusting the
accuracy number:

1. Was the model trained and tested on the same devices?
2. Was the train/test split by sample, flight, device, day, or location?
3. Did the classifier accidentally learn lab artifacts instead of drones?
4. Was Wi-Fi/Bluetooth interference present?
5. Was the receive antenna fixed, directional, or scanning?
6. Did the paper detect controller uplink, drone downlink, Remote ID, or all of
   those mixed together?
7. Was range tested outdoors with realistic geometry?
8. Did they report SNR, false positives, and false negatives?
9. Did they preserve raw IQ for replay?
10. Is the result detection, identification, localization, or decoding?

Papers often report strong classification results when the dataset is controlled
and labeled. SENTINEL's hard problem is open-world: unknown RF, unknown drone,
unknown bearing, unknown range, and lots of normal 2.4 GHz devices.

## 10. Research Takeaways For SENTINEL

The practical path is:

1. Measure the receive chain.
2. Measure the Yagi pattern.
3. Build replayable IQ artifacts.
4. Detect RF events conservatively.
5. Add features: bandwidth, burst period, duty cycle, hop behavior, bearing.
6. Only then attempt likely-drone classification.

The best early score is not "drone yes/no." It is:

```text
UNKNOWN_RF event at 2.437 GHz,
9 MHz bandwidth,
max SNR 18 dB,
seen by omni and Yagi,
Yagi strongest at 75 deg,
burst period roughly 600 ms,
raw IQ saved.
```

That is useful evidence. It can be replayed, challenged, and improved.

## 11. Reading Order

Start here:

1. [HackRF bench setup](../hardware/hackrf-bench-setup.md)
2. [Phase 1.5 RF validation plan](../phase-1-5-rf-validation.md)
3. [B210 hardware notes](../hardware/b210-notes.md)
4. [Drone emission profiles](drone-emissions.md)
5. This primer's external references below

## 12. External References

### Hardware and SDR

- [Ettus USRP B210 product page](https://www.ettus.com/all-products/ub210-kit/)
  - B210 overview, 70 MHz to 6 GHz coverage, AD9361, USB 3.0, real-time bandwidth.
- [Ettus B200/B210 spec sheet PDF](https://www.ettus.com/wp-content/uploads/2019/01/b200-b210_spec_sheet.pdf)
  - Includes the useful 2x2 bandwidth number: up to 30.72 MHz instantaneous bandwidth in 2x2 mode.
- [UHD manual: USRP B2x0 series](https://files.ettus.com/manual/page_usrp_b200.html)
  - Important for B210 MIMO: receive front ends share the RX LO in MIMO.
- [Analog Devices AD9361 product page](https://www.analog.com/en/products/ad9361.html)
  - RFIC inside the B210; covers receiver bandwidth, gain control, ADCs, direct conversion architecture.
- [HackRF One documentation](https://hackrf.readthedocs.io/en/stable/hackrf_one.html)
  - HackRF frequency range, 2 to 20 MS/s sample rates, and transmit-power caveats.

### Regulations and Remote ID

- [FCC 47 CFR 15.247](https://www.law.cornell.edu/cfr/text/47/15.247)
  - US rules for 902-928 MHz, 2400-2483.5 MHz, and 5725-5850 MHz spread-spectrum/digital systems.
- [FAA Remote ID overview](https://www.faa.gov/uas/getting_started/remote_id)
  - FAA explanation of broadcast Remote ID and compliance paths.
- [ASTM F3411-22 Remote ID standard page](https://store.astm.org/f3411-22.html)
  - Standard scope and official purchase page. The full standard is not freely available.

### Propagation and Link Budgets

- [ITU emergency telecommunications handbook, technical annex PDF](https://www.itu.int/en/ITU-D/Emergency-Telecommunications/Documents/Publications/handbook/pdf/Emergency_Telecom-e_partIII.pdf)
  - Contains free-space basic transmission loss formulas and RF conversion formulas.
- [ITU-R report RA.2510-0 PDF](https://www.itu.int/dms_pub/itu-r/opb/rep/R-REP-RA.2510-2022-PDF-E.pdf)
  - Discusses the classic free-space path loss equation in dB.

### Drone RF Research

- [Schiller et al., "Drone Security and the Mysterious Case of DJI's DroneID," NDSS 2023](https://www.ndss-symposium.org/ndss-paper/drone-security-and-the-mysterious-case-of-djis-droneid/)
  - DJI DroneID reverse engineering and decoding context.
- [Schiller et al. NDSS 2023 paper PDF](https://www.ndss-symposium.org/wp-content/uploads/2023-217-paper.pdf)
  - Full paper PDF.
- [Nguyen et al., "Matthan: Drone Presence Detection by Identifying Physical Signatures in the Drone's RF Communication," MobiSys 2017 PDF](https://wsslab.org/publications/papers/Drone_MobiSys_2017.pdf)
  - Early passive RF drone-presence work using physical signal signatures.
- [Ezuma et al., "Detection and Classification of UAVs Using RF Fingerprints in the Presence of Wi-Fi and Bluetooth Interference," IEEE Xplore](https://ieeexplore.ieee.org/document/8913640)
  - Passive RF classification under common 2.4 GHz interference.
- [Chiper et al., "Drone Detection and Defense Systems: Survey and a Software-Defined Radio-Based Solution," Sensors 2022](https://pubmed.ncbi.nlm.nih.gov/35214355/)
  - Survey with emphasis on SDR-based RF methods.
- [Elyousseph and Altamimi, "Robustness of Deep-Learning-Based RF UAV Detectors," Sensors 2024](https://www.mdpi.com/1424-8220/24/22/7339)
  - Useful recent reminder that ML RF detectors need robustness testing, not just accuracy on one dataset.
- [Mrabet et al., "Machine learning algorithms applied for drone detection and classification," Frontiers 2024](https://www.frontiersin.org/journals/communications-and-networks/articles/10.3389/frcmn.2024.1440727/full)
  - Broad ML drone detection survey across modalities, including RF.

## 13. SENTINEL-Specific Mantra

Do not ask "did we detect a drone?" first.

Ask:

```text
Did we measure a signal?
Can we repeat the measurement?
Do both RX channels agree?
Does the Yagi bearing make physical sense?
Did we save raw IQ?
Can the same event be replayed?
What non-drone explanation fits?
```

That discipline is what turns a fun spectrum demo into a real RF system.
