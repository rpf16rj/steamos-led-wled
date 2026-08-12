#!/usr/bin/env python3
"""SteamOS LED → WLED bridge.

Reads 100-byte snapshots from /dev/valve-leds-shim and forwards pixel data
to a WLED controller via the UDP realtime protocol (DRGB, port 21324).

Supports overlay layers:
  - Temperature: full-bar color based on CPU/GPU temp
  - Notifications: flash on Steam achievements/messages via DBus
  - Audio reactive: VU meter driven by PipeWire/PulseAudio
"""

import configparser
import glob
import math
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

# ══════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════

SNAPSHOT_SIZE = 100
SRC_NUM_LEDS = 17
PIXELS_OFFSET = 32
PIXEL_SIZE = 4  # R, G, B, brightness
DEFAULT_DEVICE = "/dev/valve-leds-shim"
DEFAULT_CONF = "/etc/steamos-led-wled.conf"

# WLED UDP realtime protocol
WLED_UDP_PORT = 21324
WLED_DRGB_MODE = 2      # DRGB: sequential RGB for every LED
WLED_TIMEOUT_SEC = 2     # seconds before WLED reverts to normal mode

POLL_INTERVAL = 0.033    # ~30fps
DEVICE_REOPEN_INTERVAL = 2.0  # reopen device every 2s (not every frame)

# ══════════════════════════════════════════════════════════════════
# Global state
# ══════════════════════════════════════════════════════════════════

running = True
latest = None
num_output_leds = 8

# Overlay state
overlay_lock = threading.Lock()

# Temperature overlay
temp_overlay_enabled = False
temp_color = None
temp_blink = False
temp_blink_state = True
temp_last_read = 0.0
temp_current = 0.0
TEMP_READ_INTERVAL = 2.0
TEMP_THRESHOLD_WARM = 65
TEMP_THRESHOLD_HOT = 80

# Notification overlay
notif_overlay_enabled = False
notif_active = False
notif_color = (255, 215, 0)
notif_end_time = 0.0
NOTIF_DURATION = 3.5

# Audio overlay (VU meter)
audio_overlay_enabled = False
audio_level = 0.0
audio_peak = 0.0
audio_process = None

# Transition state
prev_overlay_frame = None
current_mode = "gamemode"
last_mode_change = 0.0


# ══════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════

def load_config(path):
    """Load configuration from .conf file, returning a dict of settings."""
    defaults = {
        "wled_host": "",
        "wled_port": str(WLED_UDP_PORT),
        "num_leds": "8",
        "device": DEFAULT_DEVICE,
        "temp_overlay": "true",
        "notify_overlay": "true",
        "audio_overlay": "true",
        "wled_power_control": "true",
    }
    cfg = configparser.ConfigParser()
    cfg.read_dict({"steamos-led-wled": defaults})
    if os.path.isfile(path):
        cfg.read(path)
    section = "steamos-led-wled"
    return {
        "wled_host": cfg.get(section, "wled_host"),
        "wled_port": cfg.getint(section, "wled_port"),
        "num_leds": cfg.getint(section, "num_leds"),
        "device": cfg.get(section, "device"),
        "temp_overlay": cfg.getboolean(section, "temp_overlay"),
        "notify_overlay": cfg.getboolean(section, "notify_overlay"),
        "audio_overlay": cfg.getboolean(section, "audio_overlay"),
        "wled_power_control": cfg.getboolean(section, "wled_power_control"),
    }


# ══════════════════════════════════════════════════════════════════
# WLED UDP sender
# ══════════════════════════════════════════════════════════════════

