
# SENTINEL Architecture v2: Detection Strategy Re-evaluation

> **Status:** Design analysis / research note
> **Purpose:** Document the rationale for moving from a single-Yagi energy-bump detector to a cued, structure-based detection architecture. This file is intended as context for downstream design and implementation work.

---

## 1. Problem statement

The original SENTINEL design assumes that a directional antenna (Yagi) swept across the horizon will produce a measurable RSSI peak when boresight crosses a transmitting drone. In open-field conditions with low ambient RF, this is physically correct: directional gain raises target signal power and rejects off-axis noise, so an energy excursion above the noise floor is a valid detection statistic.

This document re-examines that assumption against realistic drone protocols and proposes architectural changes to address three failure modes that the v1 design does not handle well:

1. **Frequency hopping** — modern drone control links (ELRS, Crossfire, OcuSync) hop across tens of MHz at rates from ~50 Hz up to ~500 Hz, so any single-frequency narrowband receiver spends most of its time off-channel.
2. **Bursty duty cycle** — packets are short (often <10 ms) with significant gaps. A receiver that dwells briefly per bearing may never coincide with a transmission.
3. **Bearing × frequency × time search space** — sweeping a directional antenna while also scanning frequency creates a 3D search problem where dwell time per cell is too short for reliable detection.

## 2. RF fundamentals grounding

### 2.1 Cooperative vs. non-cooperative receivers

A drone controller is a *cooperative* receiver: it knows the protocol, frequency plan, hop sequence, preamble, and modulation. It detects "is there a valid packet" rather than "is there energy here." This is why a controller appears to "just know" — it is performing matched filtering and protocol decode, not energy detection.

SENTINEL is a *non-cooperative* receiver. It does not need to decode anything. Its job is to detect anomalous emissions whose characteristics are consistent with drone control or telemetry links. This is in principle a simpler problem than decoding, but it has different design constraints.

### 2.2 Detection SNR vs. decode SNR

Energy detection typically requires only 3–10 dB SNR above the noise floor. Coherent decoding of OFDM or FHSS signals typically requires 15–25+ dB depending on modulation order and coding. The non-cooperative receiver therefore has a substantial SNR margin to work with — it can detect signals that would not be decodable.

### 2.3 Processing gain via narrowing resolution bandwidth

The "noise floor" is not a fixed scalar; it is a function of resolution bandwidth. A wideband capture FFT'd into N bins reduces the per-bin noise floor by approximately 10·log10(N) dB relative to the full capture bandwidth. A 16,384-point FFT over 56 MHz of capture gives ~3.4 kHz bins and ~42 dB of processing gain relative to a wideband power meter. This processing gain is comparable to or exceeds the directional gain of a typical Yagi (10–15 dBi).

**Implication:** narrowband signals embedded in a wideband capture can be detected without directional antenna gain, simply by FFT'ing finely enough that the signal occupies bins where the per-bin noise floor is well below the signal power.

### 2.4 Structural selectivity vs. spatial selectivity

A Yagi provides selectivity in the *spatial* domain. The same signal-vs-background separation can be achieved through selectivity in other domains:

- **Frequency** — drones use known channel widths and band allocations
- **Time** — drones have characteristic burst durations and inter-burst spacings
- **Cyclostationary** — OFDM cyclic prefixes and FHSS hop rates produce detectable periodicities that are present even when the signal is below the wideband noise floor in raw power
- **Modulation** — specific modulation schemes (e.g., LoRa chirps, FSK deviations, OFDM subcarrier spacing) have detectable signatures

These are stackable. A detector that combines per-bin power thresholding, burst characterization, and cyclostationary feature detection can achieve effective signal-to-clutter performance that exceeds what a single Yagi provides spatially, while being non-directional.

## 3. Target protocol characterization

The architecture must handle, at minimum, the following representative protocols:

| Protocol     | Band(s)         | Channel BW | Hop rate    | Burst length | Notes                          |
|--------------|-----------------|------------|-------------|--------------|--------------------------------|
| ELRS         | 900 MHz, 2.4 GHz| ~800 kHz   | up to 500 Hz| <10 ms       | Hardest target; very bursty    |
| TBS Crossfire| 868/915 MHz     | ~250 kHz   | ~50 Hz      | short        | Similar profile to ELRS        |
| DJI OcuSync 2/3| 2.4 / 5.8 GHz | ~10 MHz    | ~tens of ms | high duty    | Wider, easier to catch         |
| Wi-Fi-based  | 2.4 / 5 GHz     | 20 MHz     | static      | high duty    | Easy mode                      |

