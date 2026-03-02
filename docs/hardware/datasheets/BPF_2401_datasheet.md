# Taoglas Airvu BPF.24.01 – 2.4 GHz Band Pass Filter Datasheet

**Product Overview**  
- **Part Number**: BPF.24.01  
- **Series**: Airvu  
- **Type**: Ceramic Band Pass Filter (inline coaxial)  
- **Description**: High-performance 2.4 GHz band pass filter designed to reduce out-of-band interference. Uses superior ceramic filter technology for low insertion loss and excellent stop-band rejection compared to LTCC or lumped-element alternatives.  
- **Primary Applications**:  
  - UAV / drone radio systems (placed at antenna port to mitigate interference)  
  - WiFi / ISM band devices (2.4 GHz WLAN, Bluetooth, Zigbee, etc.)  
  - Any 2.4 GHz receiver/transmitter needing improved selectivity and noise reduction  
- **Key Advantages**:  
  - Low insertion loss in passband  
  - High out-of-band attenuation  
  - Rugged inline design with standard connectors  
  - Helps eliminate/reduce radio interference problems  

### Electrical Specifications

| Parameter                  | Value / Specification                  | Notes / Conditions                             |
|----------------------------|----------------------------------------|------------------------------------------------|
| Center Frequency           | 2.45 GHz                               |                                                |
| Passband Frequency Range   | 2400 – 2500 MHz                        | Full 2.4 GHz ISM band                          |
| Bandwidth                  | 100 MHz                                |                                                |
| Insertion Loss             | ≤ 1.3 dB (typical/max in passband)     | Low loss for minimal signal degradation        |
| Impedance                  | 50 Ω                                   |                                                |
| VSWR                       | Not explicitly listed (typically < 1.5:1 inferred from low IL) | Good match expected                            |
| Maximum Input Power        | 10 W (10,000 mW)                       |                                                |
| Filter Type                | Band Pass                              | Ceramic construction                           |
| Out-of-Band Rejection      | High (superior stop-band performance)  | Exact attenuation curves in datasheet (strong rejection outside 2400–2500 MHz) |

### Mechanical & Connector Specifications

| Parameter                  | Value                                      | Notes                                      |
|----------------------------|--------------------------------------------|--------------------------------------------|
| Dimensions                 | 35 mm × 10 mm × 10 mm (L × W × H)          | Compact cylindrical/inline form            |
| Weight                     | Not specified (very light, < 20 g est.)    |                                            |
| Connector Type             | RP-SMA Plug (Male) to RP-SMA Jack (Female) | Standard for 2.4 GHz antennas/radios       |
| Mounting / Form Factor     | Inline coaxial (no mounting required)      | Placed directly on antenna port or cable   |
| Housing / Construction     | Ceramic filter element in rugged enclosure | Durable for UAV/field use                  |

### Environmental Specifications

| Parameter                  | Value                                      | Notes                                      |
|----------------------------|--------------------------------------------|--------------------------------------------|
| Operating Temperature      | Not explicitly listed (typical -40°C to +85°C for Taoglas RF components) | Refer to full datasheet for exact range    |
| Storage Temperature        | Not specified                              | Standard industrial range expected         |
| RoHS Compliance            | Yes                                        |                                            |

### Additional Notes

- **Performance Context**: Ideal for UAV/drone applications where external interference (e.g., from other 2.4 GHz sources) can degrade link quality. The filter cleans the signal at the antenna input/output.  
- **Integration**: Connects inline between the antenna (with RP-SMA Male) and the radio/transceiver (RP-SMA Female port).  
- **Variants**: Part of the Airvu series; companion 5.8 GHz model is BPF.58.01 (5150–5900 MHz, 370 MHz BW, <2.1 dB IL).  
- **All specifications subject to change**; exact performance curves (S-parameters, insertion loss vs. frequency, return loss, etc.) are detailed in the official Taoglas datasheet PDF.  
- **Not an antenna** — this is a passive RF bandpass filter component for signal conditioning.
