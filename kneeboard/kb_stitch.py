"""Tile planning and stitching.

A page is PAGE_W x PAGE_H pixels at the same scale as the ME map, so each Mission Editor
screenshot (the "pane") is copied straight onto the page with an integer pixel offset - no
resampling.  Because `me camera focus` centres the requested point on the pane, a tile whose
pane top-left lands at page pixel (tx, ty) is captured by focusing on the world position of the
pane centre.  Anything the ME draws as fixed screen furniture (the scale bar bottom-left) is
excluded by an "unusable" margin around the pane, and overlapping tiles are cross-faded by
always taking the pixel from the tile in which it is furthest from that tile's edge.
"""
import math

import numpy as np

import kb_geom as G

# Colours of the Mission Editor frame (sampled from real screenshots).
UI_COLOURS = {(45, 62, 80), (26, 35, 45), (0, 0, 0), (0, 2, 3)}

# Pixels of each pane that we never use (left, top, right, bottom): the ME scale bar lives at
# the bottom-left, its label above it; ~90 px covers both.
DEFAULT_MARGINS = (0, 0, 0, 90)
OVERLAP_PX = 48
BLEED_PX = 16          # tiles are planned to cover this much beyond the page edge


def _edge(img, fixed, start, step, axis, limit, run=3):
    """Walk from `start` along one row (axis 0) or column (axis 1) until the first position where
    `run` consecutive pixels are all Mission Editor chrome colours; returns the last map index."""
    pos = start
    while 0 <= pos + step * run < limit:
        ok = True
        for k in range(1, run + 1):
            q = pos + step * k
            px = img[fixed, q] if axis == 0 else img[q, fixed]
            if tuple(int(v) for v in px[:3]) not in UI_COLOURS:
                ok = False
                break
        if ok:
            return pos
        pos += step
    return pos


def _mode(vals):
    best = max(set(vals), key=lambda v: (vals.count(v), -abs(v)))
    return best


def detect_pane(img):
    """Find the map pane inside a full ME screenshot (numpy HxWx3).  -> (x, y, w, h).

    Railways and other map lines are drawn in pure black, the same colour as part of the window
    frame, so a single scan can stop on a rail line.  Instead the edge is searched along several
    rows/columns around the centre, requiring a run of chrome-coloured pixels, and the most common
    answer wins."""
    h, w = img.shape[:2]
    cx, cy = w // 2, h // 2
    rows = [cy + d for d in (-300, -200, -100, 0, 100, 200, 300) if 0 <= cy + d < h]
    cols = [cx + d for d in (-500, -375, -250, -125, 0, 125, 250, 375, 500) if 0 <= cx + d < w]
    x0 = _mode([_edge(img, r, cx, -1, 0, w) for r in rows])
    x1 = _mode([_edge(img, r, cx, +1, 0, w) for r in rows])
    y0 = _mode([_edge(img, c, cy, -1, 1, h) for c in cols])
    y1 = _mode([_edge(img, c, cy, +1, 1, h) for c in cols])
    if x1 - x0 < 100 or y1 - y0 < 100:
        raise ValueError("could not find the map pane - is DCS minimised or covered?")
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def ui_fraction(crop):
    """Share of pixels in `crop` that are exactly the Mission Editor's panel colour.  The map itself
    never uses that colour, so anything above ~0.2% means a dialog/panel/toolbar is on the map."""
    c = np.asarray(crop)[..., :3]
    return float(((c[..., 0] == 45) & (c[..., 1] == 62) & (c[..., 2] == 80)).mean())


