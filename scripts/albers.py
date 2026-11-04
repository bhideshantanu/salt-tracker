"""Albers equal-area map projection for the lower 48 states.

Turns longitude/latitude into x/y positions on the tracker's map canvas, which is
975 x 610 units. Both the state outlines and the stream sensors use this same
function, so they line up.
"""

import math

# Standard USGS Albers settings for the contiguous U.S.
_PHI1 = math.radians(29.5)  # first standard parallel
_PHI2 = math.radians(45.5)  # second standard parallel
_PHI0 = math.radians(23.0)  # latitude of origin
_LAM0 = math.radians(-96.0)  # central meridian

_N = (math.sin(_PHI1) + math.sin(_PHI2)) / 2
_C = math.cos(_PHI1) ** 2 + 2 * _N * math.sin(_PHI1)
_RHO0 = math.sqrt(_C - 2 * _N * math.sin(_PHI0)) / _N

# Canvas fit, chosen so the lower 48 fill a 975 x 610 box with a small margin.
WIDTH, HEIGHT = 975, 610
_SCALE = 1268.5
_X_OFFSET = 497.7


def _raw(lon, lat):
    lam = math.radians(lon)
    phi = math.radians(lat)
    rho = math.sqrt(max(_C - 2 * _N * math.sin(phi), 0.0)) / _N
    theta = _N * (lam - _LAM0)
    x = rho * math.sin(theta)
    y = _RHO0 - rho * math.cos(theta)
    return x, y


# Vertical offset that centers the lower 48 (25N to 49N) on the canvas.
_, _y_south = _raw(-96.0, 24.5)
_, _y_north = _raw(-96.0, 49.4)
_Y_CENTER_RAW = (_y_south + _y_north) / 2 + 0.0302


def project(lon, lat):
    """Return (x, y) canvas coordinates for a longitude/latitude in degrees."""
    x, y = _raw(lon, lat)
    cx = _X_OFFSET + x * _SCALE * 1.0
    cy = HEIGHT / 2 - (y - _Y_CENTER_RAW) * _SCALE
    return round(cx, 1), round(cy, 1)


def in_conus(lon, lat):
    """True for points inside a loose box around the lower 48 states."""
    return -125.0 <= lon <= -66.5 and 24.0 <= lat <= 49.6