ELRS at 2.4 GHz is the design driver: ~2 ms dwell on any given frequency, ~80 MHz total hop range, packets often <10 ms. A narrowband sequential scanner will miss this with very high probability.

## 4. Architectural changes

The v2 architecture introduces five interacting changes. Each addresses a specific failure mode of v1.

### 4.1 Wideband instantaneous capture (collapses the frequency axis)

Replace narrowband sequential frequency scanning with wideband instantaneous capture and real-time FFT. With a USRP capable of 30–160 MHz instantaneous bandwidth, a single capture covers most or all of the 2.4 GHz ISM band. Frequency hoppers are always inside the capture window; the detector only needs to find which FFT bin currently contains energy.

Implementation requirements:

- USRP I/Q streaming at sufficient sample rate (e.g., 56 Msps for 2.4 GHz ISM coverage)
- Real-time FFT pipeline (GNU Radio, or custom with FFTW / cuFFT / VkFFT)
- Per-bin power estimation with configurable integration time
- Rolling spectrogram buffer (1–10 s) for downstream feature extraction

This change alone resolves the frequency-hopping problem.

### 4.2 Two-stage spatial architecture (decouples bearing search from detection)

Add an omnidirectional antenna on a second coherent SDR channel for continuous wideband monitoring. The Yagi is no longer the primary detector; it becomes a cued bearing estimator.

Roles:

- **Omni channel:** continuous wideband detection. Does not depend on direction. Uses structural features (per-bin power, burst characterization, cyclostationary signatures, optionally ML classifiers) to flag candidate drone signals. This channel determines *whether* and *what*.
- **Yagi channel:** cued sweep on demand. When the omni flags a candidate, the Yagi rotator initiates a directed sweep to localize bearing. Because it operates only when cued, it can dwell longer per bearing and produce higher-quality bearing estimates. This channel determines *where*.

Hardware: minimum USRP B210 (two RX channels, coherent). Better: X310 with two daughterboards, or N310-class hardware.

This change resolves the dwell-time problem: detection is no longer mechanically bottlenecked by rotator speed.

### 4.3 Structure-based detection pipeline (replaces energy-bump heuristic)

The omni cannot rely on directional gain to produce a "bump." It must instead detect drone signals by their structural signatures. The detection pipeline becomes multi-stage:

**Stage 1 — Cheap candidate generation (always-on):**
- Wideband FFT
- Per-bin power thresholding against an adaptive noise-floor estimate (e.g., percentile-based or median-tracking)
- Burst detection in the time-frequency plane: identify (start_time, end_time, center_freq, bandwidth) tuples
- Cluster bursts into candidate emissions

**Stage 2 — Feature extraction and classification (triggered):**
- For each candidate, extract features: bandwidth, duration, inter-burst spacing, hop pattern, spectral shape
- Optional cyclostationary analysis on candidate time-frequency regions
- Classification: rule-based thresholds (e.g., "800 kHz × 2 ms × ~500 Hz hop pattern → ELRS") and/or learned classifier on spectrogram patches
- Output: classification confidence, suspected protocol, time-frequency footprint

**Stage 3 — Bearing estimation (cued Yagi):**
- Rotator sweep over the candidate's frequency band
- Bearing estimate from peak Yagi RSSI in the candidate's specific FFT bins
- Bearing confidence based on peak sharpness vs. sidelobe pattern

This pipeline replaces "is there a power bump?" with "is there a structurally drone-shaped emission, and if so, where is it coming from?"

### 4.4 Cued / state-machine sweep control (replaces open-loop rotator)

Rotator control becomes closed-loop with detector output. Suggested state machine:

```
IDLE → CUED → BEARING_SEARCH → TRACK → ALARM
  ↑                                       │
  └───────────────────────────────────────┘
```

