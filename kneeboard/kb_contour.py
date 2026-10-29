"""Contour lines from a height grid (marching squares, numpy only)."""
import math

import numpy as np

import kb_geom as G

INTERVALS_FT = [10, 20, 40, 100, 200, 400, 1000, 2000, 4000]


def contour_segments(z, level):
    """Segments of the iso-line z == level.  z[j, i]; returns float array (N, 2, 2) of
    ((x0, y0), (x1, y1)) in grid-index units (x = column, y = row)."""
    a, b, c, d = z[:-1, :-1], z[:-1, 1:], z[1:, 1:], z[1:, :-1]     # tl tr br bl
    case = ((a > level) * 8 + (b > level) * 4 + (c > level) * 2 + (d > level) * 1).astype(np.int8)
    jj, ii = np.nonzero((case > 0) & (case < 15))
    if jj.size == 0:
        return np.zeros((0, 2, 2), np.float32)
    case = case[jj, ii]
    a, b, c, d = a[jj, ii], b[jj, ii], c[jj, ii], d[jj, ii]
    with np.errstate(divide="ignore", invalid="ignore"):
        top = np.stack([ii + (level - a) / (b - a), jj.astype(np.float64)], -1)
        right = np.stack([ii + 1.0 + 0 * a, jj + (level - b) / (c - b)], -1)
        bottom = np.stack([ii + (level - d) / (c - d), jj + 1.0 + 0 * a], -1)
        left = np.stack([ii + 0.0 * a, jj + (level - a) / (d - a)], -1)
    centre_hi = (a + b + c + d) / 4.0 > level
    # (case -> list of edge pairs); saddles 5 and 10 are resolved by the cell-centre value
    pairs = {1: [(left, bottom)], 2: [(bottom, right)], 3: [(left, right)], 4: [(top, right)],
             6: [(top, bottom)], 7: [(top, left)], 8: [(top, left)], 9: [(top, bottom)],
             11: [(top, right)], 12: [(left, right)], 13: [(bottom, right)], 14: [(left, bottom)]}
    segs = []
    for cs, pr in pairs.items():
        m = case == cs
        if m.any():
            for p, q in pr:
                segs.append(np.stack([p[m], q[m]], 1))
    for cs, hi_pairs, lo_pairs in ((5, [(top, left), (bottom, right)], [(top, right), (left, bottom)]),
                                   (10, [(top, right), (left, bottom)], [(top, left), (bottom, right)])):
        m = case == cs
        for mask, pr in ((m & centre_hi, hi_pairs), (m & ~centre_hi, lo_pairs)):
            if mask.any():
                for p, q in pr:
                    segs.append(np.stack([p[mask], q[mask]], 1))
    return np.concatenate(segs, 0).astype(np.float32) if segs else np.zeros((0, 2, 2), np.float32)


def chain(segs, eps=1e-3):
    """Join segments into polylines (lists of (x, y)).  Closed loops repeat their first point."""
    key = lambda p: (int(round(p[0] / eps)), int(round(p[1] / eps)))
    ends = {}
    for i, (p, q) in enumerate(segs):
        ends.setdefault(key(p), []).append(i)
        ends.setdefault(key(q), []).append(i)
    used = np.zeros(len(segs), bool)
    lines = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        pts = [tuple(segs[i][0]), tuple(segs[i][1])]
        for forward in (True, False):
            while True:
                tip = pts[-1] if forward else pts[0]
                nxt = None
                for j in ends.get(key(tip), ()):
                    if not used[j]:
                        nxt = j
                        break
                if nxt is None:
                    break
                used[nxt] = True
                p, q = tuple(segs[nxt][0]), tuple(segs[nxt][1])
                far = q if key(p) == key(tip) else p
                if forward:
                    pts.append(far)
                else:
                    pts.insert(0, far)
        lines.append(pts)
    return lines


def pick_interval_ft(z_m, cell_px, base_ft=40.0, min_spacing_px=10.0):
    """Start from the wanted interval and coarsen while contours would be tighter than
    `min_spacing_px` on the page (judged from the 75th percentile slope of the land).
    `cell_px` = page pixels per height-grid cell."""
    zf = z_m * G.FT_PER_M
    gy, gx = np.gradient(zf)
    slope = np.hypot(gx, gy) / cell_px                      # ft per page pixel
    land = zf > 1.0
    if land.sum() < 16:
        return base_ft
    s = float(np.percentile(slope[land], 75))
    cands = [i for i in INTERVALS_FT if i >= base_ft] or [base_ft]
    for iv in cands:
        if s <= 0 or iv / s >= min_spacing_px:
            return float(iv)
    return float(cands[-1])


def contour_lines(z_m, interval_ft, index_every=5):
    """All contours: list of dicts {level_ft, index, pts[(x, y) grid units]}; level 0 skipped."""
    zf = z_m * G.FT_PER_M
    lo, hi = float(zf.min()), float(zf.max())
    out = []
    k = max(1, int(math.floor(lo / interval_ft)) + 1)
    while k * interval_ft <= hi:
        lvl = k * interval_ft
        segs = contour_segments(zf, lvl)
        for pts in chain(segs):
            out.append({"level_ft": lvl, "index": (k % index_every == 0), "pts": pts})
        k += 1
    return out
