"""Talks to a running Mission Editor through the `dcs-sms` CLI (https://github.com/nielsvaes/dcs-sms).

Needs: dcs-sms.exe installed (ME-mod), DCS windowed and NOT minimised, DCS-SMS -> External execution ON.
Everything here is a thin wrapper; the exact CLI used is spelled out in each method so it can be
reproduced by hand.
"""
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

import kb_geom as G
import kb_stitch as S

try:
    from PIL import Image
except ImportError:                                   # pragma: no cover
    Image = None


class DcsError(RuntimeError):
    pass


GEO_LUA = """-- KB:geo
-- ARGS:%(args)s
local pts = %(pts)s
local mv = require('magvar')
local T = rawget(_G, 'Terrain') or rawget(_G, 'terrain')
local out = {}
for i, p in ipairs(pts) do
  local la, lo = T.convertMetersToLatLon(p[1], p[2])
  out[i] = { la, lo, math.deg(mv.get_mag_decl(la, lo)) }
end
return out
"""

HEIGHT_LUA = """-- KB:heights
-- ARGS:%(args)s
local n0, e0, step, nx, j0, j1 = %(n0).3f, %(e0).3f, %(step).4f, %(nx)d, %(j0)d, %(j1)d
local T = rawget(_G, 'Terrain') or rawget(_G, 'terrain')
local out, k = {}, 0
for j = j0, j1 - 1 do
  for i = 0, nx - 1 do
    k = k + 1
    out[k] = math.floor(T.GetHeight(n0 - j * step, e0 + i * step) * 100 + 0.5) / 100
  end
end
return out
"""


