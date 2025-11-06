# Passive RF Drone Tracking System

## Execuitive Summary
We are developing a compact, passive RF radar system that detects and tracks drones by intercepting their 5.8 GHz video downlink. It delivers real-time azimuth and elevation bearings (with optional RSSI-based range estimation) in a user-agnostic data format, enabling integration into any downstream decision workflow, whether security alerting, forensic logging, or third-party countermeasures.

## Problem Statement
Drones transmitting on the 5.8 GHz ISM band are increasingly used for legitimate and illicit purposes across diverse environments. Early, accurate detection with precise bearing is a universal requirement for situational awareness, but current solutions fail to balance cost, passivity, and actionable output.

We provide a neutral, high-fidelity RF sensor node that: 
- Detects 5.8 GHz video emissions passively
- Outputs standardized bearing + metadata
- Leaves response policy entirely to the user

Use cases are defined by the operator, including but not limited to: 
- Perimeter security
- Even protection
- Infrastructure monitoring
- Airspace awareness
- Research and telemetry

## Approach to Architecture
Antenna will be a Yagi antenna that will scan sky for drone emissions. These are some possible scanning methods:
1. Concical Scan: Common and fast locking
2. Raster Scan: full sky survey
3. Fixed stare
FPGA will process incoming analog signal after it is converted via an ADC. It will convert to baseband I/Q to then perform Fast Fourier Transform and RSSI calibration.
