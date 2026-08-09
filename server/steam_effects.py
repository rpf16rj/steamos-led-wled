"""Steam Deck LED effect renderers.

Renders the same effects as the original Steam hardware:
- Manual: static colors
- Rainbow: cycling hue
- Breath: pulsing brightness
- Patrol: moving light
- Factory: alternating colors
- Demo: cycling through all effects
"""

import math
import time


def _hsv_to_rgb_255(h, s, v):
    """Convert HSV (h: 0-360, s: 0-255, v: 0-255) to RGB (0-255)."""
    h = h % 360
    s = s / 255.0
    v = v / 255.0
    
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    
    return (
        int((r + m) * 255),
        int((g + m) * 255),
        int((b + m) * 255)
    )


def render_manual(snapshot, num_leds):
    """Manual mode: just return the colors from snapshot."""
    pixels = []
    for i in range(num_leds):
        off = 32 + i * 4
        r = snapshot[off + 0]
        g = snapshot[off + 1]
        b = snapshot[off + 2]
        pixels.append((r, g, b))
    return pixels


def render_rainbow(snapshot, num_leds, elapsed_ms, delay):
    """Rainbow: each LED has different color, all cycling together."""
    # Invert delay: min delay (0) = slow, max delay (20) = fast
    speed = 8000.0 / max(1, (20 - delay + 1))
    # Offset cycles through hues
    offset = int((elapsed_ms / speed) * 360) % 360
    # Each LED gets a different hue offset
    shift = (360 // num_leds) if num_leds else 0
    
    pixels = []
    for i in range(num_leds):
        hue = (offset + i * shift) % 360
        pixels.append(_hsv_to_rgb_255(hue, 255, 255))
    return pixels


def render_breath(snapshot, num_leds, elapsed_ms, delay):
    """Breath: pulsing brightness on base color."""
    # Get base color from first LED
    off = 32
    r = snapshot[off + 0]
    g = snapshot[off + 1]
    b = snapshot[off + 2]
    
    # Invert delay: min delay (0) = slow, max delay (20) = fast
    speed = 5000.0 / max(1, (20 - delay + 1))
    brightness_factor = 0.5 + 0.5 * math.sin(elapsed_ms / speed * 2 * math.pi)
    brightness_factor = max(0.2, min(1.0, brightness_factor))
    
    pixels = []
    for i in range(num_leds):
        pixels.append((
            int(r * brightness_factor),
            int(g * brightness_factor),
            int(b * brightness_factor)
        ))
    return pixels


def render_patrol(snapshot, num_leds, elapsed_ms, delay):
    """Patrol: light bouncing back and forth (ping-pong)."""
    # Invert delay: min delay (0) = slow, max delay (20) = fast
    speed = 3000.0 / max(1, (20 - delay + 1))
    
    # Ping-pong: goes 0->N->0->N...
    cycle_pos = (elapsed_ms / speed) % (2 * num_leds)
    if cycle_pos < num_leds:
        position = cycle_pos
    else:
        position = 2 * num_leds - cycle_pos
    
    # Get base color
    off = 32
    r = snapshot[off + 0]
    g = snapshot[off + 1]
    b = snapshot[off + 2]
    
    pixels = []
    for i in range(num_leds):
        # Distance from light position
        dist = abs(i - position)
        
        # Gaussian falloff
        brightness = math.exp(-(dist ** 2) / (num_leds / 4.0))
        brightness = max(0, min(1.0, brightness))
        
        pixels.append((
            int(r * brightness),
            int(g * brightness),
            int(b * brightness)
        ))
    return pixels


def render_factory(snapshot, num_leds, elapsed_ms, delay):
    """Factory: alternating colors."""
    # Invert delay: min delay (0) = slow, max delay (20) = fast
    speed = 2000.0 / max(1, (20 - delay + 1))
    cycle = int(elapsed_ms / speed) % 2
    
    # Get colors from snapshot
    off = 32
    r1 = snapshot[off + 0]
    g1 = snapshot[off + 1]
    b1 = snapshot[off + 2]
    
    # Alternate with complementary color
    r2 = 255 - r1
    g2 = 255 - g1
    b2 = 255 - b1
    
    pixels = []
    for i in range(num_leds):
        if (i + cycle) % 2 == 0:
            pixels.append((r1, g1, b1))
        else:
            pixels.append((r2, g2, b2))
    return pixels


def render_demo(snapshot, num_leds, elapsed_ms, delay):
    """Demo: cycle through all effects."""
    cycle_duration = 5000.0  # 5 seconds per effect
    effect_index = int(elapsed_ms / cycle_duration) % 5
    
    # Cycle through: rainbow, breath, patrol, factory, manual
    if effect_index == 0:
        return render_rainbow(snapshot, num_leds, elapsed_ms % cycle_duration, delay)
    elif effect_index == 1:
        return render_breath(snapshot, num_leds, elapsed_ms % cycle_duration, delay)
    elif effect_index == 2:
        return render_patrol(snapshot, num_leds, elapsed_ms % cycle_duration, delay)
    elif effect_index == 3:
        return render_factory(snapshot, num_leds, elapsed_ms % cycle_duration, delay)
    else:
        return render_manual(snapshot, num_leds)


def render_effect(effect_id, snapshot, num_leds, elapsed_ms, delay=10):
    """Render an effect frame.
    
    Args:
        effect_id: 0=manual, 3=rainbow, 4=breath, 5=patrol, 6=factory, 7=demo
        snapshot: 100-byte snapshot from valve-leds-shim
        num_leds: number of LEDs to render
        elapsed_ms: milliseconds since effect started
        delay: speed parameter (0-20)
    
    Returns:
        list of (r, g, b) tuples
    """
    # Get brightness scale from snapshot (byte 26)
    # 0 = min brightness, 255 = max brightness
    brightness_scale = snapshot[26] if len(snapshot) > 26 else 255
    brightness_factor = brightness_scale / 255.0
    brightness_factor = max(0.0, min(1.0, brightness_factor))
    
    if effect_id == 0 or effect_id == 1:  # manual
        pixels = render_manual(snapshot, num_leds)
    elif effect_id == 3:  # rainbow
        pixels = render_rainbow(snapshot, num_leds, elapsed_ms, delay)
    elif effect_id == 4:  # breath
        pixels = render_breath(snapshot, num_leds, elapsed_ms, delay)
    elif effect_id == 5:  # patrol
        pixels = render_patrol(snapshot, num_leds, elapsed_ms, delay)
    elif effect_id == 6:  # factory
        pixels = render_factory(snapshot, num_leds, elapsed_ms, delay)
    elif effect_id == 7:  # demo
        pixels = render_demo(snapshot, num_leds, elapsed_ms, delay)
    else:
        pixels = render_manual(snapshot, num_leds)
    
    # Apply brightness scale to all pixels
    return [
        (int(r * brightness_factor), int(g * brightness_factor), int(b * brightness_factor))
        for r, g, b in pixels
    ]
