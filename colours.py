"""Colour maths for line colours: contrast, perceptual difference, and shades of a brand colour."""

import colorsys
import math

MODE_COLOURS = {"tram": "#6B7280", "metro": "#6A1B9A", "train": "#C2185B", "bus": "#1565C0", "ferry": "#00897B"}
MAP_BACKGROUNDS = ("#FFFFFF", "#15181C")  # light and dark basemaps
SHADE_MIN_CONTRAST = 1.6  # derived shades must stand out against both (lines also have a casing)
SHADE_HUE_SHIFTS = (0, -0.03, 0.03)  # a slight hue nudge (~11 degrees) helps tell close shades apart


def rgb(hex_colour):
    """#RRGGBB -> (r, g, b) in 0..1."""
    return tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def linear(c):
    """sRGB channel (0..1) to linear light."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    """WCAG relative luminance of #RRGGBB."""
    r, g, b = (linear(c) for c in rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio between two #RRGGBB colours (1 = identical, 21 = black on white)."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def to_lab(hex_colour):
    """CIELAB (D65) of #RRGGBB, for perceptual colour differences."""
    r, g, b = (linear(c) for c in rgb(hex_colour))
    xyz = ((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b,
           (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883)
    fx, fy, fz = (v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in xyz)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def colour_distance(a, b):
    """CIE76 colour difference (about 2.3 = just noticeable; 40+ = clearly different colours)."""
    return math.dist(to_lab(a), to_lab(b))


def mode_shades(base, n):
    """n colours derived from base: base itself first, then lighter and darker shades (hue nudged by
    at most ~11 degrees), each chosen for the greatest luminance contrast with those already picked,
    ties broken by colour difference, while staying visible on both light and dark maps."""
    hue, _, sat = colorsys.rgb_to_hls(*rgb(base))

    def shade(dh, lightness):
        return "#" + "".join(f"{round(c * 255):02X}" for c in colorsys.hls_to_rgb((hue + dh) % 1, lightness, sat))

    pool = sorted({shade(dh, x / 100) for dh in SHADE_HUE_SHIFTS for x in range(10, 91, 2)})
    candidates = [c for c in pool if min(contrast(c, bg) for bg in MAP_BACKGROUNDS) >= SHADE_MIN_CONTRAST] or pool
    out = [base.upper()]

    def score(c):
        return (round(min(contrast(c, o) for o in out), 1), round(min(colour_distance(c, o) for o in out), 3), c)
    while len(out) < n:
        out.append(max((c for c in candidates if c not in out), key=score))
    return out
