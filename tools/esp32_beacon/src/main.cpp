/*
 * SENTINEL — ESP32 Test Beacon
 *
 * Controllable 2.4GHz Wi-Fi beacon transmitter for bench-testing the
 * SENTINEL detection pipeline.  Generates known RF signatures that the
 * SDR receiver can validate against.
 *
 * Test profiles:
 *   CONTINUOUS  — fixed channel, beacon every 100ms (baseline detection)
 *   FLOOD       — blast broadcast packets nonstop (~90% duty cycle, simulates drone video)
 *   BURST       — on/off duty cycle (tests tripwire duration gating)
 *   POWER_RAMP  — sweep TX power 2–20 dBm (tests SNR sensitivity)
 *   FHSS        — hop through channels (tests multi-freq detection)
 *
 * Serial commands (115200 baud):
 *   PROFILE <CONTINUOUS|FLOOD|BURST|POWER_RAMP|FHSS>
 *   CHANNEL <1-14>
 *   POWER <2-20>    (dBm)
 *   START
 *   STOP
 *   STATUS
 *
 * LED indicator (built-in LED, usually GPIO 2):
 *   ON    = transmitting
 *   OFF   = stopped
 *   BLINK = burst off-phase
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_wifi.h>

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

static const int LED_PIN = 2;  // Built-in LED on most ESP32 dev boards

// WiFi channel → frequency is handled by the ESP32 Wi-Fi stack.
// We just need to set the channel number (1–14).

// Power limits: esp_wifi_set_max_tx_power() takes int8_t in 0.25 dBm units.
// Range: 8 (2 dBm) to 80 (20 dBm) on most ESP32 modules.
static const int8_t MIN_POWER_QUARTER_DBM = 8;   // 2 dBm
static const int8_t MAX_POWER_QUARTER_DBM = 80;   // 20 dBm

// Serial command buffer
static const int CMD_BUF_SIZE = 64;

// ---------------------------------------------------------------------------
// Profile definitions
// ---------------------------------------------------------------------------

enum Profile {
    PROFILE_CONTINUOUS,
    PROFILE_FLOOD,
    PROFILE_BURST,
    PROFILE_POWER_RAMP,
    PROFILE_FHSS,
    PROFILE_COUNT,
};

static const char* profile_names[] = {
    "CONTINUOUS", "FLOOD", "BURST", "POWER_RAMP", "FHSS"
};

// SSID prefix tags per profile (for easy identification in spectrum)
static const char* profile_ssid_tags[] = {
    "CONT", "FLOOD", "BURST", "RAMP", "FHSS"
};

// FLOOD profile: broadcast packet payload (250 bytes of data per packet)
static const int FLOOD_PACKET_SIZE = 250;

// BURST profile timing (ms)
static const unsigned long BURST_ON_MS  = 500;
static const unsigned long BURST_OFF_MS = 500;

// POWER_RAMP profile
static const int RAMP_STEP_QUARTER_DBM = 8;   // 2 dBm steps
static const unsigned long RAMP_DWELL_MS = 2000;

// FHSS profile
static const int FHSS_CHANNELS[] = {1, 6, 11, 3, 9, 13};
static const int FHSS_NUM_CHANNELS = sizeof(FHSS_CHANNELS) / sizeof(FHSS_CHANNELS[0]);
static const unsigned long FHSS_DWELL_MS = 1000;

// Beacon interval
static const unsigned long BEACON_INTERVAL_MS = 100;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

static Profile current_profile = PROFILE_CONTINUOUS;
static int current_channel = 6;
static int8_t current_power_quarter_dbm = 48;  // 12 dBm default
static bool running = false;

// Timing
static unsigned long last_beacon_ms = 0;
static unsigned long phase_start_ms = 0;
static bool burst_on_phase = true;

// POWER_RAMP state
static int8_t ramp_current_power = MIN_POWER_QUARTER_DBM;

// FHSS state
static int fhss_channel_idx = 0;

// Serial command buffer
static char cmd_buf[CMD_BUF_SIZE];
static int cmd_pos = 0;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void set_tx_power(int8_t quarter_dbm) {
    quarter_dbm = constrain(quarter_dbm, MIN_POWER_QUARTER_DBM, MAX_POWER_QUARTER_DBM);
    esp_wifi_set_max_tx_power(quarter_dbm);
    current_power_quarter_dbm = quarter_dbm;
}

static void set_channel(int ch) {
    ch = constrain(ch, 1, 14);
    esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
    current_channel = ch;
}

static void build_ssid(char* ssid, int max_len) {
    snprintf(ssid, max_len, "SENTINEL_%s_CH%d",
             profile_ssid_tags[current_profile], current_channel);
}

static void start_ap() {
    char ssid[32];
    build_ssid(ssid, sizeof(ssid));

    WiFi.mode(WIFI_AP);
    WiFi.softAP(ssid, nullptr, current_channel, 0, 0);
    set_tx_power(current_power_quarter_dbm);

    Serial.printf("[BEACON] AP started: SSID=%s CH=%d PWR=%.1f dBm\n",
                  ssid, current_channel, current_power_quarter_dbm / 4.0f);
}

static void stop_ap() {
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_OFF);
}

static void restart_ap() {
    stop_ap();
    delay(50);
    start_ap();
}

// ---------------------------------------------------------------------------
// Profile handlers
// ---------------------------------------------------------------------------

static void handle_continuous() {
    // AP runs continuously — nothing to do per-tick.
    // The Wi-Fi stack sends beacons automatically.
    digitalWrite(LED_PIN, HIGH);
}

static void handle_flood() {
    // Blast broadcast UDP packets as fast as possible.
    // This keeps the WiFi radio transmitting near-continuously (~90% duty cycle),
    // simulating a high-throughput drone video downlink.
    static WiFiUDP udp;
    static bool udp_started = false;
    static uint8_t payload[FLOOD_PACKET_SIZE];
    static uint32_t seq = 0;

    if (!udp_started) {
        udp.begin(12345);
        // Fill payload with recognizable pattern
        for (int i = 0; i < FLOOD_PACKET_SIZE; i++) {
            payload[i] = (uint8_t)(i & 0xFF);
        }
        udp_started = true;
    }

    digitalWrite(LED_PIN, HIGH);

    // Send a burst of broadcast packets per loop iteration
    // Broadcast to 255.255.255.255 so no association needed
    for (int i = 0; i < 20; i++) {
        // Stamp sequence number into first 4 bytes
        payload[0] = (seq >> 24) & 0xFF;
        payload[1] = (seq >> 16) & 0xFF;
        payload[2] = (seq >> 8) & 0xFF;
        payload[3] = seq & 0xFF;

        udp.beginPacket(IPAddress(255, 255, 255, 255), 12345);
        udp.write(payload, FLOOD_PACKET_SIZE);
        udp.endPacket();
        seq++;
    }
}

static void handle_burst() {
    unsigned long now = millis();
    unsigned long elapsed = now - phase_start_ms;

    if (burst_on_phase) {
        digitalWrite(LED_PIN, HIGH);
        if (elapsed >= BURST_ON_MS) {
            // Transition to off phase
            stop_ap();
            burst_on_phase = false;
            phase_start_ms = now;
        }
    } else {
        // Blink LED during off phase
        digitalWrite(LED_PIN, (millis() / 100) % 2);
        if (elapsed >= BURST_OFF_MS) {
            // Transition to on phase
            start_ap();
            burst_on_phase = true;
            phase_start_ms = now;
        }
    }
}

static void handle_power_ramp() {
    unsigned long now = millis();
    unsigned long elapsed = now - phase_start_ms;

    digitalWrite(LED_PIN, HIGH);

    if (elapsed >= RAMP_DWELL_MS) {
        ramp_current_power += RAMP_STEP_QUARTER_DBM;
        if (ramp_current_power > MAX_POWER_QUARTER_DBM) {
            ramp_current_power = MIN_POWER_QUARTER_DBM;
        }
        set_tx_power(ramp_current_power);
        phase_start_ms = now;

        Serial.printf("[RAMP] Power: %.1f dBm\n", ramp_current_power / 4.0f);
    }
}

static void handle_fhss() {
    unsigned long now = millis();
    unsigned long elapsed = now - phase_start_ms;

    digitalWrite(LED_PIN, HIGH);

    if (elapsed >= FHSS_DWELL_MS) {
        fhss_channel_idx = (fhss_channel_idx + 1) % FHSS_NUM_CHANNELS;
        int new_ch = FHSS_CHANNELS[fhss_channel_idx];
        set_channel(new_ch);
        current_channel = new_ch;

        // Update SSID to reflect new channel
        restart_ap();
        phase_start_ms = now;

        Serial.printf("[FHSS] Hop → CH%d\n", new_ch);
    }
}

// ---------------------------------------------------------------------------
// Serial command parser
// ---------------------------------------------------------------------------

static void process_command(const char* cmd) {
    // Trim leading whitespace
    while (*cmd == ' ') cmd++;
    if (*cmd == '\0') return;

    if (strncasecmp(cmd, "PROFILE ", 8) == 0) {
        const char* name = cmd + 8;
        while (*name == ' ') name++;

        bool found = false;
        for (int i = 0; i < PROFILE_COUNT; i++) {
            if (strcasecmp(name, profile_names[i]) == 0) {
                current_profile = (Profile)i;
                found = true;
                Serial.printf("[OK] Profile: %s\n", profile_names[i]);
                if (running) {
                    // Reset state for new profile
                    phase_start_ms = millis();
                    burst_on_phase = true;
                    ramp_current_power = MIN_POWER_QUARTER_DBM;
                    fhss_channel_idx = 0;
                    restart_ap();
                }
                break;
            }
        }
        if (!found) {
            Serial.printf("[ERR] Unknown profile: %s\n", name);
        }

    } else if (strncasecmp(cmd, "CHANNEL ", 8) == 0) {
        int ch = atoi(cmd + 8);
        if (ch >= 1 && ch <= 14) {
            current_channel = ch;
            Serial.printf("[OK] Channel: %d\n", ch);
            if (running) restart_ap();
        } else {
            Serial.printf("[ERR] Invalid channel (1-14): %d\n", ch);
        }

    } else if (strncasecmp(cmd, "POWER ", 6) == 0) {
        float dbm = atof(cmd + 6);
        int8_t quarter = (int8_t)(dbm * 4.0f);
        quarter = constrain(quarter, MIN_POWER_QUARTER_DBM, MAX_POWER_QUARTER_DBM);
        current_power_quarter_dbm = quarter;
        Serial.printf("[OK] Power: %.1f dBm\n", quarter / 4.0f);
        if (running) set_tx_power(quarter);

    } else if (strcasecmp(cmd, "START") == 0) {
        if (!running) {
            running = true;
            phase_start_ms = millis();
            burst_on_phase = true;
            ramp_current_power = current_power_quarter_dbm;
            fhss_channel_idx = 0;
            start_ap();
            Serial.println("[OK] Started");
        } else {
            Serial.println("[OK] Already running");
        }

    } else if (strcasecmp(cmd, "STOP") == 0) {
        if (running) {
            running = false;
            stop_ap();
            digitalWrite(LED_PIN, LOW);
            Serial.println("[OK] Stopped");
        } else {
            Serial.println("[OK] Already stopped");
        }

    } else if (strcasecmp(cmd, "STATUS") == 0) {
        Serial.printf("[STATUS] profile=%s channel=%d power=%.1f_dBm running=%s\n",
                      profile_names[current_profile],
                      current_channel,
                      current_power_quarter_dbm / 4.0f,
                      running ? "yes" : "no");

    } else {
        Serial.printf("[ERR] Unknown command: %s\n", cmd);
        Serial.println("  Commands: PROFILE <name>, CHANNEL <n>, POWER <dBm>, START, STOP, STATUS");
    }
}

static void poll_serial() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (cmd_pos > 0) {
                cmd_buf[cmd_pos] = '\0';
                process_command(cmd_buf);
                cmd_pos = 0;
            }
        } else if (cmd_pos < CMD_BUF_SIZE - 1) {
            cmd_buf[cmd_pos++] = c;
        }
    }
}

// ---------------------------------------------------------------------------
// Arduino entry points
// ---------------------------------------------------------------------------

void setup() {
    Serial.begin(115200);
    delay(500);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    Serial.println();
    Serial.println("========================================");
    Serial.println("  SENTINEL — ESP32 Test Beacon v1.0");
    Serial.println("========================================");
    Serial.printf("  Default: CH%d, %.1f dBm, %s\n",
                  current_channel,
                  current_power_quarter_dbm / 4.0f,
                  profile_names[current_profile]);
    Serial.println("  Commands: PROFILE, CHANNEL, POWER, START, STOP, STATUS");
    Serial.println("  Type START to begin transmitting.");
    Serial.println("========================================");
}

void loop() {
    poll_serial();

    if (!running) {
        delay(10);
        return;
    }

    switch (current_profile) {
        case PROFILE_CONTINUOUS:
            handle_continuous();
            break;
        case PROFILE_FLOOD:
            handle_flood();
            break;
        case PROFILE_BURST:
            handle_burst();
            break;
        case PROFILE_POWER_RAMP:
            handle_power_ramp();
            break;
        case PROFILE_FHSS:
            handle_fhss();
            break;
    }

    delay(10);
}
