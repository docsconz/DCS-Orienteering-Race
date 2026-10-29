"""A stand-in for dcs-sms.exe that fakes just enough of the Mission Editor for testing.

It renders a deterministic synthetic world (a pure function of north/east metres) into a screenshot
laid out like the real ME window, so a stitched page can be compared pixel-for-pixel with the same
world sampled directly.  Camera state lives in $MOCK_STATE.  MOCK_FOCUS_ROUND=1 rounds the focus to
whole metres, like an ME that quantised its camera, to exercise overlap registration.
It also fakes a projection with a 5 degree grid convergence and a +6.58 degree declination.
"""
import json
import math
import os
import re
import sys

import numpy as np

W, H = 1696, 1089
PANE = (58, 71, 1630, 981)
UI = (45, 62, 80)
CONV_DEG = 5.0
DECL_DEG = 6.58
AIRDROME = (-290800.0, 623100.0)


def terrain_height(n, e):
    n = np.asarray(n, np.float64)
    e = np.asarray(e, np.float64)
    h = 70 * np.sin(e / 4000.0 + 0.5) * np.cos(n / 3000.0) + 25 * np.sin(n / 900.0 + e / 1300.0)
    h = np.where(h < 0, 0.0, h + 5.0)
    return h


def world_rgb(n, e):
    """Colours for arrays of world (north, east) coordinates."""
    n = np.asarray(n, np.float64)
    e = np.asarray(e, np.float64)
    h = terrain_height(n, e)
    out = np.empty(n.shape + (3,), np.uint8)
    out[:] = (100, 150, 100)
    out[h <= 0] = (126, 185, 198)
    cx = np.floor(e / 9.0).astype(np.int64)
    cy = np.floor(n / 9.0).astype(np.int64)
    dots = ((cx * 73856093) ^ (cy * 19349663)) % 7 == 0
    out[dots & (h > 0)] = (55, 78, 22)
    if os.environ.get("MOCK_AIRFIELDS"):          # the AIRFIELDS layer: a grey apron round the airfield
        near = np.hypot(n - AIRDROME[0], e - AIRDROME[1]) < 150.0
        out[near] = (90, 90, 90)
    road = np.abs(np.sin((e * 0.8 + n * 0.3) / 140.0)) < 0.03
    out[road & (h > 0)] = (160, 70, 0)
    blk = (np.floor(e / 400.0) + np.floor(n / 400.0)) % 5 == 0
    out[blk & (h > 0) & ~road] = (200, 130, 0)
    return out


def state_path():
    return os.environ.get("MOCK_STATE", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      ".mock_state.json"))


def load_state():
    try:
        with open(state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"n": 0.0, "e": 0.0, "scale": 35000}


def save_state(s):
    with open(state_path(), "w") as f:
        json.dump(s, f)


def flag(argv, name, cast=str, default=None):
    if name in argv:
        return cast(argv[argv.index(name) + 1])
    return default


def screenshot(path, state):
    from PIL import Image
    img = np.empty((H, W, 3), np.uint8)
    img[:] = UI
    x0, y0, w, h = PANE
    mpp = state["scale"] / 3500.0
    u = np.arange(w) + 0.5 - w / 2.0
    v = np.arange(h) + 0.5 - h / 2.0
    east = state["e"] + u[None, :] * mpp
    north = state["n"] - v[:, None] * mpp
    east, north = np.broadcast_arrays(east, north)
    img[y0:y0 + h, x0:x0 + w] = world_rgb(north, east)
    img[y0:y0 + h, x0 - 1] = (26, 35, 45)
    img[y0:y0 + h, x0 + w] = (0, 0, 0)
    img[y0 + h, x0:x0 + w] = (0, 2, 3)
    # the ME draws its scale bar bottom-left of the pane - make it impossible to miss
    img[y0 + 914:y0 + 944, x0 + 51:x0 + 189] = (255, 0, 255)
    Image.fromarray(img).save(path)


def fake_latlon(n, e):
    t = math.radians(CONV_DEG)
    n2 = n * math.cos(t) - e * math.sin(t)
    e2 = n * math.sin(t) + e * math.cos(t)
    lat = 42.0 + n2 / 111000.0
    lon = 41.5 + e2 / (111000.0 * math.cos(math.radians(42.0)))
    return lat, lon


def do_exec(code):
    m = re.search(r"-- KB:(\w+)", code)
    a = re.search(r"-- ARGS:(.*)", code)
    if not m:
        return None
    args = json.loads(a.group(1)) if a else {}
    if m.group(1) == "geo":
        out = []
        for n, e in args["pts"]:
            la, lo = fake_latlon(n, e)
            out.append([la, lo, DECL_DEG])
        return out
    if m.group(1) == "airdromes":
        return [{"id": "1", "name": "Mock Field", "n": AIRDROME[0], "e": AIRDROME[1]}]
    if m.group(1) == "heights":
        nx = args["nx"]
        n = args["n0"] - (np.arange(args["j0"], args["j1"])[:, None]) * args["step"]
        e = args["e0"] + np.arange(nx)[None, :] * args["step"]
        n, e = np.broadcast_arrays(n, e)
        return np.round(terrain_height(n, e), 2).ravel().tolist()
    return None


def main(argv):
    if argv[:3] == ["me", "camera", "focus"]:
        s = load_state()
        n, e = flag(argv, "--x", float), flag(argv, "--y", float)
        if os.environ.get("MOCK_FOCUS_ROUND"):
            n, e = round(n), round(e)
        s.update(n=n, e=e, scale=flag(argv, "--scale", float))
        save_state(s)
        print(json.dumps({"ok": True, "return_value": {"ok": True, "x": n, "y": e, "scale": s["scale"]}}))
    elif argv[:1] == ["screenshot"]:
        screenshot(flag(argv, "--out"), load_state())
        print("captured")
    elif argv[:1] == ["exec"]:
        with open(flag(argv, "--file")) as f:
            res = do_exec(f.read())
        print(json.dumps({"ok": res is not None, "return_value": res, "error": None if res is not None else "mock: unsupported"}))
    elif argv[:3] == ["me", "file", "new"]:
        print(json.dumps({"ok": True, "return_value": {"ok": True}}))
    else:
        sys.stderr.write("mock: unsupported %r\n" % (argv,))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