class WLEDUdpSender:
    """Send pixel data to WLED via UDP DRGB protocol."""

    def __init__(self, host, port=WLED_UDP_PORT):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)

    def send_pixels(self, pixels):
        """Send a list of (r, g, b) tuples as a DRGB packet.

        DRGB format:
          byte 0: protocol (2 = DRGB)
          byte 1: timeout in seconds
          byte 2+n*3: R value for LED n
          byte 3+n*3: G value for LED n
          byte 4+n*3: B value for LED n
        """
        packet = bytearray(2 + len(pixels) * 3)
        packet[0] = WLED_DRGB_MODE
        packet[1] = WLED_TIMEOUT_SEC
        for i, (r, g, b) in enumerate(pixels):
            off = 2 + i * 3
            packet[off + 0] = r & 0xFF
            packet[off + 1] = g & 0xFF
            packet[off + 2] = b & 0xFF
        try:
            self.sock.sendto(packet, (self.host, self.port))
        except OSError as e:
            print(f"WLED UDP send error: {e}", file=sys.stderr)

    def close(self):
        self.sock.close()


# ══════════════════════════════════════════════════════════════════
# WLED HTTP control (power on/off)
# ══════════════════════════════════════════════════════════════════

class WLEDHttpControl:
    """Control WLED power state via HTTP API."""

    def __init__(self, host):
        self.base_url = f"http://{host}"

    def power_on(self):
        def _do_power_on():
            try:
                urllib.request.urlopen(
                    f"{self.base_url}/win&T=1", timeout=2
                ).read()
                print("WLED: powered on", file=sys.stderr)
            except Exception as e:
                print(f"WLED power on failed: {e}", file=sys.stderr)
        threading.Thread(target=_do_power_on, daemon=True).start()

    def power_off(self):
        try:
            urllib.request.urlopen(
                f"{self.base_url}/win&T=0", timeout=2
            ).read()
            print("WLED: powered off", file=sys.stderr)
        except Exception as e:
            print(f"WLED power off failed: {e}", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════
# Snapshot processing
# ══════════════════════════════════════════════════════════════════

def read_snapshot(led_fd):
    try:
        data = os.read(led_fd, SNAPSHOT_SIZE)
    except OSError:
        return None
    if len(data) != SNAPSHOT_SIZE:
        return None
    return data


def remap_snapshot(data, out_leds):
    """Remap 17 source LEDs to out_leds by averaging pixel blocks."""
    if out_leds >= SRC_NUM_LEDS:
        return data

    header = bytearray(data[:PIXELS_OFFSET])
    pixels_out = bytearray(out_leds * PIXEL_SIZE)

    for i in range(out_leds):
        start = i * SRC_NUM_LEDS / out_leds
        end = (i + 1) * SRC_NUM_LEDS / out_leds

        r_sum, g_sum, b_sum, br_sum = 0.0, 0.0, 0.0, 0.0
        weight_total = 0.0

        j = int(start)
        while j < end and j < SRC_NUM_LEDS:
            lo = max(start, j)
            hi = min(end, j + 1)
            w = hi - lo

            off = PIXELS_OFFSET + j * PIXEL_SIZE
            r_sum += data[off + 0] * w
            g_sum += data[off + 1] * w
            b_sum += data[off + 2] * w
            br_sum += data[off + 3] * w
            weight_total += w
            j += 1

        if weight_total > 0:
            pixels_out[i * PIXEL_SIZE + 0] = int(r_sum / weight_total + 0.5)
            pixels_out[i * PIXEL_SIZE + 1] = int(g_sum / weight_total + 0.5)
            pixels_out[i * PIXEL_SIZE + 2] = int(b_sum / weight_total + 0.5)
            pixels_out[i * PIXEL_SIZE + 3] = int(br_sum / weight_total + 0.5)

    padding = bytearray((SRC_NUM_LEDS - out_leds) * PIXEL_SIZE)
    return bytes(header + pixels_out + padding)


def extract_pixels(snapshot, num_leds):
    """Extract (r, g, b) tuples from snapshot, applying per-LED brightness."""
    pixels = []
    brightness_scale = snapshot[26] / 255.0 if len(snapshot) > 26 else 1.0
    for i in range(num_leds):
        off = PIXELS_OFFSET + i * PIXEL_SIZE
        r = snapshot[off + 0]
        g = snapshot[off + 1]
        b = snapshot[off + 2]
        br = snapshot[off + 3] / 255.0
        factor = br * brightness_scale
        pixels.append((
            int(r * factor),
            int(g * factor),
            int(b * factor),
        ))
    return pixels


# ══════════════════════════════════════════════════════════════════
# Temperature overlay
# ══════════════════════════════════════════════════════════════════

def get_max_temperature():
    """Read max temperature from thermal zones and GPU."""
    temps = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(path) as f:
                temps.append(int(f.read().strip()) / 1000.0)
        except (OSError, ValueError):
            pass
    for path in glob.glob("/sys/class/drm/card*/device/hwmon/hwmon*/temp1_input"):
        try:
            with open(path) as f:
                temps.append(int(f.read().strip()) / 1000.0)
        except (OSError, ValueError):
            pass
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=2, stderr=subprocess.DEVNULL
        )
        for line in out.decode().strip().split('\n'):
            temps.append(float(line))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return max(temps) if temps else None


