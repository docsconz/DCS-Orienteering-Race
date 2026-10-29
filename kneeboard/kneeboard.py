#!/usr/bin/env python3
"""Kneeboard pages for the orienteering race, built from the real DCS Mission Editor map.

  python3 -I kneeboard/kneeboard.py plan    build/controls.json
  python3 -I kneeboard/kneeboard.py capture build/controls.json --cache kb_cache [--scratch]
  python3 -I kneeboard/kneeboard.py render  build/controls.json --cache kb_cache --out kb_out
  python3 -I kneeboard/kneeboard.py package kb_out --out MyRace_kneeboards

controls.json comes from extract.py.  See kneeboard/README.md.
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                                    # noqa: E402
from PIL import Image                                 # noqa: E402

import kb_capture as C                                # noqa: E402
import kb_geom as G                                   # noqa: E402
import kb_pages as P                                  # noqa: E402
import kb_render as R                                 # noqa: E402
import kb_stitch as S                                 # noqa: E402


def load_data(path):
    with open(path) as f:
        return json.load(f)


def page_options(a):
    return P.PageOptions(medium_w_m=a.medium_m, close_w_m=a.close_m, ultra_w_m=a.ultra_m,
                         close_below_m=a.close_below, ultra_below_m=a.ultra_below)


def cmd_plan(a):
    data = load_data(a.controls)
    pages = P.build_pages(data, page_options(a))
    pane = (0, 0, a.pane[0], a.pane[1])
    total = 0
    for line, p in zip(P.describe(pages), pages):
        n = len(S.plan_tiles(pane[2], pane[3]))
        total += n
        print("%s  %d tiles" % (line, n))
    print("%d pages, %d tiles, about %d min of capture" % (len(pages), total, round(total * 4.5 / 60) or 1))


def cmd_capture(a):
    data = load_data(a.controls)
    pages = P.build_pages(data, page_options(a))
    os.makedirs(a.cache, exist_ok=True)
    dcs = C.Dcs(cmd=a.exe.split("|"), scratch=os.path.join(a.cache, "scratch"))
    t0 = time.time()

    geo_path = os.path.join(a.cache, "geo.json")
    if os.path.exists(geo_path) and not a.refresh_geo:
        print("geo.json exists - keeping it (use --refresh-geo to redo; the race mission must be open)")
    else:
        print("Reading lat/lon and magnetic declination (the race mission must be the one open in the ME)...")
        rows = C.fetch_geo(dcs, R.required_points(data, pages))
        with open(geo_path, "w") as f:
            json.dump(rows, f)
        print("  declination at START: %+.2f deg" % rows[0]["decl"])

    if a.scratch:
        print("Opening a new empty %s mission for capture (your race mission is untouched; if it has "
              "unsaved edits the Mission Editor will refuse - save first)." % data["theatre"])
        dcs.new_scratch_mission(data["theatre"])
        time.sleep(4)

    print("Detecting the map pane...")
    pane = C.calibrate_pane(dcs, pages[0]["scale"], os.path.join(dcs.scratch, "kb_pane.png"))
    print("  pane %dx%d at (%d, %d)" % (pane[2], pane[3], pane[0], pane[1]))
    if pane[2] < 900 or pane[3] < 600:
        sys.exit("map pane is small - enlarge the DCS window (windowed, nothing overlapping it)")
    drop_stale(a.cache, pane, pages)
    with open(os.path.join(a.cache, "plan.json"), "w") as f:
        json.dump({"pane": pane, "pages": pages}, f, indent=1)

    only = set(a.only.split(",")) if a.only else None
    if a.airfields:
        return airfield_pass(a, dcs, pages, pane, only, t0)
    base_dir = os.path.join(a.cache, "base")
    h_dir = os.path.join(a.cache, "heights")
    os.makedirs(base_dir, exist_ok=True)
    os.makedirs(h_dir, exist_ok=True)
    for i, page in enumerate(pages, 1):
        if only and page["id"] not in only:
            continue
        print("[%d/%d] %s  (scale %d)" % (i, len(pages), page["id"], page["scale"]))
        frame = G.Frame(page["cn"], page["ce"], page["scale"])
        out_png = os.path.join(base_dir, page["id"] + ".png")
        if not (os.path.exists(out_png) and not a.recapture):
            got = C.capture_page_tiles(dcs, page, frame, pane, os.path.join(a.cache, "tiles"),
                                       settle=a.settle, reuse=not a.recapture, again=a.again)
            tiles = [t for t, _ in got]
            page_img, shifts, covered = S.compose([im for _, im in got], tiles)
            if not covered:
                print("  WARNING: page not fully covered by tiles")
            if any(s != (0, 0) for s in shifts):
                print("  tile alignment corrected by %s px" % (shifts,))
            Image.fromarray(page_img).save(out_png)
        h_path = os.path.join(h_dir, page["id"] + ".npz")
        if not a.no_contours and not (os.path.exists(h_path) and not a.recapture):
            spec = C.height_grid_spec(frame)
            z = C.fetch_heights(dcs, spec)
            np.savez_compressed(h_path, z=z, **{k: np.float64(v) for k, v in spec.items()})
    print("Capture done in %.1f min.  Next: render (works offline, no DCS needed)." % ((time.time() - t0) / 60))
    if a.scratch:
        print("Reopen your race mission in the Mission Editor when you want to edit it again.")


def drop_stale(cache, pane, pages):
    """Delete cached pictures of pages whose position or zoom changed since they were captured
    (or all of them if the map pane size changed), so an old capture is never reused by mistake."""
    pp = os.path.join(cache, "plan.json")
    try:
        with open(pp) as f:
            old = json.load(f)
    except (OSError, ValueError):
        return
    old_pages = {p["id"]: p for p in old.get("pages", [])}
    same_pane = list(old.get("pane", [])) == list(pane)
    for p in pages:
        o = old_pages.get(p["id"])
        if o is not None and same_pane and o["scale"] == p["scale"] and \
                abs(o["cn"] - p["cn"]) < 0.5 and abs(o["ce"] - p["ce"]) < 0.5:
            continue
        gone = []
        for d, pat in (("base", p["id"] + ".png"), ("base_af", p["id"] + ".png"),
                       ("heights", p["id"] + ".npz")):
            gone.append(os.path.join(cache, d, pat))
        for d in ("tiles", "tiles_af"):
            td = os.path.join(cache, d)
            if os.path.isdir(td):
                gone += [os.path.join(td, n) for n in os.listdir(td) if n.startswith(p["id"] + "_t")]
        hit = [g for g in gone if os.path.exists(g)]
        if hit:
            print("  %s changed since it was last captured - discarding the old capture" % p["id"])
            for g in hit:
                os.remove(g)


def airfield_pass(a, dcs, pages, pane, only, t0):
    """Second capture, with the Mission Editor's AIRFIELDS layer ON, of the pages that show an airfield.

    The first pass is kept (its overview and all other pages stay free of radio-beacon cones);
    render uses the second-pass picture instead for the pages recaptured here."""
    af_path = os.path.join(a.cache, "airfields.json")
    if os.path.exists(af_path) and not a.refresh_airfields:
        with open(af_path) as f:
            airdromes = json.load(f)
    else:
        print("Reading the map's airfield positions...")
        airdromes = C.fetch_airdromes(dcs)
        with open(af_path, "w") as f:
            json.dump(airdromes, f, indent=1)
    print("  %d airfields on this map" % len(airdromes))
    near = C.pages_near_airfields(pages, airdromes, a.airfield_margin)
    if only:
        near = [n for n in near if n[0] in only]
    if not near:
        print("No page shows an airfield - nothing to do.")
        return
    print("%d pages show an airfield:" % len(near))
    for pid, name in near:
        print("  %-34s %s" % (pid, name))
    print("Make sure the AIRFIELDS layer is switched ON in the Mission Editor (radio-beacon cones off if "
          "there is a separate switch).")
    by_id = {p["id"]: p for p in pages}
    os.makedirs(os.path.join(a.cache, "base_af"), exist_ok=True)
    kept = 0
    for i, (pid, name) in enumerate(near, 1):
        page = by_id[pid]
        print("[%d/%d] %s  (scale %d)" % (i, len(near), pid, page["scale"]))
        frame = G.Frame(page["cn"], page["ce"], page["scale"])
        got = C.capture_page_tiles(dcs, page, frame, pane, os.path.join(a.cache, "tiles_af"),
                                   settle=a.settle, reuse=not a.recapture, again=a.again)
        tiles = [t for t, _ in got]
        page_img, shifts, covered = S.compose([im for _, im in got], tiles)
        first = os.path.join(a.cache, "base", pid + ".png")
        if os.path.exists(first):
            with Image.open(first) as im:
                if not C.layer_changed(np.asarray(im.convert("RGB")), page_img):
                    print("  WARNING: identical to the first pass - the AIRFIELDS layer does not look switched "
                          "on.  Not saved.")
                    continue
        Image.fromarray(page_img).save(os.path.join(a.cache, "base_af", pid + ".png"))
        kept += 1
    print("Airfield pass done in %.1f min: %d pages saved.  Next: render." % ((time.time() - t0) / 60, kept))


def load_heights(cache, pid):
    p = os.path.join(cache, "heights", pid + ".npz")
    if not os.path.exists(p):
        return None
    d = np.load(p)
    spec = {"n0": float(d["n0"]), "e0": float(d["e0"]), "step": float(d["step"]),
            "nx": int(d["nx"]), "ny": int(d["ny"])}
    return d["z"], spec


def cmd_render(a):
    data = load_data(a.controls)
    with open(os.path.join(a.cache, "plan.json")) as f:
        pages = json.load(f)["pages"]
    geo = None
    gp = os.path.join(a.cache, "geo.json")
    if os.path.exists(gp):
        with open(gp) as f:
            geo = R.GeoTable(json.load(f))
    else:
        print("WARNING: no geo.json - bearings will be GRID, not magnetic")
    only = set(a.only.split(",")) if a.only else None
    big = os.path.join(a.out, "1536x2048")
    small = os.path.join(a.out, "768x1024")
    os.makedirs(big, exist_ok=True)
    os.makedirs(small, exist_ok=True)
    for page in pages:
        if only and page["id"] not in only:
            continue
        bp = os.path.join(a.cache, "base", page["id"] + ".png")
        af = os.path.join(a.cache, "base_af", page["id"] + ".png")
        if os.path.exists(af) and not a.no_airfield_pass and page["kind"] != "overview":
            bp = af                                   # captured with the AIRFIELDS layer on
        if not os.path.exists(bp):
            print("skip %s (not captured)" % page["id"])
            continue
        base = np.asarray(Image.open(bp).convert("RGB"))
        img = R.render_page(base, page, data, heights=load_heights(a.cache, page["id"]), geo=geo,
                            base_contour_ft=a.contour_ft, spokes=not a.no_spokes)
        img.save(os.path.join(big, page["id"] + ".png"), optimize=True)
        img.resize((768, 1024), Image.LANCZOS).save(os.path.join(small, page["id"] + ".png"), optimize=True)
        print("wrote", page["id"])


INSTALL_TXT = """Kneeboard pages for the orienteering race
==========================================

