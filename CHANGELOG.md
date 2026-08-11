# Changelog

All notable changes to this project will be documented in this file.

## [1.0.2] - 2026-08-11

### Fixed

- WLED boot preset config and non-blocking power on

## [1.0.1] - 2026-08-11

### Added

- WLED power on/off via HTTP on boot and shutdown
- auto-configure WLED boot rainbow preset during install

### Fixed

- install steam_effects.py alongside led_server.py

## [1.0.0] - 2026-08-09

### Added

- Initial release
- WLED UDP realtime bridge using DRGB protocol (port 21324)
- Reads LED snapshots from `/dev/valve-leds-shim` (valve-leds-shim kernel module)
- Remaps 17 source LEDs to configurable strip length (1-17)
- Server-side rendering of Steam Deck LED effects:
  - Manual (static color)
  - Rainbow (cycling hues per LED)
  - Breath (pulsing brightness)
  - Patrol (ping-pong bouncing light)
  - Factory (alternating complementary colors)
  - Demo (cycles through all effects)
- Overlay features:
  - Temperature: yellow→red color bar based on CPU/GPU temp
  - Notifications: flash on Steam achievements/messages via DBus
  - Audio reactive: VU meter driven by PipeWire/PulseAudio
- Animations:
  - Boot: center-out sweep in Steam blue
  - Shutdown: edge-to-center fade out
  - Suspend: slow fade to black
  - Resume: quick center-out sweep
- Auto-discovery of WLED devices (mDNS + subnet scan)
- Configuration via `/etc/steamos-led-wled.conf`
- Interactive installer with dependency management (SteamOS, Arch, Debian, Fedora)
- Systemd service with auto-restart
- Uninstaller script
- Decky Loader plugin integration (Toolkit SteamOS Control)