def temp_to_color(temp):
    if temp < TEMP_THRESHOLD_WARM:
        return None, False
    elif temp >= TEMP_THRESHOLD_HOT:
        return (255, 0, 0), True
    else:
        t = (temp - TEMP_THRESHOLD_WARM) / (TEMP_THRESHOLD_HOT - TEMP_THRESHOLD_WARM)
        r = 255
        g = int(255 * (1 - t))
        return (r, g, 0), False


def update_temperature():
    global temp_color, temp_blink, temp_last_read, temp_current
    now = time.time()
    if now - temp_last_read < TEMP_READ_INTERVAL:
        return
    temp_last_read = now
    temp = get_max_temperature()
    if temp is None:
        temp_color = None
        temp_current = 0.0
        return
    temp_current = temp
    temp_color, temp_blink = temp_to_color(temp)


# ══════════════════════════════════════════════════════════════════
# Notification overlay (DBus)
# ══════════════════════════════════════════════════════════════════

def start_notification_listener():
    global notif_active, notif_end_time, notif_color

    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError:
        print("dbus-python not available, notification overlay disabled", file=sys.stderr)
        return

    def on_notification(bus_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout):
        global notif_active, notif_end_time, notif_color
        with overlay_lock:
            summary_lower = summary.lower() if summary else ""
            if "achievement" in summary_lower or "conquista" in summary_lower:
                notif_color = (255, 215, 0)
            else:
                notif_color = (0, 120, 255)
            notif_active = True
            notif_end_time = time.time() + NOTIF_DURATION

    def dbus_thread():
        DBusGMainLoop(set_as_default=True)
        waiting_logged = False
        while running:
            try:
                bus = dbus.SessionBus()
                bus.add_signal_receiver(
                    on_notification,
                    dbus_interface="org.freedesktop.Notifications",
                    signal_name="Notify",
                    bus_name="org.freedesktop.Notifications",
                    path="/org/freedesktop/Notifications"
                )
                print("Notifications: connected to session DBus", file=sys.stderr)
                loop = GLib.MainLoop()
                while running:
                    loop.get_context().iteration(True)
                return
            except Exception:
                if not waiting_logged:
                    print("Notifications: waiting for user session DBus", file=sys.stderr)
                    waiting_logged = True
                time.sleep(5)

    t = threading.Thread(target=dbus_thread, daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════════
# Audio reactive overlay
# ══════════════════════════════════════════════════════════════════

def start_audio_monitor():
    global audio_process

    def find_default_sink_id():
        try:
            out = subprocess.check_output(
                ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            for line in out.split('\n'):
                if 'id' in line and 'object.id' not in line:
                    parts = line.strip().split()
                    for i, p in enumerate(parts):
                        if p == 'id':
                            return parts[i + 1].rstrip(',')
            out2 = subprocess.check_output(
                ["wpctl", "status"], timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            for line in out2.split('\n'):
                if '*' in line and 'vol:' in line:
                    parts = line.strip().lstrip('*').strip().split('.')
                    return parts[0].strip()
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            pass
        return None

    def audio_thread():
        global audio_level, audio_peak, audio_process
        rate = 48000
        samples_per_update = rate // 50
        chunk_bytes = samples_per_update * 2
        waiting_logged = False

        while running:
            sink_id = find_default_sink_id()
            if not sink_id:
                if not waiting_logged:
                    print("Audio: waiting for PipeWire default sink", file=sys.stderr)
                    waiting_logged = True
                time.sleep(5)
                continue

            try:
                audio_process = subprocess.Popen(
                    ["pw-record", "--target", sink_id,
                     "-P", "{ stream.capture.sink = true }",
                     "--rate", str(rate), "--channels", "1", "--format", "s16", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
                header = audio_process.stdout.read(44)
                if len(header) < 44:
                    raise RuntimeError("incomplete WAV header")

                print(f"Audio: using pw-record on sink {sink_id} at {rate}Hz", file=sys.stderr)
                waiting_logged = False

                while running and audio_process.poll() is None:
                    data = audio_process.stdout.read(chunk_bytes)
                    if len(data) < 2:
                        break
                    n_samples = len(data) // 2
                    samples = struct.unpack(f'<{n_samples}h', data[:n_samples * 2])
                    rms = (sum(s * s for s in samples) / n_samples) ** 0.5
                    level = min(rms / 32768.0 * 6.0, 1.0)
                    with overlay_lock:
                        audio_level = audio_level * 0.3 + level * 0.7
                        if level > audio_peak:
                            audio_peak = level
                        else:
                            audio_peak = max(0.0, audio_peak - 0.03)
            except (OSError, RuntimeError) as e:
                print(f"Audio: capture unavailable ({e}), retrying", file=sys.stderr)
            finally:
                if audio_process and audio_process.poll() is None:
                    audio_process.terminate()
                audio_process = None
                with overlay_lock:
                    audio_level = 0.0
                    audio_peak = 0.0

            if running:
                time.sleep(5)

    t = threading.Thread(target=audio_thread, daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════════
# Overlay application
# ══════════════════════════════════════════════════════════════════

OFFSET_ENABLED = 24
OFFSET_EFFECT = 25
OFFSET_BRIGHTNESS_SCALE = 26
EFFECT_MANUAL = 1


def force_manual_mode(data, preserve_brightness=True):
    data[OFFSET_ENABLED] = 1
    data[OFFSET_EFFECT] = EFFECT_MANUAL
    if not preserve_brightness:
        data[OFFSET_BRIGHTNESS_SCALE] = 255


def render_vu_meter(data, out_leds, level, peak):
    force_manual_mode(data)
    lit_count = level * out_leds
    peak_led = int(peak * (out_leds - 1))

    for i in range(out_leds):
        vi = (out_leds - 1) - i
        off = PIXELS_OFFSET + i * PIXEL_SIZE
        t = vi / max(out_leds - 1, 1)
        if t < 0.5:
            r, g, b = int(255 * t * 2), 255, 0
        else:
            r, g, b = 255, int(255 * (1 - (t - 0.5) * 2)), 0

        if vi < int(lit_count):
            br = 255
        elif vi < lit_count + 1 and lit_count > 0:
            br = int(255 * (lit_count - int(lit_count)))
        elif vi == peak_led and peak > 0.05:
            br = 180
        else:
            br = 0

        data[off + 0] = r
        data[off + 1] = g
        data[off + 2] = b
        data[off + 3] = br


def blend_frames(frame_a, frame_b, factor, out_leds):
    result = bytearray(frame_a)
    result[:PIXELS_OFFSET] = frame_b[:PIXELS_OFFSET]
    for i in range(out_leds):
        off = PIXELS_OFFSET + i * PIXEL_SIZE
        for c in range(PIXEL_SIZE):
            a = frame_a[off + c]
            b = frame_b[off + c]
            result[off + c] = int(a + (b - a) * factor)
    return bytes(result)


def apply_overlays(snapshot, out_leds):
    global notif_active, temp_blink_state
    global prev_overlay_frame, current_mode, last_mode_change

    data = bytearray(snapshot)
    now = time.time()

    with overlay_lock:
        # Priority 1: Notification flash
        if notif_overlay_enabled and notif_active:
            if now > notif_end_time:
                notif_active = False
            else:
                force_manual_mode(data)
                flash_on = int((now - (notif_end_time - NOTIF_DURATION)) / 0.2) % 2 == 0
                br = 255 if flash_on else 80
                r, g, b = notif_color
                for i in range(out_leds):
                    off = PIXELS_OFFSET + i * PIXEL_SIZE
                    data[off + 0] = r
                    data[off + 1] = g
                    data[off + 2] = b
                    data[off + 3] = br
                new_mode = "notif"
                if current_mode != new_mode:
                    last_mode_change = now
                    current_mode = new_mode
                    prev_overlay_frame = snapshot
                return bytes(data)

        has_audio = audio_overlay_enabled and audio_level > 0.02
        has_temp = temp_overlay_enabled and temp_color is not None

        # Determine new mode
        if has_audio and has_temp:
            new_mode = "vu+temp"
        elif has_audio:
            new_mode = "vu"
        elif has_temp:
            new_mode = "temp"
        else:
            new_mode = "gamemode"

        # Audio VU meter
        if has_audio:
            vu_leds = out_leds - 2 if has_temp else out_leds
            render_vu_meter(data, vu_leds, audio_level, audio_peak)

        # Temperature indicator: last 2 LEDs
        if has_temp:
            force_manual_mode(data)
            r, g, b = temp_color
            if temp_blink:
                temp_blink_state = int(now * 4) % 2 == 0
                br = 255 if temp_blink_state else 0
            else:
                br = 255
            for i in range(out_leds - 2, out_leds):
                off = PIXELS_OFFSET + i * PIXEL_SIZE
                data[off + 0] = r
                data[off + 1] = g
                data[off + 2] = b
                data[off + 3] = br

        # Crossfade on mode transition
        if current_mode != new_mode:
            prev_overlay_frame = latest if latest else snapshot
            last_mode_change = now
            current_mode = new_mode

        if prev_overlay_frame and (now - last_mode_change) < 0.3:
            progress = min((now - last_mode_change) / 0.3, 1.0)
            target = bytes(data)
            return blend_frames(prev_overlay_frame, target, progress, out_leds)
        else:
            prev_overlay_frame = None

        if has_audio or has_temp:
            return bytes(data)

    return bytes(data)


# ══════════════════════════════════════════════════════════════════
# Boot / Shutdown / Suspend animations
# ══════════════════════════════════════════════════════════════════

Steam_BLUE = (0, 174, 239)   # Steam Deck blue
Steam_WHITE = (200, 220, 255) # Soft white


def anim_boot(wled, num_leds, duration=1.5):
    """Boot animation: LEDs sweep on from center outward, then pulse."""
    print("Playing boot animation", file=sys.stderr)
    fps = 30
    frames = int(duration * fps)
    mid = num_leds / 2.0

    # Phase 1: sweep from center (0.8s)
    sweep_frames = int(0.8 * fps)
    for f in range(sweep_frames):
        progress = f / max(sweep_frames - 1, 1)
        reach = progress * mid
        pixels = []
        for i in range(num_leds):
            dist = abs(i - mid + 0.5)
            if dist <= reach:
                fade = 1.0 - (dist / mid) * 0.3
                r = int(Steam_BLUE[0] * fade)
                g = int(Steam_BLUE[1] * fade)
                b = int(Steam_BLUE[2] * fade)
                pixels.append((r, g, b))
            else:
                pixels.append((0, 0, 0))
        wled.send_pixels(pixels)
        time.sleep(1.0 / fps)

    # Phase 2: pulse bright then settle (0.7s)
    pulse_frames = int(0.7 * fps)
    for f in range(pulse_frames):
        t = f / max(pulse_frames - 1, 1)
        # Bright flash then ease down
        brightness = 1.0 + 0.5 * math.sin(t * math.pi) * (1.0 - t)
        pixels = []
        for i in range(num_leds):
            r = min(255, int(Steam_BLUE[0] * brightness))
            g = min(255, int(Steam_BLUE[1] * brightness))
            b = min(255, int(Steam_BLUE[2] * brightness))
            pixels.append((r, g, b))
        wled.send_pixels(pixels)
        time.sleep(1.0 / fps)


def anim_shutdown(wled, num_leds, duration=1.2):
    """Shutdown animation: LEDs fade out from edges to center, then off."""
    print("Playing shutdown animation", file=sys.stderr)
    fps = 30
    frames = int(duration * fps)
    mid = num_leds / 2.0

    for f in range(frames):
        progress = f / max(frames - 1, 1)
        # Shrink lit area from edges to center
        reach = mid * (1.0 - progress)
        overall_fade = 1.0 - progress * 0.5
        pixels = []
        for i in range(num_leds):
            dist = abs(i - mid + 0.5)
            if dist <= reach:
                fade = overall_fade * (1.0 - (dist / mid) * 0.3)
                r = int(Steam_BLUE[0] * fade)
                g = int(Steam_BLUE[1] * fade)
                b = int(Steam_BLUE[2] * fade)
                pixels.append((r, g, b))
            else:
                pixels.append((0, 0, 0))
        wled.send_pixels(pixels)
        time.sleep(1.0 / fps)

    # Final: all off
    wled.send_pixels([(0, 0, 0)] * num_leds)


def anim_suspend(wled, num_leds, duration=0.8):
    """Suspend animation: slow fade out."""
    print("Playing suspend animation", file=sys.stderr)
    fps = 30
    frames = int(duration * fps)
    for f in range(frames):
        brightness = 1.0 - (f / max(frames - 1, 1))
        pixels = []
        for i in range(num_leds):
            r = int(Steam_BLUE[0] * brightness * 0.3)
            g = int(Steam_BLUE[1] * brightness * 0.3)
            b = int(Steam_BLUE[2] * brightness * 0.3)
            pixels.append((r, g, b))
        wled.send_pixels(pixels)
        time.sleep(1.0 / fps)
    wled.send_pixels([(0, 0, 0)] * num_leds)


def anim_resume(wled, num_leds, duration=0.8):
    """Resume animation: quick sweep back on."""
    print("Playing resume animation", file=sys.stderr)
    fps = 30
    frames = int(duration * fps)
    mid = num_leds / 2.0
    for f in range(frames):
        progress = f / max(frames - 1, 1)
        reach = progress * mid
        pixels = []
        for i in range(num_leds):
            dist = abs(i - mid + 0.5)
            if dist <= reach:
                fade = progress
                r = int(Steam_BLUE[0] * fade)
                g = int(Steam_BLUE[1] * fade)
                b = int(Steam_BLUE[2] * fade)
                pixels.append((r, g, b))
            else:
                pixels.append((0, 0, 0))
        wled.send_pixels(pixels)
        time.sleep(1.0 / fps)


def start_suspend_monitor(wled, num_leds):
    """Monitor systemd-logind for suspend/resume events via DBus."""
    def logind_thread():
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except ImportError:
            print("dbus-python not available, suspend/resume animations disabled",
                  file=sys.stderr)
            return

        DBusGMainLoop(set_as_default=True)
        try:
            bus = dbus.SystemBus()
            bus.add_signal_receiver(
                lambda going_sleep: (
                    anim_suspend(wled, num_leds) if going_sleep
                    else anim_resume(wled, num_leds)
                ),
                signal_name="PrepareForSleep",
                dbus_interface="org.freedesktop.login1.Manager",
                bus_name="org.freedesktop.login1",
                path="/org/freedesktop/login1"
            )
            print("Suspend/resume monitor: connected to systemd-logind",
                  file=sys.stderr)
            loop = GLib.MainLoop()
            while running:
                loop.get_context().iteration(True)
        except Exception as e:
            print(f"Suspend monitor error: {e}", file=sys.stderr)

    t = threading.Thread(target=logind_thread, daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

_wled_instance = None
_wled_http_instance = None
_num_leds_instance = 0


def shutdown_handler(signum, frame):
    global running
    print(f"Received signal {signum}, shutting down", file=sys.stderr)
    running = False
    # Play shutdown animation before exiting
    if _wled_instance:
        anim_shutdown(_wled_instance, _num_leds_instance)
    # Power off WLED after shutdown animation
    if _wled_http_instance:
        _wled_http_instance.power_off()


def main():
    global latest, running, num_output_leds
    global temp_overlay_enabled, notif_overlay_enabled, audio_overlay_enabled

    # Load config
    conf_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONF
    cfg = load_config(conf_path)

    if not cfg["wled_host"]:
        print("ERROR: wled_host not set in config. Run install.sh first.", file=sys.stderr)
        sys.exit(1)

    num_output_leds = max(1, min(cfg["num_leds"], SRC_NUM_LEDS))
    temp_overlay_enabled = cfg["temp_overlay"]
    notif_overlay_enabled = cfg["notify_overlay"]
    audio_overlay_enabled = cfg["audio_overlay"]

    # Open LED device
    device = cfg["device"]
    try:
        led_fd = os.open(device, os.O_RDONLY)
    except OSError as e:
        print(f"ERROR: Cannot open {device}: {e}", file=sys.stderr)
        sys.exit(1)

    # Create WLED UDP sender
    global _wled_instance, _wled_http_instance, _num_leds_instance
    wled = WLEDUdpSender(cfg["wled_host"], cfg["wled_port"])
    _wled_instance = wled
    _num_leds_instance = num_output_leds

    # Create WLED HTTP controller for power on/off
    wled_http = None
    if cfg["wled_power_control"]:
        wled_http = WLEDHttpControl(cfg["wled_host"])
        _wled_http_instance = wled_http

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Power on WLED and play boot animation
    if wled_http:
        wled_http.power_on()
    anim_boot(wled, num_output_leds)

    # Start overlay threads
    overlays_active = []
    if temp_overlay_enabled:
        overlays_active.append("temp")
    if notif_overlay_enabled:
        start_notification_listener()
        overlays_active.append("notify")
    if audio_overlay_enabled:
        start_audio_monitor()
        overlays_active.append("audio")

    # Start suspend/resume monitor
    start_suspend_monitor(wled, num_output_leds)
    overlays_active.append("suspend")

    overlays_str = ", ".join(overlays_active) if overlays_active else "none"
    print(f"WLED bridge started: {cfg['wled_host']}:{cfg['wled_port']} (UDP), "
          f"{num_output_leds} LEDs, overlays: {overlays_str}",
          file=sys.stderr)

    # Import effect renderer
    import steam_effects

    # Initial snapshot
    last_raw_snap = read_snapshot(led_fd)
    if last_raw_snap:
        latest = last_raw_snap

    last_send = 0.0
    effect_start_time = time.time()
    last_effect_id = None

    last_reopen = time.time()

    while running:
        # Reopen device periodically (not every frame) to get fresh snapshots
        now = time.time()
        if now - last_reopen >= DEVICE_REOPEN_INTERVAL:
            last_reopen = now
            try:
                os.close(led_fd)
                led_fd = os.open(device, os.O_RDONLY)
            except OSError:
                time.sleep(0.5)
                continue

        snap = read_snapshot(led_fd)
        if snap:
            last_raw_snap = snap

        if temp_overlay_enabled:
            update_temperature()

        if last_raw_snap:
            remapped = remap_snapshot(last_raw_snap, num_output_leds)
            final = apply_overlays(remapped, num_output_leds)
        else:
            final = latest

        if final and (now - last_send >= POLL_INTERVAL):
            latest = final

            # Check if an effect is active (byte 25: effect ID)
            effect_id = final[OFFSET_EFFECT] if len(final) > OFFSET_EFFECT else 0
            delay = final[27] if len(final) > 27 else 10

            # Reset timer on effect change
            if effect_id != last_effect_id:
                effect_start_time = now
                last_effect_id = effect_id

            # Effects > 1 need server-side rendering
            if effect_id > 1:
                elapsed_ms = (now - effect_start_time) * 1000.0
                pixels = steam_effects.render_effect(
                    effect_id, final, num_output_leds, elapsed_ms, delay
                )
            else:
                pixels = extract_pixels(final, num_output_leds)

            wled.send_pixels(pixels)
            last_send = now

        time.sleep(POLL_INTERVAL)

    # Cleanup
    print("Shutting down", file=sys.stderr)
    if audio_process:
        audio_process.terminate()
    try:
        os.close(led_fd)
    except OSError:
        pass
    wled.close()


if __name__ == "__main__":
    main()