def plan_tiles(pane_w, pane_h, page_w=G.PAGE_W, page_h=G.PAGE_H,
               margins=DEFAULT_MARGINS, overlap=OVERLAP_PX, bleed=BLEED_PX):
    """Positions (tx, ty) of each pane's top-left in page pixels so the usable parts cover the page
    (plus `bleed` pixels all round, so a small registration shift can't expose the page edge)."""
    ul, ut, ur, ub = margins
    page_w, page_h = page_w + 2 * bleed, page_h + 2 * bleed
    uw, uh = pane_w - ul - ur, pane_h - ut - ub
    if uw <= overlap or uh <= overlap:
        raise ValueError("map pane too small for tiling")

    def positions(page, usable, lead):
        if usable >= page:
            return [-(usable - page) // 2 - lead - bleed]
        n = int(math.ceil((page - overlap) / float(usable - overlap)))
        step = (page - usable) / float(n - 1)
        return [int(round(i * step)) - lead - bleed for i in range(n)]

    xs = positions(page_w, uw, ul)
    ys = positions(page_h, uh, ut)
    tiles = []
    for j, ty in enumerate(ys):
        for i, tx in enumerate(xs):
            tiles.append({"k": len(tiles), "row": j, "col": i, "tx": tx, "ty": ty})
    return tiles


def tile_focus(frame, tile, pane_w, pane_h):
    """World (north, east) to focus on so the pane lands at (tx, ty) on this page."""
    px = tile["tx"] + pane_w / 2.0
    py = tile["ty"] + pane_h / 2.0
    return frame.to_world(px, py)


def _gray(a):
    return a[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _phase_shift(ref, mov):
    """Integer (dy, dx) by which `mov` content must be moved to line up with `ref`; + confidence."""
    h, w = ref.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    a = (ref - ref.mean()) * win
    b = (mov - mov.mean()) * win
    fa, fb = np.fft.rfft2(a), np.fft.rfft2(b)
    cp = fa * np.conj(fb)
    cp /= np.maximum(np.abs(cp), 1e-9)
    r = np.fft.irfft2(cp, s=ref.shape)
    idx = int(np.argmax(r))
    py, px = divmod(idx, w)
    dy = py if py <= h // 2 else py - h
    dx = px if px <= w // 2 else px - w
    conf = float(r.max() / (np.abs(r).mean() + 1e-9))
    return dy, dx, conf


def _mean_abs_diff(a, b, dy, dx):
    """Mean |a - b| where b is moved by (dy, dx) (compared on the common area)."""
    h, w = a.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    aa = a[ys0:ys1, xs0:xs1]
    bb = b[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    return float(np.abs(aa - bb).mean())


def estimate_shifts(imgs, tiles, margins=DEFAULT_MARGINS, max_shift=12, min_conf=12.0):
    """Residual integer (dx, dy) per tile, from overlap with an already-placed neighbour.

    Normally all zero (the focus is exact).  If the ME rounds the camera position, textured
    overlaps expose the error and it is corrected here.  Featureless overlaps (open sea) have no
    reliable signal and are left alone.
    """
    ul, ut, ur, ub = margins
    shifts = [(0, 0)] * len(tiles)
    placed = []
    for k, t in enumerate(tiles):
        best = None
        for j in placed:
            u = tiles[j]
            ox0 = max(t["tx"] + ul, u["tx"] + shifts[j][0] + ul)
            ox1 = min(t["tx"] + imgs[k].shape[1] - ur, u["tx"] + shifts[j][0] + imgs[j].shape[1] - ur)
            oy0 = max(t["ty"] + ut, u["ty"] + shifts[j][1] + ut)
            oy1 = min(t["ty"] + imgs[k].shape[0] - ub, u["ty"] + shifts[j][1] + imgs[j].shape[0] - ub)
            area = max(0, ox1 - ox0) * max(0, oy1 - oy0)
            if area > 0 and (best is None or area > best[0]):
                best = (area, j, ox0, ox1, oy0, oy1)
        placed.append(k)
        if best is None or best[2 + 1] - best[2] < 96 or best[5] - best[4] < 96:
            continue
        _, j, ox0, ox1, oy0, oy1 = best
        u = tiles[j]
        # the same page rectangle seen through each tile (tile k at its nominal position)
        a = imgs[j][oy0 - (u["ty"] + shifts[j][1]): oy1 - (u["ty"] + shifts[j][1]),
                    ox0 - (u["tx"] + shifts[j][0]): ox1 - (u["tx"] + shifts[j][0])]
        b = imgs[k][oy0 - t["ty"]: oy1 - t["ty"], ox0 - t["tx"]: ox1 - t["tx"]]
        ga, gb = _gray(a), _gray(b)
        if ga.std() < 4.0 or gb.std() < 4.0:
            continue
        dy, dx, conf = _phase_shift(ga, gb)
        if conf >= min_conf and abs(dx) <= max_shift and abs(dy) <= max_shift and (dx or dy):
            e0 = _mean_abs_diff(ga, gb, 0, 0)
            e1 = _mean_abs_diff(ga, gb, dy, dx)
            if e0 > 0.05 and e1 < 0.5 * e0:           # real improvement, not texture noise
                shifts[k] = (dx, dy)
    return shifts


def compose(imgs, tiles, page_w=G.PAGE_W, page_h=G.PAGE_H, margins=DEFAULT_MARGINS,
            refine=True, fill=(200, 200, 200)):
    """Stitch cropped pane images onto the page.  Returns (page ndarray, shifts, coverage_ok)."""
    ul, ut, ur, ub = margins
    shifts = estimate_shifts(imgs, tiles, margins) if refine and len(tiles) > 1 \
        else [(0, 0)] * len(tiles)
    out = np.empty((page_h, page_w, 3), np.uint8)
    out[:] = fill
    best = np.full((page_h, page_w), -1, np.int32)
    for img, t, (dx, dy) in zip(imgs, tiles, shifts):
        h, w = img.shape[:2]
        x0, y0 = t["tx"] + dx, t["ty"] + dy                  # pane origin on the page
        ux0, ux1, uy0, uy1 = x0 + ul, x0 + w - ur, y0 + ut, y0 + h - ub
        cx0, cx1 = max(ux0, 0), min(ux1, page_w)
        cy0, cy1 = max(uy0, 0), min(uy1, page_h)
        if cx0 >= cx1 or cy0 >= cy1:
            continue
        xs = np.arange(cx0, cx1)[None, :]
        ys = np.arange(cy0, cy1)[:, None]
        margin = np.minimum(np.minimum(xs - ux0, ux1 - 1 - xs), np.minimum(ys - uy0, uy1 - 1 - ys))
        sub = best[cy0:cy1, cx0:cx1]
        take = margin > sub
        src = img[cy0 - y0: cy1 - y0, cx0 - x0: cx1 - x0, :3]
        dst = out[cy0:cy1, cx0:cx1]
        dst[take] = src[take]
        sub[take] = margin[take]
    covered = bool((best >= 0).all())
    return out, shifts, covered
