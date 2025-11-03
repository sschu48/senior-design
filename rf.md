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

Note: Learn more about the modulation techniques and why each emission could be useful

## 
