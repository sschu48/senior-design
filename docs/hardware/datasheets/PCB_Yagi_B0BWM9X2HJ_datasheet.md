# Directional Yagi Antenna Dataset

## 1. Classic Metallic Boom Yagi Antenna (from Provided Diagram)

**Model / Type**  
Traditional multi-element Yagi (likely custom or simulated design, not commercially named in the image)

**Physical Dimensions** (approximate, from diagram in mm)  
- Overall length (boom): **121.4 mm**  
- Width (max element span): **~59 mm** (reflector) → tapering to narrower directors  
- Height/thickness (boom + elements): Not specified (typical ~10–20 mm for elements)  
- Element spacing/layout: Multiple parallel directors + driven + reflector, with feed point indicated  

**Radiation Characteristics** (from provided patterns)  
- **Peak Gain**: ~**12 dBi** (from 3D pattern color scale)  
- **3D Radiation Pattern**: Highly directive forward lobe (elongated oval/egg shape), minimal back lobe  
- **Azimuth Plane (H-plane)**: Narrow main beam (~ skull-like shape with -15 dB sidelobes), strong front-to-back ratio  
- **Elevation Plane (E-plane)**: Cardioid/figure-8 like with main lobe forward, narrower beam  
- **Polarization**: Likely linear/vertical (standard for Yagi unless specified)  

**Intended / Inferred Use**  
- Directional high-gain application (e.g., point-to-point links, SDR experiments, amateur radio, or research simulation)  
- Frequency not explicitly stated in diagram → infer from size/elements: likely in the **2–6 GHz range** (e.g., 2.4 GHz WiFi, 5 GHz, or similar; physical scale suggests ~2.4–5.8 GHz band)

**Notes**  
- This appears to be a simulation model (e.g., CST, HFSS) rather than a mass-produced item  
- Excellent sidelobe suppression and directivity shown in plots

## 2. Commercial Dual-Band PCB Yagi Antenna (Amazon ASIN B0BWM9X2HJ)

**Product Title / Variants** (from listings)  
ANTOSIYA (or generic) 12dBi 2.4GHz 5.8GHz Dual Band WiFi Directional PCB Yagi Antenna  
Variants: RP-SMA Male, RP-SMA Female, SMA Female

**Frequency Bands**  
- 2.4 GHz: **2400–2500 MHz**  
- 5.8 GHz: **5600–6900 MHz** (some listings narrow to 5600–5900 MHz)  

**Gain**  
- 2.4 GHz: **12 dBi**  
- 5.8 GHz: **3–4 dBi** (varies slightly by listing: 3 dBi, 4 dBi)  

**Electrical Specifications**  
- **Impedance**: 50 Ω  
- **Polarization**: Vertical or Horizontal (flexible mounting)  
- **Radiation Pattern**: Directional (forward gain focus; narrower beam on 2.4 GHz)  
- **VSWR**: Not explicitly listed (typical < 2.0 for such PCB designs)  
- **Maximum Power**: Not specified (typical 1–10 W for WiFi/FPV antennas)  

**Connector**  
- **RP-SMA Male** (most common variant)  
- Alternatives: RP-SMA Female, SMA Female  

**Physical / Mechanical**  
- **Type**: Printed Circuit Board (PCB) Yagi – flat, lightweight, etched copper elements on substrate  
- **Dimensions**: Not explicitly listed (typical for similar PCB Yagis: ~100–200 mm length × 30–60 mm width, very thin ~1–3 mm)  
- **Weight**: Very light (< 50 g estimated)  
- **Mounting**: Universal fixed mount (e.g., adhesive, bracket, or direct to device)  

**Applications / Use Cases** (from product description)  
- WiFi signal/range extension for network cards, USB adapters, routers  
- FPV drone/UAV remote control enhancement (2.4 GHz control + 5.8 GHz video)  
- Reducing interference in noisy 2.4 GHz environments  
- Long-distance directional links on compatible devices  

**Other Features**  
- High directivity on 2.4 GHz → narrower beamwidth, better interference rejection  
- Compact and portable (no large metal boom/elements)  
- Affordable (~$10–20 range)  

## Comparison Table: Diagram Model vs. Amazon PCB Yagi

| Parameter                  | Diagram (Metallic Boom Yagi)          | Amazon PCB Yagi (B0BWM9X2HJ)          |
|----------------------------|----------------------------------------|----------------------------------------|
| **Construction**           | Metal elements + boom                 | Etched PCB (flat, compact)             |
| **Size**                   | ~121 × 59 mm (boom/elements)          | Smaller/thinner (~10–20 cm est.)       |
| **Frequency**              | Inferred ~2–6 GHz (not stated)        | Explicit: 2.4 & 5.8 GHz                |
| **Peak Gain**              | ~12 dBi                               | 12 dBi (2.4 GHz) / 3–4 dBi (5.8 GHz)   |
| **Directivity / Pattern**  | Very narrow beam, excellent F/B       | Directional, wider on 5.8 GHz          |
| **Connector**              | Not shown                             | RP-SMA Male/Female or SMA Female       |
| **Typical Use**            | Simulation/research, custom links     | WiFi/FPV/drone signal boost            |
| **Form Factor**            | Larger, traditional                   | Ultra-portable, lightweight            |