{n} pages, {size} pixels, 3:4 portrait.  They sort by file name, which is the order they appear
on the kneeboard: 00_overview first, then each control (a = medium view, b = close, c = ultra).

Bearings are MAGNETIC, distances nautical miles, contours in feet MSL.

Using them (DCS reads PNG files from either place):
  * whole mission, every aircraft: add the PNGs to the .miz under KNEEBOARD/IMAGES/
    (a .miz is a zip file)
  * one aircraft only: Saved Games/DCS/Kneeboard/<aircraft folder>/
"""


def cmd_package(a):
    src = os.path.join(a.images, a.size)
    if not os.path.isdir(src):
        sys.exit("no %s folder in %s - run render first" % (a.size, a.images))
    names = sorted(n for n in os.listdir(src) if n.lower().endswith(".png"))
    if not names:
        sys.exit("no PNGs in %s" % src)
    out = a.out
    if os.path.isdir(out):
        old = [n for n in os.listdir(out) if n.lower().endswith(".png")]
        if old and not a.replace:
            sys.exit("%s already holds %d PNGs - use --replace to overwrite them" % (out, len(old)))
        for n in old:
            os.remove(os.path.join(out, n))
    os.makedirs(out, exist_ok=True)
    for n in names:
        shutil.copyfile(os.path.join(src, n), os.path.join(out, n))
    with open(os.path.join(out, "INSTALL.txt"), "w") as f:
        f.write(INSTALL_TXT.format(n=len(names), size=a.size))
    print("wrote %d pages (%s) to %s" % (len(names), a.size, out))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_pages(p):
        p.add_argument("--medium-m", type=float, default=3700.0, help="width of medium pages, metres")
        p.add_argument("--close-m", type=float, default=900.0)
        p.add_argument("--ultra-m", type=float, default=220.0)
        p.add_argument("--close-below", type=float, default=1500.0,
                       help="zones smaller than this (m) also get a close page")
        p.add_argument("--ultra-below", type=float, default=50.0,
                       help="zones smaller than this (m) also get an ultra page")

    p = sub.add_parser("plan", help="list pages and tile counts (offline)")
    p.add_argument("controls")
    p.add_argument("--pane", type=int, nargs=2, default=[1630, 981], metavar=("W", "H"))
    add_pages(p)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("capture", help="drive the Mission Editor and save map pages + heights")
    p.add_argument("controls")
    p.add_argument("--cache", default="kb_cache")
    p.add_argument("--exe", default="dcs-sms.exe", help="dcs-sms command (default: dcs-sms.exe)")
    p.add_argument("--scratch", action="store_true", help="switch to a new empty mission on the same map first")
    p.add_argument("--settle", type=float, default=2.0, help="seconds to let the map redraw")
    p.add_argument("--again", type=float, default=0.8, help=argparse.SUPPRESS)
    p.add_argument("--only", help="comma-separated page ids")
    p.add_argument("--recapture", action="store_true")
    p.add_argument("--refresh-geo", action="store_true")
    p.add_argument("--no-contours", action="store_true")
    p.add_argument("--airfields", action="store_true",
                   help="second pass: recapture only the pages that show an airfield, with the ME's "
                        "AIRFIELDS layer switched ON")
    p.add_argument("--refresh-airfields", action="store_true", help="re-read the airfield list")
    p.add_argument("--airfield-margin", type=float, default=1500.0,
                   help="metres around a page within which an airfield counts as shown")
    add_pages(p)
    p.set_defaults(fn=cmd_capture)

    p = sub.add_parser("render", help="draw overlays and write the final PNGs (offline)")
    p.add_argument("controls")
    p.add_argument("--cache", default="kb_cache")
    p.add_argument("--out", default="kb_out")
    p.add_argument("--contour-ft", type=float, default=10.0)
    p.add_argument("--no-spokes", action="store_true", help="overview: no START-to-control lines")
    p.add_argument("--only")
    p.add_argument("--no-airfield-pass", action="store_true", help="ignore pages captured with --airfields")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("package", help="copy the finished pages into one tidy folder")
    p.add_argument("images", help="the --out folder of render")
    p.add_argument("--out", default="kb_package")
    p.add_argument("--size", default="1536x2048", choices=["1536x2048", "768x1024"])
    p.add_argument("--replace", action="store_true", help="overwrite PNGs already in the folder")
    p.set_defaults(fn=cmd_package)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