- **IDLE:** omni monitors continuously; Yagi parked or slow-sweeping
- **CUED:** omni has flagged a candidate; rotator initiates focused sweep over the candidate frequency
- **BEARING_SEARCH:** rotator sweeps; per-bearing power in candidate bins is logged
- **TRACK:** peak bearing identified; Yagi locks; ongoing detections refine estimate
- **ALARM:** detection confidence exceeds threshold; event emitted

This also opens the door to external cueing (acoustic, optical, network) without architectural rework.

### 4.5 Optional: matched filtering for known protocols

For protocols with publicly characterized preambles (ELRS, OcuSync sync words), a per-protocol matched filter can be added as a high-confidence detection path. This effectively makes SENTINEL a partial cooperative receiver for known protocols while remaining non-cooperative in general. Highest implementation cost; highest detection confidence; narrowest coverage.

## 5. The omni detection question (key design rationale)

A natural objection: if the omni has no directional gain, how does it detect drone signals against ambient noise? This section addresses that explicitly because it is central to the architecture's validity.

### 5.1 The omni does not detect via raw power

The v1 detection statistic was "is power at this bearing above the noise floor?" The v2 omni does not use this statistic. Its detection statistic is "is there a signal in the capture window whose time-frequency-modulation structure matches a drone protocol?"

These are different questions. The first requires the signal to dominate the average power in the band. The second only requires the signal to be *distinguishable by structure* — which is a much weaker condition.

### 5.2 Sources of effective gain without directionality

Without spatial gain, the omni still benefits from:

1. **FFT processing gain** (~40 dB for the FFT sizes we plan to use) — narrowband drone signals do not compete with the full wideband noise power, only with the noise in their own bins.
2. **Time-domain selectivity** — short bursts at known durations are easily separable from continuous emitters like Wi-Fi.
3. **Cyclostationary gain** — periodic features can be integrated coherently across many cycles, pulling signals out from below the noise floor.
4. **Protocol-specific priors** — drones use a small, finite set of protocols with well-characterized signatures; the detector exploits this.

The combined effective gain from these sources typically exceeds the 10–15 dBi a single Yagi provides, except in the specific case of weak distant signals in an otherwise quiet band.

### 5.3 The Wi-Fi analogy

A phone with an omnidirectional antenna reliably detects Wi-Fi APs at -90 dBm in dense RF environments. It does not do this by detecting a power bump. It detects by listening for 802.11 PHY-layer structure (preambles, modulation, timing). The same principle applies to SENTINEL's omni channel for drone signals.

### 5.4 Where the omni is genuinely worse than v1's Yagi

To be honest about trade-offs, structure-based omni detection is *worse* than directional energy detection in the following cases:

- **Weak distant drone in a quiet band** — pure SNR margin matters here, and the Yagi's 10–15 dB advantage was real. The v2 cued Yagi recovers some of this for confirmation but not for initial detection.
- **Novel/unknown protocols** — structure-based detection depends on priors. A protocol that doesn't match known patterns may slip through unless the generic burst-detection layer is tuned permissively.
- **Strong nearby interferers causing front-end desense** — without spatial filtering, a powerful nearby Wi-Fi AP can saturate the SDR ADC or compress the front end, raising the effective noise floor across the entire capture. Mitigations: SAW filters, careful gain staging, possibly cavity filters for known interferers.

These are real costs and should be tracked in the FMEA.

## 6. Block diagram

```
┌──────────────┐        ┌─────────────────────────────────────┐
│  Omni        │───────▶│  SDR RX channel A                   │
│  antenna     │        │  (continuous wideband capture)      │
└──────────────┘        └────────────────┬────────────────────┘
                                         │
                                         ▼
                        ┌─────────────────────────────────────┐
                        │  Wideband FFT + spectrogram buffer  │
                        └────────────────┬────────────────────┘
                                         │
                                         ▼
                        ┌─────────────────────────────────────┐
                        │  Stage 1: burst / power detector    │
                        │  → candidate (t, f, BW, dur)        │
                        └────────────────┬────────────────────┘
                                         │
                                         ▼
                        ┌─────────────────────────────────────┐
                        │  Stage 2: feature extraction +      │
                        │  classifier (rules / ML / cyclo)    │
                        └────────────────┬────────────────────┘
                                         │  cue
                                         ▼
┌──────────────┐        ┌─────────────────────────────────────┐
│  Yagi on     │───────▶│  SDR RX channel B (cued capture)    │
│  rotator     │◀───────│  Rotator control state machine      │
└──────────────┘        └────────────────┬────────────────────┘
                                         │
                                         ▼
                        ┌─────────────────────────────────────┐
                        │  Bearing estimation on candidate    │
                        │  frequency bins                     │
                        └────────────────┬────────────────────┘
                                         │
                                         ▼
                        ┌─────────────────────────────────────┐
                        │  Detection event:                   │
                        │  {time, freq, BW, bearing, class,   │
                        │   confidence, spectrogram snippet}  │
                        └─────────────────────────────────────┘
```

