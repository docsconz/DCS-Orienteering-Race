"""Draws the kneeboard overlays on a stitched Mission Editor map page.

Everything is drawn at 2x and reduced, so lines and text are anti-aliased.  Pages are north-up
(grid north).  Bearings are MAGNETIC: true azimuth from the ME's own lat/lon conversion minus the
ME's own magnetic declination (the same `magvar` module the ME uses for waypoint headings).
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import kb_contour as K
import kb_geom as G
import kb_pages as P

SS = 2                                    # supersampling factor
INK = (25, 25, 25, 255)
PANEL = (255, 255, 255, 228)
CTRL = (205, 0, 125, 255)
CTRL_FILL = (205, 0, 125, 55)
START = (0, 80, 210, 255)
START_FILL = (0, 80, 210, 45)
CONTOUR = (125, 72, 20, 255)
HALO = (255, 255, 255, 150)

_FONTS = {}


def font(size, bold=False):
    key = (size, bold)
    if key in _FONTS:
        return _FONTS[key]
    names = (["arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if bold else
             ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    f = None
    for n in names:
        try:
            f = ImageFont.truetype(n, size * SS)
            break
        except OSError:
            continue
    if f is None:
        f = ImageFont.load_default(size * SS)
    _FONTS[key] = f
    return f


# ---------------------------------------------------------------------------------------------
# geo / bearings

class GeoTable:
    """Pre-fetched lat/lon/declination for the points we need (see required_points)."""

    def __init__(self, rows=None):
        self.rows = {}
        for r in rows or []:
            self.rows[self._k(r["n"], r["e"])] = r

    @staticmethod
    def _k(n, e):
        return (round(n, 1), round(e, 1))

    def get(self, n, e):
        return self.rows.get(self._k(n, e))

    def __bool__(self):
        return bool(self.rows)


def required_points(data, pages):
    pts = [G.zone_centre(data["start"])] + [G.zone_centre(c) for c in data["controls"]]
    for p in pages:
        pts.append((p["cn"], p["ce"]))
        pts.append((p["cn"] + 1000.0, p["ce"]))
    seen, out = set(), []
    for q in pts:
        k = GeoTable._k(*q)
        if k not in seen:
            seen.add(k)
            out.append((round(q[0], 1), round(q[1], 1)))
    return out


def leg(geo, a, b):
    """(bearing deg, distance nm, 'M' or 'G') from grid point a to b."""
    dist_nm = G.grid_distance_m(a, b) / G.M_PER_NM
    ga, gb = geo.get(*a) if geo else None, geo.get(*b) if geo else None
    if ga and gb:
        true = G.true_azimuth_deg(ga["lat"], ga["lon"], gb["lat"], gb["lon"])
        return G.magnetic_deg(true, ga["decl"]), dist_nm, "M"
    return G.grid_bearing_deg(a, b), dist_nm, "G"


def mag_north_angle(geo, page):
    """Degrees clockwise from page-up at which MAGNETIC north points (None if no geo)."""
    a = geo.get(page["cn"], page["ce"]) if geo else None
    b = geo.get(page["cn"] + 1000.0, page["ce"]) if geo else None
    if not (a and b):
        return None
    gamma = G.true_azimuth_deg(a["lat"], a["lon"], b["lat"], b["lon"])   # true azimuth of grid north
    return a["decl"] - gamma, a["decl"]


def alt_text(c):
    lo, hi = c.get("min_ft"), c.get("max_ft")
    ref = c.get("ref", "AGL")
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        return "%g-%g ft %s" % (lo, hi, ref)
    return ("min %g ft %s" % (lo, ref)) if lo is not None else ("max %g ft %s" % (hi, ref))


def alt_short(c):
    lo, hi, ref = c.get("min_ft"), c.get("max_ft"), c.get("ref", "AGL")
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        return "%g-%g %s" % (lo, hi, ref)
    return ("\u2265%g %s" % (lo, ref)) if lo is not None else ("\u2264%g %s" % (hi, ref))


# ---------------------------------------------------------------------------------------------
# drawing helpers (all coordinates in 1x page pixels; scaled by SS inside)

def _s(p):
    return (p[0] * SS, p[1] * SS)


def _poly(draw, pts, fill=None, outline=None, width=1, closed=True):
    q = [_s(p) for p in pts]
    if fill is not None and closed:
        draw.polygon(q, fill=fill)
    if outline is not None:
        draw.line(q + ([q[0]] if closed else []), fill=outline, width=int(width * SS), joint="curve")


def _text_size(draw, text, f):
    b = draw.textbbox((0, 0), text, font=f)
    return (b[2] - b[0]) / SS, (b[3] - b[1]) / SS


def _label(draw, xy, text, f, fill=INK, bg=PANEL, pad=7, anchor="lt", border=None):
    """Text on a rounded panel; returns its 1x bounding rect (x0, y0, x1, y1)."""
    w, h = _text_size(draw, text, f)
    x, y = xy
    if anchor == "rt":
        x -= w + 2 * pad
    elif anchor == "ct":
        x -= (w + 2 * pad) / 2
    rect = (x, y, x + w + 2 * pad, y + h + 2 * pad)
    draw.rounded_rectangle([c * SS for c in rect], radius=6 * SS, fill=bg,
                           outline=border, width=2 * SS if border else 0)
    b = draw.textbbox((0, 0), text, font=f)
    draw.text(((x + pad) * SS - b[0], (y + pad) * SS - b[1]), text, font=f, fill=fill)
    return rect


def _rotated_text(ov, centre, text, f, angle_deg, fill, halo=(255, 255, 255, 235)):
    w = ov.size[0]
    tmp = Image.new("RGBA", (400 * SS, 80 * SS), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    b = td.textbbox((0, 0), text, font=f, stroke_width=3 * SS)
    td.text((20 * SS - b[0], 20 * SS - b[1]), text, font=f, fill=fill,
            stroke_width=3 * SS, stroke_fill=halo)
    tmp = tmp.crop((0, 0, 40 * SS + (b[2] - b[0]), 40 * SS + (b[3] - b[1])))
    rot = tmp.rotate(angle_deg, expand=True, resample=Image.BICUBIC)
    ov.alpha_composite(rot, (int(centre[0] * SS - rot.size[0] / 2), int(centre[1] * SS - rot.size[1] / 2)))


def _overlaps(r, taken):
    return any(not (r[2] <= t[0] or r[0] >= t[2] or r[3] <= t[1] or r[1] >= t[3]) for t in taken)


def _place_box(anchor, W, H, taken, bounds, gap=10):
    """Choose the top-left for a W x H box near `anchor` (8 positions tried); avoids `taken`."""
    ax, ay = anchor
    cands = [(ax + gap, ay - H - gap / 2), (ax + gap, ay + gap / 2), (ax - W - gap, ay - H - gap / 2),
             (ax - W - gap, ay + gap / 2), (ax - W / 2, ay - H - gap), (ax - W / 2, ay + gap),
             (ax + gap, ay - H / 2), (ax - W - gap, ay - H / 2)]
    x0, y0, x1, y1 = bounds
    chosen = None
    for cx, cy in cands:
        r = (cx, cy, cx + W, cy + H)
        if r[0] >= x0 and r[1] >= y0 and r[2] <= x1 and r[3] <= y1 and not _overlaps(r, taken):
            chosen = (cx, cy)
            break
    if chosen is None:
        chosen = (min(max(cands[0][0], x0), x1 - W), min(max(cands[0][1], y0), y1 - H))
    taken.append((chosen[0], chosen[1], chosen[0] + W, chosen[1] + H))
    return chosen


def _place(draw, anchor, text, f, taken, bounds, gap=10, **kw):
    """Draw a label near `anchor`, trying 8 positions around it; avoids `taken` and the page edge."""
    w, h = _text_size(draw, text, f)
    pad = kw.get("pad", 7)
    x, y = _place_box(anchor, w + 2 * pad, h + 2 * pad, taken, bounds, gap)
    return _label(draw, (x, y), text, f, **kw)


def _centred_text(draw, cx, cy, text, f, fill):
    b = draw.textbbox((0, 0), text, font=f)
    draw.text((cx * SS - (b[0] + b[2]) / 2, cy * SS - (b[1] + b[3]) / 2), text, font=f, fill=fill)


BADGE_FILL = (255, 255, 255, 246)


def draw_num_circle(draw, cx, cy, num, f, d, colour=CTRL):
    """Control number inside a circle (centre cx, cy; diameter d)."""
    draw.ellipse([(cx - d / 2) * SS, (cy - d / 2) * SS, (cx + d / 2) * SS, (cy + d / 2) * SS],
                 fill=BADGE_FILL, outline=colour, width=int(3 * SS))
    _centred_text(draw, cx, cy, "%02d" % num, f, colour)


def draw_pts_box(draw, x, cy, text, f, w, h, colour=CTRL):
    """Point value inside a square-cornered rectangle (left edge x, centre y)."""
    draw.rectangle([x * SS, (cy - h / 2) * SS, (x + w) * SS, (cy + h / 2) * SS],
                   fill=BADGE_FILL, outline=colour, width=int(3 * SS))
    _centred_text(draw, x + w / 2, cy, text, f, INK)


def _badge_layout(draw, num, pts, f, name=None, fn=None, colour=CTRL):
    """A control marker: (circle with number)(rectangle with points)[ name ].  -> (W, H, paint(x, y))."""
    tw, th = _text_size(draw, "00", f)
    d = max(tw, th) + 16
    pw = _text_size(draw, str(pts), f)[0] + 22
    h = d - 6
    gap = 6
    nw = 0
    if name:
        nw = _text_size(draw, name, fn)[0] + 20 + gap
    W = d + gap + pw + nw

    def paint(x, y):
        cy = y + d / 2
        draw_num_circle(draw, x + d / 2, cy, num, f, d, colour)
        draw_pts_box(draw, x + d + gap, cy, str(pts), f, pw, h, colour)
        if name:
            nx = x + d + gap + pw + gap
            nh = _text_size(draw, name, fn)[1] + 14
            draw.rounded_rectangle([nx * SS, (cy - nh / 2) * SS, (nx + nw - gap) * SS, (cy + nh / 2) * SS],
                                   radius=6 * SS, fill=PANEL)
            _centred_text(draw, nx + (nw - gap) / 2, cy, name, fn, colour)
    return W, d, paint


def _place_badge(draw, anchor, taken, bounds, gap, **kw):
    W, H, paint = _badge_layout(draw, **kw)
    x, y = _place_box(anchor, W, H, taken, bounds, gap)
    paint(x, y)


def _zone_px(z, frame):
    pts = G.zone_points(z)
    if pts:
        return [frame.to_px(n, e) for n, e in pts]
    return None


def draw_zone(draw, z, frame, colour, fill, width, min_marker=0):
    n, e = G.zone_centre(z)
    cx, cy = frame.to_px(n, e)
    poly = _zone_px(z, frame)
    if poly:
        _poly(draw, poly, fill=fill, outline=colour, width=width)
        extent = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a in poly for b in poly) / 2.0
    else:
        r = z.get("radius_m", 0.0) / frame.mpp
        draw.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                     fill=fill, outline=colour, width=int(width * SS))
        extent = r
    if min_marker and extent < min_marker:               # tiny on this page: add a visible ring
        r = min_marker
        draw.ellipse([(cx - r - 2) * SS, (cy - r - 2) * SS, (cx + r + 2) * SS, (cy + r + 2) * SS],
                     outline=(255, 255, 255, 230), width=int(7 * SS))
        draw.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                     outline=colour, width=int(3.5 * SS))
    return (cx, cy), max(extent, min_marker)


def draw_cross(draw, c, colour, r=9, w=2.5):
    for dx, dy in ((r, 0), (0, r)):
        draw.line([(c[0] - dx) * SS, (c[1] - dy) * SS, (c[0] + dx) * SS, (c[1] + dy) * SS],
                  fill=(255, 255, 255, 230), width=int((w + 3) * SS))
        draw.line([(c[0] - dx) * SS, (c[1] - dy) * SS, (c[0] + dx) * SS, (c[1] + dy) * SS],
                  fill=colour, width=int(w * SS))


# ---------------------------------------------------------------------------------------------

def draw_contours(ov, draw, z, spec, frame, interval_ft):
    lines = K.contour_lines(z, interval_ft)
    step = spec["step"]
    sx = step / frame.mpp
    x_off = frame.w / 2.0 + (spec["e0"] - frame.ce) / frame.mpp
    y_off = frame.h / 2.0 - (spec["n0"] - frame.cn) / frame.mpp
    f = font(21, bold=True)
    labelled = []
    for ln in lines:
        px = [(x_off + p[0] * sx, y_off + p[1] * sx) for p in ln["pts"]]
        if len(px) < 2:
            continue
        w = 2.2 if ln["index"] else 1.0
        q = [_s(p) for p in px]
        draw.line(q, fill=HALO, width=int((w + 1.6) * SS), joint="curve")
        draw.line(q, fill=CONTOUR, width=int(w * SS), joint="curve")
        if ln["index"]:
            seg = np.hypot(*np.diff(np.array(px), axis=0).T)
            total = float(seg.sum())
            if total < 260:
                continue
            nlab = max(1, int(total // 700))
            for m in range(nlab):
                target = total * (m + 0.5) / nlab
                acc = np.cumsum(seg)
                i = int(np.searchsorted(acc, target))
                i = min(i, len(seg) - 1)
                a, b = px[i], px[i + 1]
                pos = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if not (40 < pos[0] < frame.w - 40 and 40 < pos[1] < frame.h - 40):
                    continue
                if any(math.hypot(pos[0] - o[0], pos[1] - o[1]) < 260 for o in labelled):
                    continue
                ang = math.degrees(math.atan2(-(b[1] - a[1]), b[0] - a[0]))
                if ang > 90:
                    ang -= 180
                elif ang < -90:
                    ang += 180
                _rotated_text(ov, pos, "%d" % round(ln["level_ft"]), f, ang, CONTOUR)
                labelled.append(pos)
    return len(lines)


def draw_scale_bar(draw, frame, x, y, max_px=520):
    target_m = max_px * frame.mpp
    L = G.nice_length_m(target_m)
    px = L / frame.mpp
    f = font(24, bold=True)
    draw.rectangle([(x - 12) * SS, (y - 44) * SS, (x + px + 12) * SS, (y + 26) * SS], fill=PANEL)
    draw.rectangle([x * SS, (y - 5) * SS, (x + px / 2) * SS, y * SS], fill=INK)
    draw.rectangle([(x + px / 2) * SS, (y - 5) * SS, (x + px) * SS, y * SS], outline=INK, width=SS)
    for t in (0, px):
        draw.line([(x + t) * SS, (y - 11) * SS, (x + t) * SS, (y + 6) * SS], fill=INK, width=2 * SS)
    txt = ("%g km = %.1f nm" % (L / 1000.0, L / G.M_PER_NM)) if L >= 1000 else \
          ("%g m = %d ft" % (L, round(L * G.FT_PER_M)))
    draw.text((x * SS, (y - 41) * SS), txt, font=f, fill=INK)
    return (x - 12, y - 44, x + px + 12, y + 26)


def draw_north_arrows(draw, centre, mag_info, size=64):
    """Grid-north (up, grey) and magnetic-north (blue) arrows."""
    cx, cy = centre
    draw.rounded_rectangle([(cx - 78) * SS, (cy - 84) * SS, (cx + 78) * SS, (cy + 80) * SS],
                           radius=10 * SS, fill=PANEL)

    def arrow(ang, colour, width, length):
        a = math.radians(ang)
        tip = (cx + math.sin(a) * length, cy - math.cos(a) * length)
        base = (cx - math.sin(a) * 14, cy + math.cos(a) * 14)
        draw.line([_s(base), _s(tip)], fill=colour, width=int(width * SS))
        for da in (150, -150):
            b = a + math.radians(da)
            draw.line([_s(tip), _s((tip[0] + math.sin(b) * 17, tip[1] - math.cos(b) * 17))],
                      fill=colour, width=int(width * SS))
    arrow(0, (110, 110, 110, 255), 3, size * 0.8)
    f = font(22, bold=True)
    draw.text(((cx + 14) * SS, (cy - 80) * SS), "N", font=f, fill=(90, 90, 90, 255))
    if mag_info:
        arrow(mag_info[0], (0, 70, 200, 255), 5, size)
        t = "MAG %.1f°%s" % (abs(mag_info[1]), "E" if mag_info[1] >= 0 else "W")
        draw.text(((cx - 62) * SS, (cy + 52) * SS), t, font=font(21, bold=True), fill=(0, 70, 200, 255))
    else:
        draw.text(((cx - 50) * SS, (cy + 52) * SS), "grid N", font=f, fill=(90, 90, 90, 255))


# ---------------------------------------------------------------------------------------------

def _fmt_leg(l):
    deg = int(round(l[0])) % 360 or 360
    return "%03d\u00b0%s %.1f nm" % (deg, l[2], l[1])


def _nearest(c, ctrls, k=2):
    cc = G.zone_centre(c)
    others = [o for o in ctrls if o["num"] != c["num"]]
    others.sort(key=lambda o: G.grid_distance_m(cc, G.zone_centre(o)))
    return others[:k]


def render_page(base, page, data, heights=None, geo=None, base_contour_ft=40.0, spokes=True,
                contours=True):
    """base: ndarray HxWx3 uint8 stitched map; heights: (z, spec) or None.  -> PIL RGB image."""
    frame = G.Frame(page["cn"], page["ce"], page["scale"], base.shape[1], base.shape[0])
    W, H = frame.w, frame.h
    img = Image.fromarray(base).convert("RGBA")
    ov = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ov)
    start = data["start"]
    ctrls = sorted(data["controls"], key=lambda c: c["num"])
    by_num = {c["num"]: c for c in ctrls}
    kind = page["kind"]
    focus = by_num.get(page["ctrl"])
    sc = G.zone_centre(start)
    unit = "M" if geo else "G"

    # --- contours
    interval = None
    if contours and heights is not None:
        z, spec = heights
        interval = K.pick_interval_ft(z, spec["step"] / frame.mpp, base_ft=base_contour_ft)
        draw_contours(ov, draw, z, spec, frame, interval)

    top_res = P.HEADER_PX if kind == "overview" else 0
    bounds = (14, top_res + 14 if kind == "overview" else 210, W - 14, H - 110)
    taken = []
    table = None
    if kind == "overview":
        table = _table_geometry(draw, ctrls)
        table["rect"] = _pick_table_corner(base, frame, table, [sc] + [G.zone_centre(c) for c in ctrls])
        taken.append(table["rect"])

    # --- spokes (overview): START -> each control
    if kind == "overview" and spokes:
        sx, sy = frame.to_px(*sc)
        for c in ctrls:
            cx, cy = frame.to_px(*G.zone_centre(c))
            ln = [(sx * SS, sy * SS), (cx * SS, cy * SS)]
            draw.line(ln, fill=(0, 0, 0, 110), width=int(5 * SS))
            draw.line(ln, fill=(255, 255, 255, 235), width=int(2.2 * SS))

    # --- zones
    big = 16 if kind == "overview" else 0
    for c in ctrls:
        is_focus = (c is focus)
        col = CTRL
        pos, ext = draw_zone(draw, c, frame, col, CTRL_FILL, 5 if is_focus else 3, min_marker=big)
        if not is_focus and kind != "overview":
            col = (150, 40, 110, 255)
        draw_cross(draw, pos, col)
    spos, sext = draw_zone(draw, start, frame, START, START_FILL, 4, min_marker=big)
    draw_cross(draw, spos, START)

    # --- labels
    fl = font(34, bold=True)
    fm = font(27, bold=True)
    for c in ctrls:
        pos = frame.to_px(*G.zone_centre(c))
        if not (-40 < pos[0] < W + 40 and -40 < pos[1] < H + 40):
            continue
        if kind == "overview":
            _place_badge(draw, pos, taken, bounds, big, num=c["num"], pts=c["points"], f=fl)
        elif c is focus:
            continue                      # described in the header
        else:
            _place_badge(draw, pos, taken, bounds, 10, num=c["num"], pts=c["points"], f=fm,
                         name=c["name"], fn=font(24, True), colour=(150, 40, 110, 255))
    spos = frame.to_px(*sc)
    if -40 < spos[0] < W + 40 and -40 < spos[1] < H + 40:
        _place(draw, spos, "START", fl, taken, bounds, gap=big + 6, fill=START, border=START)

    # --- header / table
    if kind == "overview":
        _overview_panels(draw, frame, data, ctrls, geo, unit, interval, table)
    else:
        _control_header(draw, frame, page, data, focus, ctrls, geo, unit)
        # the focus control's own label near its zone (points are the thing a pilot needs)
        pos = frame.to_px(*G.zone_centre(focus))
        _place_badge(draw, pos, taken, (14, 210, W - 14, H - 130),
                     max(26, (G.zone_size(focus) / frame.mpp) / 2 + 12),
                     num=focus["num"], pts=focus["points"], f=fl)

    # --- footer: scale bar, north arrows, notes
    draw_scale_bar(draw, frame, 30, H - 38 - (0 if kind != "overview" else 0))
    mag = mag_north_angle(geo, page)
    draw_north_arrows(draw, (W - 100, 100) if kind == "overview" else (W - 100, H - 100), mag)
    note = ("Bearings %s, distances nm.  %s" %
            ("MAGNETIC" if unit == "M" else "GRID (no declination data!)",
             ("Contours %g ft, index lines labelled (ft MSL)." % interval) if interval else "No contours."))
    _label(draw, (30, H - 110 - 0), note, font(22), pad=6) if kind != "overview" else None

    ov = ov.resize((W, H), Image.LANCZOS)
    return Image.alpha_composite(img, ov).convert("RGB")


def _control_header(draw, frame, page, data, c, ctrls, geo, unit):
    W = frame.w
    cc = G.zone_centre(c)
    alt = alt_text(c)
    ft, fs, fl = font(54, True), font(30, True), font(26)
    D = 66                                                   # number circle diameter
    pts_txt = "%d pts" % c["points"]
    rest = ""
    if alt:
        rest += "altitude %s   \u2022   " % alt
    rest += "%s view" % page["kind"]
    back = leg(geo, cc, G.zone_centre(data["start"]))
    parts = ["to START %s" % _fmt_leg(back)]
    for o in _nearest(c, ctrls, 2):
        parts.append("%02d %s" % (o["num"], _fmt_leg(leg(geo, cc, G.zone_centre(o)))))
    legs = "   \u2022   ".join(parts)
    h_sub = _text_size(draw, pts_txt, fs)[1] + 16
    h_leg = _text_size(draw, legs, fl)[1]
    total = D + 12 + h_sub + 12 + h_leg + 28
    draw.rounded_rectangle([14 * SS, 14 * SS, (W - 14) * SS, (14 + total) * SS], radius=10 * SS, fill=PANEL)
    y = 28
    draw_num_circle(draw, 30 + D / 2, y + D / 2, c["num"], font(34, True), D)
    b = draw.textbbox((0, 0), c["name"], font=ft)
    draw.text(((30 + D + 18) * SS - b[0], (y + D / 2) * SS - (b[1] + b[3]) / 2), c["name"], font=ft, fill=INK)
    y += D + 12
    bw = _text_size(draw, pts_txt, fs)[0] + 24
    draw_pts_box(draw, 30, y + h_sub / 2, pts_txt, fs, bw, h_sub)
    b = draw.textbbox((0, 0), rest, font=fs)
    draw.text(((30 + bw + 18) * SS - b[0], (y + h_sub / 2) * SS - (b[1] + b[3]) / 2), rest, font=fs, fill=CTRL)
    y += h_sub + 12
    b = draw.textbbox((0, 0), legs, font=fl)
    draw.text((30 * SS - b[0], y * SS - b[1]), legs, font=fl, fill=INK)


def _table_geometry(draw, ctrls):
    """Column positions and size of the compact overview table (no placement yet)."""
    fn, fa, fh = font(24), font(21), font(24, True)
    name_w = 0
    for c in ctrls:
        w = _text_size(draw, c["name"][:26], fn)[0]
        if alt_short(c):
            w += 16 + _text_size(draw, alt_short(c), fa)[0]
        name_w = max(name_w, w)
    x_name = 56
    x_pts = x_name + name_w + 18
    x_brg = x_pts + 62 + 16
    brg_w = max(_text_size(draw, "000\u00b0M 00.0 nm", font(26))[0], _text_size(draw, "From START", fh)[0])
    w = x_brg + brg_w + 18
    h = 12 + P.ROW_PX * (len(ctrls) + 1) + 10
    return {"x_name": x_name, "x_pts": x_pts, "x_brg": x_brg, "w": int(w), "h": int(h)}


def _pick_table_corner(base, frame, t, pts):
    """Corner of the page where the table hides the least: no controls, and mostly sea."""
    W, H = frame.w, frame.h
    w, h = t["w"], t["h"]
    m = 14
    top = P.HEADER_PX + 4
    cands = [(m, H - 100 - h), (W - m - w, H - m - h), (m, top), (W - m - w, 196)]
    best, best_score = None, None
    px = [frame.to_px(*p) for p in pts]
    for i, (x, y) in enumerate(cands):
        r = (x, y, x + w, y + h)
        if r[1] < top - 4 or r[0] < 0:
            continue
        hits = sum(1 for (a, b) in px if r[0] - 70 <= a <= r[2] + 70 and r[1] - 40 <= b <= r[3] + 40)
        crop = base[int(r[1]):int(r[3]), int(r[0]):int(r[2])].astype(int)
        sea = float(((crop[..., 2] > crop[..., 0] + 30) & (crop[..., 2] > crop[..., 1] - 5)).mean()) if crop.size else 0
        score = hits * 1000 + (1 - sea) * 100 + i
        if best_score is None or score < best_score:
            best, best_score = r, score
    return best


def _overview_panels(draw, frame, data, ctrls, geo, unit, interval, t):
    W, H = frame.w, frame.h
    # header
    draw.rounded_rectangle([14 * SS, 14 * SS, (W - 190) * SS, (P.HEADER_PX - 20) * SS], radius=10 * SS, fill=PANEL)
    f = font(54, True)
    draw.text((34 * SS, 24 * SS), "OVERVIEW", font=f, fill=INK)
    tour = data.get("tour")
    if isinstance(tour, dict) and tour.get("distance_nm") is not None:
        sub = "START + %d controls   \u2022   shortest route %.1f nm" % (len(ctrls), tour["distance_nm"])
    else:
        sub = "START + %d controls" % len(ctrls)
    draw.text((34 * SS, 96 * SS), sub, font=font(28), fill=INK)
    fk = font(22, True)                                       # key: what the two marker shapes mean
    kx = 470
    draw_num_circle(draw, kx + 17, 52, 1, fk, 34)
    draw.text(((kx + 42) * SS, 40 * SS), "control no.", font=font(24), fill=INK)
    draw_pts_box(draw, kx + 190, 52, "24", fk, 50, 30)
    draw.text(((kx + 250) * SS, 40 * SS), "points", font=font(24), fill=INK)
    # table
    x0, y0, x1, y1 = t["rect"]
    draw.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=10 * SS, fill=(255, 255, 255, 238))
    fh, fn, fr = font(24, True), font(24), font(26)
    y = y0 + 12
    grey = (80, 80, 80, 255)
    draw.text(((x0 + t["x_name"]) * SS, y * SS), "Control", font=fh, fill=grey)
    draw.text(((x0 + t["x_pts"] - 6) * SS, y * SS), "Pts", font=fh, fill=grey)
    draw.text(((x0 + t["x_brg"]) * SS, y * SS), "From START", font=fh, fill=grey)
    y += P.ROW_PX
    sc = G.zone_centre(data["start"])
    for c in ctrls:
        cc = G.zone_centre(c)
        draw_num_circle(draw, x0 + 30, y + 17, c["num"], font(19, True), 38)
        draw.text(((x0 + t["x_name"]) * SS, (y + 3) * SS), c["name"][:26], font=fn, fill=INK)
        if alt_short(c):
            nm_w = _text_size(draw, c["name"][:26], fn)[0]
            draw.text(((x0 + t["x_name"] + nm_w + 16) * SS, (y + 6) * SS), alt_short(c), font=font(21),
                      fill=(90, 90, 90, 255))
        draw_pts_box(draw, x0 + t["x_pts"] - 8, y + 17, "%d" % c["points"], font(24, True), 62, 34)
        draw.text(((x0 + t["x_brg"]) * SS, (y + 1) * SS), _fmt_leg(leg(geo, sc, cc)), font=fr, fill=INK)
        y += P.ROW_PX
