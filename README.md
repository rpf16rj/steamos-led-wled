# SteamOS LED → WLED Bridge

Mirrors the Steam Deck's LED bar to a [WLED](https://github.com/Aircoookie/WLED) controller via UDP realtime protocol (DRGB).

No custom firmware needed — just a stock WLED device on your network.

**[Leia em Português (PT-BR)](README.pt-br.md)**

## How it works

1. Reads 100-byte LED snapshots from `/dev/valve-leds-shim` (kernel module)
2. Remaps 17 source LEDs to your strip length
3. Renders Steam Deck effects server-side (rainbow, breath, patrol, factory, demo)
4. Applies optional overlays (temperature, notifications, audio VU)
5. Sends pixel data via **UDP DRGB** (port 21324) to WLED — same protocol HyperHDR uses
6. Plays boot/shutdown/suspend/resume animations

## Requirements

- **Steam Deck** (SteamOS) or Linux PC running Steam in Game Mode
- **WLED device** on the same network (ESP8266, ESP32, etc.)
- **Python 3** (no extra packages needed — uses only stdlib)

### Setting up WLED (prerequisite)

You need a WLED controller on your network before installing this bridge. Quick steps:

1. **Flash WLED** to an ESP8266 or ESP32 board:
   - Download the latest release from [WLED releases](https://github.com/Aircoookie/WLED/releases)
   - Use the [WLED web installer](https://install.wled.me/) (Chrome/Edge) for easiest setup
2. **Connect to WiFi**: After flashing, connect to the `WLED-AP` hotspot and configure your WiFi credentials
3. **Configure LED strip**: In WLED settings → LED Preferences, set the correct LED count and type (WS2812B, SK6812, etc.)
4. **Note the IP address**: WLED shows its IP on the main page. The installer can also auto-discover it
5. **Enable UDP sync**: In WLED settings → Sync Interfaces, ensure the UDP port is **21324** (default)

For detailed instructions, see the [WLED documentation](https://kno.wled.ge/).

## Installation

```bash
git clone https://github.com/rpf16rj/steamos-led-wled.git
cd steamos-led-wled
sudo ./install.sh
```

The installer will:
1. Try to **auto-discover** WLED devices on your network (mDNS / subnet scan)
2. Fall back to asking for the IP address manually
3. Ask for LED count and overlay preferences
4. Build and install the `leds-valve-shim` kernel module
5. Install the bridge service and generate `/etc/steamos-led-wled.conf`
6. Enable and start the systemd service

## Decky Loader Plugin

Control overlays directly from Game Mode using the **Toolkit SteamOS Control** Decky plugin:

- Repository: [toolkit-steamos-control-decky](https://github.com/rpf16rj/toolkit-steamos-control-decky)
- Toggle temperature, audio VU, and notification overlays without leaving Game Mode

## Configuration

Edit `/etc/steamos-led-wled.conf`:

```ini
[steamos-led-wled]
# WLED device IP address
wled_host = 192.168.1.100

# WLED UDP realtime port (default: 21324)
wled_port = 21324

# Number of LEDs on your strip (1-17)
num_leds = 8

# Path to the valve-leds-shim device
device = /dev/valve-leds-shim

# Overlay features (true/false)
temp_overlay = true
notify_overlay = true
audio_overlay = true
```

After editing, restart the service:

```bash
sudo systemctl restart steamos-led-wled
```

## Features

### LED Effects (server-side rendering)

All Steam Deck LED effects are rendered by the bridge and sent to WLED:

| Effect | Description |
|--------|-------------|
| **Manual** | Static color set by Game Mode |
| **Rainbow** | Cycling hues across all LEDs |
| **Breath** | Pulsing brightness |
| **Patrol** | Ping-pong bouncing light |
| **Factory** | Alternating complementary colors |
| **Demo** | Cycles through all effects |

### Overlay features

| Overlay | Description |
|---------|-------------|
| **Temperature** | Colors the bar yellow→red based on CPU/GPU temp (> 65°C) |
| **Notifications** | Flashes gold for achievements, blue for messages |
| **Audio VU** | VU meter driven by system audio (PipeWire/PulseAudio) |

Priority: Notification > Audio+Temperature > Game Mode

### Animations

| Event | Animation |
|-------|-----------|
| **Boot** | Center-out sweep in Steam blue, then pulse |
| **Shutdown** | Edge-to-center fade out |
| **Suspend** | Slow fade to black |
| **Resume** | Quick center-out sweep |

## Commands

```bash
# Check status
sudo systemctl status steamos-led-wled

# View logs
sudo journalctl -u steamos-led-wled -f

# Restart
sudo systemctl restart steamos-led-wled

# Stop
sudo systemctl stop steamos-led-wled
```

## Uninstall

```bash
sudo ./uninstall.sh
```

## UDP Protocol

Uses WLED's **DRGB** realtime protocol on port 21324:

| Byte | Value | Description |
|------|-------|-------------|
| 0 | `0x02` | Protocol: DRGB |
| 1 | `0x02` | Timeout: 2 seconds |
| 2+n×3 | R | Red value for LED n |
| 3+n×3 | G | Green value for LED n |
| 4+n×3 | B | Blue value for LED n |

This is the same protocol used by HyperHDR, Hyperion, and other ambilight solutions.

## License

MIT
