"""Pure geometry for the kneeboard tool. No DCS, no images.

Measured on a real Mission Editor (see README, "Calibration"):
  * metres per screen pixel = scale / 3500, exactly, at every zoom tried;
  * `me camera focus` places the requested point at the centre of the map pane;
  * DCS local coordinates: x = north, y/z = east (metres).
Pages are always north-up (grid north), 3:4 portrait.
"""
import math

SCALE_DIV = 3500.0
PAGE_W, PAGE_H = 1536, 2048
FT_PER_M = 3.280839895013123
M_PER_NM = 1852.0


def mpp(scale):
    """Metres per pixel for an ME map scale."""
    return scale / SCALE_DIV


def scale_for_mpp(m):
    """ME scale (kept integral on purpose) for a wanted metres-per-pixel."""
    return int(round(m * SCALE_DIV))


class Frame:
    """World <-> page-pixel mapping for one north-up page."""

    def __init__(self, cn, ce, scale, w=PAGE_W, h=PAGE_H):
        self.cn, self.ce, self.scale, self.w, self.h = cn, ce, scale, w, h
        self.mpp = mpp(scale)

    def to_px(self, north, east):
        return (self.w / 2.0 + (east - self.ce) / self.mpp,
                self.h / 2.0 - (north - self.cn) / self.mpp)

    def to_world(self, px, py):
        return (self.cn - (py - self.h / 2.0) * self.mpp,
                self.ce + (px - self.w / 2.0) * self.mpp)

    def extent_m(self):
        return self.w * self.mpp, self.h * self.mpp


# --- zones as written by extract.py into controls.json ---------------------------------

def zone_centre(z):
    return (z["x"], z["z"])


def zone_points(z):
    """Quad vertices as (north, east); [] for a circle."""
    return [(v["x"], v["z"]) for v in z.get("vertices", [])]


def zone_extent(z):
    """(min_n, max_n, min_e, max_e)."""
    pts = zone_points(z)
    if pts:
        ns = [p[0] for p in pts]
        es = [p[1] for p in pts]
        return min(ns), max(ns), min(es), max(es)
    r = z.get("radius_m", 0.0)
    n, e = zone_centre(z)
    return n - r, n + r, e - r, e + r


def zone_size(z):
    """Longest dimension of the zone in metres (diameter or longest vertex-to-vertex)."""
    pts = zone_points(z)
    if pts:
        return max(math.hypot(a[0] - b[0], a[1] - b[1]) for a in pts for b in pts)
    return 2.0 * z.get("radius_m", 0.0)


# --- bearings and distances --------------------------------------------------------------

def grid_distance_m(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def grid_bearing_deg(a, b):
    """Bearing on the (north-up) grid, degrees clockwise from grid north."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def true_azimuth_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle azimuth from point 1 to point 2 (spherical; plenty for < 100 km)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def magnetic_deg(true_deg, decl_east_deg):
    """Magnetic bearing = true bearing - (east-positive) declination."""
    return (true_deg - decl_east_deg) % 360.0


def nice_length_m(target_m):
    """Round a length down to 1/2/5 x 10^n for a scale bar."""
    if target_m <= 0:
        return 1.0
    e = math.floor(math.log10(target_m))
    for m in (5, 2, 1):
        v = m * 10 ** e
        if v <= target_m:
            return v
    return 10 ** e
