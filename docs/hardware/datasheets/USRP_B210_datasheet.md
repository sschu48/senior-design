# USRP B200 & B210 – Technical Specifications

**Overview**  
The USRP B200 and B210 are software-defined radio (SDR) platforms covering RF frequencies from **70 MHz to 6 GHz**. They feature a Xilinx Spartan-6 FPGA and high-speed **USB 3.0** connectivity (backward compatible with USB 2.0). Both devices use an Analog Devices RFIC and support streaming up to 56 MHz of instantaneous bandwidth (on compatible USB 3.0 chipsets). They are fully integrated with the open-source **USRP Hardware Driver (UHD)**, enabling seamless use with GNU Radio, C++, Python, and portability to other USRP platforms (X310, E310, etc.).

### Key Product Differences

| Feature                          | USRP B200                          | USRP B210                                      |
|----------------------------------|------------------------------------|------------------------------------------------|
| Channels                         | 1 TX + 1 RX (half or full duplex)  | 2 TX + 2 RX (half or full duplex)              |
| MIMO Capability                  | —                                  | Fully-coherent 2×2 MIMO                        |
| FPGA                             | Xilinx Spartan-6 XC6SLX75          | Xilinx Spartan-6 XC6SLX150 (larger)            |
| Instantaneous Bandwidth          | Up to 56 MHz                       | Up to 56 MHz (1×1) / Up to 30.72 MHz (2×2)     |
| Power Supply                     | USB bus-powered                    | External DC power supply included              |
| GPIO                             | —                                  | Yes                                            |
| Dimensions                       | 97 × 155 × 15 mm                   | 97 × 155 × 15 mm                               |
| Weight                           | ~350 g                             | ~350 g                                         |

### General Specifications

| Parameter                        | Value                              | Notes / Conditions                             |
|----------------------------------|------------------------------------|------------------------------------------------|
| Frequency range                  | 70 MHz – 6 GHz                     |                                                |
| Frequency accuracy               | ±2.0 ppm                           |                                                |
| TCXO reference (unlocked)        | ±75 ppb                            |                                                |
| TCXO reference (GPS locked)      | < 1 ppb                            | When using optional GPSDO                      |
| USB interface                    | USB 3.0 SuperSpeed (Type-B)        | Backward compatible with USB 2.0               |
| ADC / DAC resolution             | 12-bit                             | Flexible sample rate                           |
| Max sample rate (ADC & DAC)      | 61.44 MS/s                         | Host sample rate (16-bit) also up to 61.44 MS/s|
| Wideband SFDR (ADC)              | 78 dBc                             |                                                |
| Mounting                         | Grounded mounting holes            |                                                |

### RF Performance

| Parameter                        | Value                              | Notes / Conditions                             |
|----------------------------------|------------------------------------|------------------------------------------------|
| Output power                     | > 10 dBm                           | Typical                                        |
| Receive noise figure             | < 8 dB                             | Typical                                        |
| IIP3 (at typical NF)             | -20 dBm                            | B210                                           |
| SSB / LO suppression             | —                                  | -35 / -50 dBc (typical, B210)                  |
| Integrated phase noise           | —                                  | 1.0° RMS @ 3.5 GHz, 1.5° RMS @ 6 GHz (B210)    |

### Additional Notes

- All specifications are **subject to change** without notice.
- Actual achievable sample rates depend on host USB controller, cable quality, and software configuration. Refer to Ettus/NI benchmark results for real-world performance in different modes.
- The B210's 2×2 mode has a reduced instantaneous bandwidth cap (30.72 MHz) compared to single-channel operation.