class Dcs:
    """`cmd` is the argv prefix, e.g. ["dcs-sms.exe"] (or the mock in tests)."""

    def __init__(self, cmd=("dcs-sms.exe",), scratch=".", log=print):
        self.cmd = list(cmd)
        self.scratch = scratch
        self.log = log
        os.makedirs(scratch, exist_ok=True)

    def run(self, args, timeout=90):
        try:
            p = subprocess.run(self.cmd + list(args), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except FileNotFoundError:
            raise DcsError("cannot run %r - pass --exe with the path to dcs-sms.exe" % self.cmd[0])
        if p.returncode != 0:
            raise DcsError("dcs-sms %s failed (%d): %s" % (" ".join(args[:3]), p.returncode,
                                                          (p.stderr or p.stdout).strip()))
        return p.stdout

    # dcs-sms me camera focus --x N --y E --scale S
    def focus(self, north, east, scale):
        out = _json(self.run(["me", "camera", "focus", "--x", "%.3f" % north,
                                   "--y", "%.3f" % east, "--scale", "%d" % scale]))
        if not out.get("return_value", out).get("ok", True):
            raise DcsError("camera focus refused: %s" % out)
        return out

    # dcs-sms screenshot --out FILE
    def screenshot(self, path):
        self.run(["screenshot", "--out", os.path.abspath(path)])
        return path

    # dcs-sms exec --target gui --file FILE
    def exec_lua(self, code, timeout=90):
        path = os.path.abspath(os.path.join(self.scratch, "kb_exec.lua"))
        with open(path, "w") as f:
            f.write(code)
        out = _json(self.run(["exec", "--target", "gui", "--file", path,
                                   "--timeout", "%ds" % timeout], timeout=timeout + 10))
        if not out.get("ok") or out.get("error"):
            raise DcsError("exec failed: %s" % (out.get("error") or out))
        return out["return_value"]

    # dcs-sms me file new --map NAME   (no --force: refuses if the open mission has unsaved edits)
    def new_scratch_mission(self, theatre):
        self.run(["me", "file", "new", "--map", theatre])


# ---------------------------------------------------------------------------------------------

def _lua_pts(pts):
    return "{" + ",".join("{%.3f,%.3f}" % p for p in pts) + "}"


def fetch_geo(dcs, points, chunk=200):
    """[(north, east)] -> [{'n','e','lat','lon','decl'}].  Declination is the open mission's date's."""
    out = []
    for i in range(0, len(points), chunk):
        part = points[i:i + chunk]
        res = dcs.exec_lua(GEO_LUA % {"args": json.dumps({"pts": part}), "pts": _lua_pts(part)})
        if len(res) != len(part):
            raise DcsError("geo: expected %d results, got %d" % (len(part), len(res)))
        for p, r in zip(part, res):
            out.append({"n": p[0], "e": p[1], "lat": r[0], "lon": r[1], "decl": r[2]})
    return out


def height_grid_spec(frame, min_step_m=5.0, px_per_sample=4.0, border_px=64):
    """Grid covering the page (plus a border) in north-down rows."""
    step = max(min_step_m, px_per_sample * frame.mpp)
    n_top, e_left = frame.to_world(-border_px, -border_px)
    n_bot, e_right = frame.to_world(frame.w + border_px, frame.h + border_px)
    nx = int(math.ceil((e_right - e_left) / step)) + 1
    ny = int(math.ceil((n_top - n_bot) / step)) + 1
    return {"n0": n_top, "e0": e_left, "step": step, "nx": nx, "ny": ny}


def fetch_heights(dcs, spec, max_values=4000, progress=None):
    """Terrain heights in metres, array [ny, nx], row 0 = northernmost."""
    nx, ny = spec["nx"], spec["ny"]
    rows = max(1, max_values // nx)
    z = np.empty((ny, nx), np.float32)
    for j0 in range(0, ny, rows):
        j1 = min(ny, j0 + rows)
        args = dict(spec, j0=j0, j1=j1)
        res = dcs.exec_lua(HEIGHT_LUA % dict(args, args=json.dumps(args)))
        if len(res) != (j1 - j0) * nx:
            raise DcsError("heights: expected %d values, got %d" % ((j1 - j0) * nx, len(res)))
        z[j0:j1] = np.asarray(res, np.float32).reshape(j1 - j0, nx)
        if progress:
            progress(j1, ny)
    return z


def _load(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _json(text):
    """The CLI prints one JSON object; tolerate a banner line before it."""
    return json.loads(text[text.index("{"):])


def stable_shot(dcs, path, pane, settle=2.0, again=0.8, tries=6):
    """Screenshot once the map has finished redrawing (two successive pane crops identical)."""
    time.sleep(settle)
    prev = None
    for _ in range(tries):
        dcs.screenshot(path)
        img = _load(path)
        x, y, w, h = pane
        crop = img[y:y + h, x:x + w]
        if prev is not None and crop.shape == prev.shape and np.array_equal(crop, prev):
            return img
        prev = crop
        time.sleep(again)
    return img


def calibrate_pane(dcs, scale, path):
    """Detect the map pane from one screenshot."""
    dcs.screenshot(path)
    return S.detect_pane(_load(path))


def capture_page_tiles(dcs, page, frame, pane, tiles_dir, settle=2.0, margins=S.DEFAULT_MARGINS,
                       log=print, reuse=True, again=0.8):
    """Capture (or reuse) all tiles for a page.  Returns [(tile, cropped ndarray)]."""
    os.makedirs(tiles_dir, exist_ok=True)
    px, py, pw, ph = pane
    tiles = S.plan_tiles(pw, ph, frame.w, frame.h, margins)
    out = []
    for t in tiles:
        fn = os.path.join(tiles_dir, "%s_t%d.png" % (page["id"], t["k"]))
        if reuse and os.path.exists(fn):
            out.append((t, _load(fn)))
            continue
        n, e = S.tile_focus(frame, t, pw, ph)
        dcs.focus(n, e, page["scale"])
        raw = os.path.join(dcs.scratch, "kb_raw.png")
        img = stable_shot(dcs, raw, pane, settle=settle, again=again)
        got = S.detect_pane(img)
        if (got[2], got[3]) != (pw, ph):
            raise DcsError("map pane changed size (%s -> %s); keep the DCS window fixed" %
                           (pane, got))
        crop = img[py:py + ph, px:px + pw]
        Image.fromarray(crop).save(fn)
        out.append((t, crop))
        log("  tile %d/%d  focus %.1f, %.1f" % (t["k"] + 1, len(tiles), n, e))
    return out
