# RF Design Notes

## What to pick up from the drone

There are multiple frequencies we can monitor and pickup a signature: 
1. Video Downlink: 2.4 GHz / 5.8 GHz 
- Modulation is OFDM
- Has high EIRP and continuous

2. Control Uplink: 2.4 GHz
- Modulation: FHSS / DSSS
- Lower power but periodic

3. WiFi Telemetry: 2.3-5.8 GHz
- Modulation: 802.11
- Beacon Frames

4. GPS L1: 1.575 GHz
- Modulation: BPSK
- Useful if retransmitted

The best approach would be to use an emission that we know (ex. 5.8 GHz video downlink)

*Note: Learn more about the modulation techniques and why each emission could be useful*

## Angle of Arrival (AoA)
This is an approach for signal detection that uses maximum signal strength during the antenna rotation to identify the location of the signal being received. 

For example, with one station, rotating constantly, it will identify the bearing where there is a maximum signal strength.


## Passive Radar
A passive radar system does not transmit any signal. Common radars use Continuous Wave or Pulses using a Tx antenna measuring the time of the signal coming back. Passive radar systems use ambient RF emmisions. In our example we will be using the emissions from a drone target. 

## The Flow from RF Emission to DSP
### 1. Drone transmits RF signal from its Tx antenna

### 2. Signal will propagate through space

### 3. Yagi antenna will receive signal
- This antenna is highly directional $(30-50\degree beamwidth)$
- *Learn more about Yagi Antenna*

**Yagi Antenna**

This type of antenna is used because of directionality. The main lobe provides a bearing. The accuracy of this bearing depends on the beamwidth. For reference, a 12-element Yagi has $~35\degree$ beamwidth.

Yagi also provides additional gain ~24 dBi gain. This will boost distance signals ~25x, helping diffrienciate from noise. 

Narrow beams regect signals from other directions, removing unwanted noise.

**What the signal looks like at this stage**
- Very tiny voltage $~10-100\micro V$ Free space loss is huge
- Frequency is $5.8 GHz$ which is too fast to see on device like oscilloscope
- Waveform is modulated data: **OFDM** = hudnreds of subcarriers turning on/off

### 4. Induced current in antenna
- Electric field oscillates free electrons inside antenna
- Voltage is induced in **driven element**
- Parasitic elements reinforce forward beam

### 5. Signal is fed through coax

### 6. Low Noise Amplificatino (LNA) 
- Noise amplifier will need to boost the weak signal
- Preserves **Signal-to-noise ratio (SNR)**

Once the signal is sent through an LNA it will amplify the voltage to ~0.1-1 V peak. LNA gain provides ~+30dB \to x31.6 in voltage. Frequency has not changed yet. Waveform is still chaotic but can see on spectrum analyzer. This is the **Power Spectral Density**.

### 7. Downconversion to baseband or IF
- *Need to understand this better...*
- 5.8 Ghz signal is mixed with a local oscillator (LO) which produces:
    - **Intermediate Frequency (IF)**
    - **Direct I/Q baseband**
- This enables digital sampling

After downconverter, 5.8 GHz signal is shifted down to more manageable frequency. 
- Local Oscillator (LO) = 5.78 GHz
- Mixer output = IF = 5.8 - 5.78 = 20 MHz

8. Analog-to-Digital Conversion (ADC)
- High Speed ADC digitizes signal
- Complex samples producsd (I and Q channels) *Another thing to learn*

ADC samples at 80 MS/s (80 million samples / sec)
- Each sample = I (in-phase) and Q (quadrature) $\to$ complex number
- At this stage you will see a stream of numbers and an $$IQ Plot$$

9. DSP begins