## 7. Implementation priorities

Suggested sequencing for incremental development:

1. **Wideband capture + spectrogram pipeline** on the omni channel. Validate against known emitters (Wi-Fi beacons, Bluetooth, a test ELRS transmitter if available). This is the foundation; nothing else works without it.
2. **Stage 1 burst detector.** Adaptive noise-floor estimation, burst extraction, basic clustering. Tune false-alarm rate against real ambient ISM data.
3. **Stage 2 rule-based classifier** for ELRS and OcuSync. Avoid ML until rule-based performance is characterized — this gives a baseline to compare against and forces explicit feature engineering.
4. **Two-channel coherent SDR + Yagi cueing.** Closed-loop rotator state machine. Validate cued bearing estimation against the same test transmitters.
5. **ML classifier on spectrogram patches** (optional, if rule-based hits a ceiling). Train on labeled captures from steps 1–2.
6. **Matched filtering** for ELRS preamble (optional, highest-confidence path).

## 8. Updated FMEA considerations

New failure modes introduced by v2 that should be added to the FMEA:

- **Wideband front-end overload** by strong nearby ISM emitters; characterize ADC headroom and gain staging.
- **Stage 1 false-alarm rate** against real-world ambient ISM (not lab conditions). Requires field data.
- **Cued-search latency** from omni trigger to Yagi-on-target. Budget this end-to-end (detector latency + state machine + rotator slew).
- **Channel coherence and calibration** between RX A and RX B if any future work uses phase information (TDOA / beamforming).
- **Compute headroom** for real-time FFT + Stage 1 + Stage 2 on the target hardware. Profile early.
- **Detector blind spots** for protocols not represented in the rule set / training data.

## 9. Open questions

Items that need resolution before committing to specific implementations:

- **Target instantaneous bandwidth.** 56 MHz covers 2.4 GHz ISM cleanly but requires ~225 MB/s sustained streaming at 16-bit I/Q. Does the planned host machine support this without dropped samples?
- **FFT size and integration time.** Trade-off between frequency resolution (favors larger FFT) and time resolution for short bursts (favors smaller FFT). ELRS bursts at ~2 ms set a hard upper bound on integration window.
- **Adaptive noise-floor algorithm.** Median tracking, percentile-based, or Otsu-style? Each has different behavior under non-stationary interference.
- **Classifier ground-truth strategy.** Where do labeled spectrograms come from? Self-collected with a known transmitter, public datasets (DroneRF, DroneDetect), or both?
- **900 MHz coverage.** ELRS / Crossfire 900 MHz support requires either a separate front end or band switching. Defer or include in v2?

## 10. Summary

The v1 design is correct as physics but architecturally fragile against modern bursty, frequency-hopping drone protocols. The v2 design addresses this by:

- replacing narrowband sequential scanning with wideband instantaneous capture (frequency axis collapses)
- replacing the single-Yagi detector with a two-stage omni-detect / Yagi-localize architecture (bearing axis decouples from detection)
- replacing the energy-bump statistic with structure-based detection that exploits FFT processing gain, time-frequency burst features, and cyclostationary signatures (compensates for loss of directional gain)
- replacing the open-loop rotator with a state-machine-driven cued sweep (intelligent dwell allocation)

The trade-off is real: the omni gives up ~10–15 dB of directional gain for primary detection. This is recovered through ~40 dB of FFT processing gain plus structural priors, with the cued Yagi providing additional confirmation SNR for events that warrant it. The net result is a system that handles the full target protocol set including the hardest case (ELRS) rather than only the easy case (continuous-emission drones in quiet bands).
