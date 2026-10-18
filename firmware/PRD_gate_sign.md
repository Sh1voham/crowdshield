# PRD: CrowdShield advisory gate sign (ESP32, built with FirmGen)

**Purpose:** an ESP32 shows CrowdShield's advice at the gate. It is an advisory display for stewards and the public. It controls nothing.

## Requirements

| ID | Requirement | Acceptance test |
|---|---|---|
| R1 | Connect to Wi-Fi using credentials from `secrets.h`, which is not committed. Retry every 5 s until connected. | Power on near the access point: UART logs `wifi: connected <ip>` within 15 s. |
| R2 | Connect to MQTT `<laptop-ip>:1883` (a configurable host) and subscribe to `crowdshield/actuator/cmd` with QoS 1. The topic is retained, so the current advice arrives on connect. | `mosquitto_pub -h <laptop> -t crowdshield/actuator/cmd -r -q 1 -m '{"v":1,"seq":1,"action":"NONE","level":"GREEN","text":"ENTRY OPEN","advisory":true}'`; the sign shows it within 1 s of connecting. |
| R3 | Show `NONE` as green "ENTRY OPEN", `SLOW_ENTRY` as amber "SLOW ENTRY", and `HOLD_ENTRY` as red "HOLD ENTRY" plus a buzzer pattern (3 × 200 ms on, 200 ms off, repeated every 10 s). Map these to the kit's LEDs, matrix or display. | Publish each action; the sign and buzzer behave as specified. |
| R4 | Within 500 ms of each command, publish `crowdshield/actuator/state` `{"v":1,"device":"esp32-sign-1","ack_seq":<seq>,"shown":"<action>","rssi":<dBm>,"uptime_s":<s>}` with QoS 1. | `tools/actuator_test.py` reports every acknowledgement and its delay. |
| R5 | If no command arrives for 15 s (CrowdShield repeats it every 5 s), show a distinct SYSTEM OFFLINE pattern (white blinking at 1 Hz) and stop the buzzer. This makes a failure visible. | Stop the engine: OFFLINE appears after 15 s. Restart it: normal display returns within 5 s. |
| R6 | Log one UART line per received command: `cmd seq=<n> action=<a> latency_ms=<ms since receipt>`. | Read the serial monitor during `tools/actuator_test.py`. |
| R7 | Ignore malformed JSON, unknown actions and `"advisory": false` messages, and log them. | `mosquitto_pub ... -m 'not json'`: the sign keeps its state and logs `cmd rejected`. |

## Network

The laptop runs the broker inside WSL2, which is behind NAT. Either:
- forward port 1883 from Windows (`netsh interface portproxy add v4tov4 listenport=1883 connectport=1883 connectaddress=<WSL IP>`), or
- use WSL mirrored networking.

The broker must listen on 0.0.0.0 (`tools/mosquitto_crowdshield.conf`). If the venue Wi-Fi blocks device-to-device traffic, put both the laptop and the ESP32 on a phone hotspot.

## Testing without hardware

`tools/esp32_sim.py` is a SIMULATED sign that implements R2–R7 in software. It acknowledges with `"simulated": true`, so the dashboard labels it.
- Run it with `python tools/esp32_sim.py`, or `CS_SIGN_SIM=1 scripts/demo.sh ...`.
- `tools/actuator_test.py` passes against it: every acknowledgement arrives in under 50 ms on the local broker.

It tests the loop and the dashboard, not FirmGen firmware, and does not count for the Closed Loop bonus.

## Build

Generate the firmware with FirmGen from this PRD, and flash it through FirmGen; the Closed Loop bonus requires that.
