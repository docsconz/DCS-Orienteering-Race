"""Tests for the kneeboard tool, using a mock dcs-sms (no DCS needed).

    python3 -I test/test_kneeboard.py          (needs numpy and pillow)
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(os.path.dirname(HERE), "kneeboard")
sys.path.insert(0, KB)

import numpy as np                                    # noqa: E402
from PIL import Image                                 # noqa: E402

import kb_capture as C                                # noqa: E402
import kb_contour as K                                # noqa: E402
import kb_geom as G                                   # noqa: E402
import kb_pages as P                                  # noqa: E402
import kb_render as R                                 # noqa: E402
import kb_stitch as S                                 # noqa: E402
import mock_dcs_sms as M                              # noqa: E402

# a small course on the mock world's land (values chosen to cover quad + circle, big + tiny)
DATA = {
    "theatre": "Caucasus",
    "start": {"x": -289500.0, "z": 619000.0, "vertices": [
        {"x": -289700.0, "z": 618800.0}, {"x": -289700.0, "z": 619200.0},
        {"x": -289300.0, "z": 619200.0}, {"x": -289300.0, "z": 618800.0}]},
    "controls": [
        {"num": 1, "name": "Under Rail Bridge", "shape": "quad", "x": -288800.0, "z": 621600.0, "points": 4,
         "max_ft": 100.0, "min_ft": None, "ref": "AGL",
         "vertices": [{"x": -288820.0, "z": 621540.0}, {"x": -288830.0, "z": 621560.0},
                      {"x": -288780.0, "z": 621660.0}, {"x": -288770.0, "z": 621640.0}]},
        {"num": 2, "name": "Tiny Pad", "shape": "circle", "x": -291000.0, "z": 623000.0, "points": 9,
         "radius_m": 15.0, "min_ft": 1500.0, "max_ft": None, "ref": "MSL"},
        {"num": 3, "name": "Big Field", "shape": "circle", "x": -287000.0, "z": 616500.0, "points": 7,
         "radius_m": 900.0, "min_ft": None, "max_ft": None, "ref": "AGL"},
    ],
    "tour": {"distance_nm": 20.0, "control_order": [1, 2, 3]},
}


def mock_cmd():
    return [sys.executable, "-I", os.path.join(KB, "mock_dcs_sms.py")]


class Geometry(unittest.TestCase):
    def test_scale_is_linear_and_invertible(self):
        self.assertAlmostEqual(G.mpp(500), 500 / 3500.0)
        self.assertEqual(G.scale_for_mpp(G.mpp(12345)), 12345)

    def test_frame_roundtrip(self):
        f = G.Frame(-290000.0, 614000.0, 6000)
        n, e = f.to_world(*f.to_px(-289000.0, 615500.0))
        self.assertAlmostEqual(n, -289000.0, 6)
        self.assertAlmostEqual(e, 615500.0, 6)
        px, py = f.to_px(-290000.0, 614000.0)
        self.assertEqual((px, py), (G.PAGE_W / 2.0, G.PAGE_H / 2.0))

    def test_north_is_up_east_is_right(self):
        f = G.Frame(0, 0, 3500)                       # 1 m per pixel
        self.assertEqual(f.to_px(100, 0), (768.0, 924.0))
        self.assertEqual(f.to_px(0, 100), (868.0, 1024.0))

    def test_bearings(self):
        self.assertAlmostEqual(G.grid_bearing_deg((0, 0), (0, 10)), 90.0)
        self.assertAlmostEqual(G.grid_bearing_deg((0, 0), (-10, 0)), 180.0)
        self.assertAlmostEqual(G.true_azimuth_deg(0, 0, 0, 1), 90.0, 4)
        self.assertAlmostEqual(G.true_azimuth_deg(10, 20, 11, 20), 0.0, 4)
        self.assertAlmostEqual(G.magnetic_deg(100.0, 6.5), 93.5)
        self.assertAlmostEqual(G.magnetic_deg(3.0, 6.5), 356.5)

    def test_nice_length(self):
        self.assertEqual(G.nice_length_m(740), 500)
        self.assertEqual(G.nice_length_m(1999), 1000)
        self.assertEqual(G.nice_length_m(2000), 2000)


class Pages(unittest.TestCase):
    def test_page_set_follows_zone_size(self):
        pages = P.build_pages(DATA)
        ids = [p["id"] for p in pages]
        self.assertEqual(ids[0], "00_overview")
        self.assertEqual(ids, sorted(ids))                       # kneeboard order = file-name order
        self.assertIn("01a_Under_Rail_Bridge_medium", ids)
        self.assertIn("01b_Under_Rail_Bridge_close", ids)
        self.assertIn("02c_Tiny_Pad_ultra", ids)                 # 30 m zone -> ultra
        self.assertNotIn("03b_Big_Field_close", ids)             # 1.8 km zone -> medium only

    def test_overview_contains_everything(self):
        page = P.build_pages(DATA)[0]
        f = G.Frame(page["cn"], page["ce"], page["scale"])
        top, bot = P.HEADER_PX, P.FOOTER_PX
        for z in [DATA["start"]] + DATA["controls"]:
            x, y = f.to_px(*G.zone_centre(z))
            self.assertTrue(0 < x < G.PAGE_W and top < y < G.PAGE_H - bot, (x, y))

    def test_big_zone_widens_medium_page(self):
        p = [q for q in P.build_pages(DATA) if q["id"] == "03a_Big_Field_medium"][0]
        self.assertGreaterEqual(p["width_m"], 1800 * 2.2 * 0.99)


class Stitching(unittest.TestCase):
    def test_tiles_cover_page_with_bleed(self):
        for pane in ((1630, 981), (1280, 720), (1900, 1000)):
            tiles = S.plan_tiles(*pane)
            xs = sorted({t["tx"] for t in tiles})
            ys = sorted({t["ty"] for t in tiles})
            ub = S.DEFAULT_MARGINS[3]
            self.assertLessEqual(xs[0], -S.BLEED_PX)
            self.assertLessEqual(ys[0], -S.BLEED_PX)
            self.assertGreaterEqual(xs[-1] + pane[0], G.PAGE_W + S.BLEED_PX)
            self.assertGreaterEqual(ys[-1] + pane[1] - ub, G.PAGE_H + S.BLEED_PX)
            for a, b in zip(ys, ys[1:]):                          # usable parts overlap
                self.assertGreater(a + pane[1] - ub - b, S.OVERLAP_PX - 2)

    def test_detect_pane_on_mock_screenshot(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["MOCK_STATE"] = os.path.join(d, "s.json")
            fn = os.path.join(d, "s.png")
            M.screenshot(fn, {"n": 0, "e": 0, "scale": 500})
            self.assertEqual(S.detect_pane(np.asarray(Image.open(fn).convert("RGB"))), (58, 71, 1630, 981))

    def test_black_map_lines_do_not_fool_pane_detection(self):
        # railways are pure black on the real map - the same colour as part of the window frame
        with tempfile.TemporaryDirectory() as d:
            fn = os.path.join(d, "s.png")
            M.screenshot(fn, {"n": 0, "e": 0, "scale": 500})
            img = np.asarray(Image.open(fn).convert("RGB")).copy()
            img[:, 700:710] = 0                                   # thick vertical rail, full height
            img[540:550, :1000] = 0                               # thick horizontal rail through the centre
            self.assertEqual(S.detect_pane(img), (58, 71, 1630, 981))

    def test_pane_found_by_scanning_when_layout_differs(self):
        # a different window/UI size: the standard offsets fail, the line scan must still work
        img = np.zeros((900, 1500, 3), np.uint8)
        img[:] = (45, 62, 80)
        img[100:820, 90:1400] = (100, 150, 100)                   # pane x 90..1399, y 100..819
        img[100:820, 89] = (26, 35, 45)
        img[100:820, 1400:] = (0, 0, 0)
        img[820, 90:1400] = (0, 2, 3)
        img[300:310, 90:1400] = 0                                 # rails inside the map
        img[100:820, 700:706] = 0
        self.assertEqual(S.detect_pane(img), (90, 100, 1310, 720))

    def test_panel_over_the_map_is_detected(self):
        img = np.zeros((981, 1630, 3), np.uint8)
        img[:] = (100, 150, 100)
        self.assertEqual(S.ui_fraction(img), 0.0)
        img[300:700, 0:500] = (45, 62, 80)                    # a docked panel / dialog
        self.assertGreater(S.ui_fraction(img), 0.002)

    def _stitch(self, scale, rounding):
        fr = G.Frame(-289500.5, 619000.5, scale)
        tiles = S.plan_tiles(1630, 981)
        imgs, n0 = [], None
        for t in tiles:
            n, e = S.tile_focus(fr, t, 1630, 981)
            if rounding:
                n, e = round(n), round(e)
            if n0 is None:
                n0 = (n, e)
            w, h = 1630, 981
            u = np.arange(w) + 0.5 - w / 2.0
            v = np.arange(h) + 0.5 - h / 2.0
            east, north = np.broadcast_arrays(e + u[None, :] * fr.mpp, n - v[:, None] * fr.mpp)
            img = M.world_rgb(north, east)
            img[914:944, 51:189] = (255, 0, 255)                   # the ME scale bar
            imgs.append(img)
        page, shifts, covered = S.compose(imgs, tiles)
        return fr, tiles, page, shifts, covered, n0

    def _truth(self, fr, dn, de):
        ii, jj = np.meshgrid(np.arange(G.PAGE_W), np.arange(G.PAGE_H))
        n = fr.cn - (jj + 0.5 - G.PAGE_H / 2.0) * fr.mpp + dn
        e = fr.ce + (ii + 0.5 - G.PAGE_W / 2.0) * fr.mpp + de
        return M.world_rgb(n, e)

    def test_exact_focus_gives_pixel_perfect_page(self):
        for scale in (500, 2000, 35000):
            fr, tiles, page, shifts, covered, _ = self._stitch(scale, False)
            self.assertTrue(covered)
            self.assertTrue(all(s == (0, 0) for s in shifts), shifts)
            self.assertTrue((page == self._truth(fr, 0, 0)).all(), scale)
            self.assertFalse(((page == (255, 0, 255)).all(axis=2)).any(), "scale bar leaked in")

    def test_registration_fixes_quantised_focus(self):
        # the ME rounding the camera to whole metres: tiles drift by up to ~4 px at scale 500
        fr, tiles, page, shifts, covered, n0 = self._stitch(500, True)
        self.assertTrue(covered)
        fn0, fe0 = S.tile_focus(fr, tiles[0], 1630, 981)
        # relative to tile 0 (which sets the page's own offset) every seam must be exact
        truth = self._truth(fr, n0[0] - fn0, n0[1] - fe0)
        self.assertTrue((page == truth).all())
        self.assertTrue(any(s != (0, 0) for s in shifts))


class Contours(unittest.TestCase):
    def test_cone_gives_circles_of_the_right_radius(self):
        y, x = np.mgrid[0:200, 0:200]
        z = np.maximum(0, 80 - np.hypot(x - 100, y - 100))
        lines = K.contour_lines(z, 40.0)
        self.assertEqual(len(lines), 6)
        for ln in lines:
            self.assertEqual(ln["pts"][0], ln["pts"][-1])                 # closed
        l40 = [l for l in lines if l["level_ft"] == 40.0][0]
        r = np.mean([math.hypot(px - 100, py - 100) for px, py in l40["pts"]])
        self.assertAlmostEqual(r, 80 - 40 / G.FT_PER_M, 1)
        self.assertEqual(sum(l["index"] for l in lines), 1)               # every 5th = 200 ft

    def test_flat_ground_has_no_contours(self):
        self.assertEqual(K.contour_lines(np.full((50, 50), 5.0), 40.0), [])

    def test_interval_coarsens_in_steep_terrain(self):
        y, x = np.mgrid[0:100, 0:100]
        steep = x * 8.0                                                 # 8 m (26 ft) per cell
        self.assertGreater(K.pick_interval_ft(steep, cell_px=4.0), 40.0)
        gentle = x * 0.2
        self.assertEqual(K.pick_interval_ft(gentle, cell_px=4.0), 40.0)


def fake_geo(pages):
    rows = []
    for n, e in R.required_points(DATA, pages):
        la, lo = M.fake_latlon(n, e)
        rows.append({"n": n, "e": e, "lat": la, "lon": lo, "decl": M.DECL_DEG})
    return R.GeoTable(rows)


class Bearings(unittest.TestCase):
    def test_magnetic_includes_convergence_and_declination(self):
        pages = P.build_pages(DATA)
        geo = fake_geo(pages)
        a, b = G.zone_centre(DATA["start"]), (-289500.0, 620000.0)       # due east on the grid
        geo.rows.update(R.GeoTable([{"n": q[0], "e": q[1], "lat": M.fake_latlon(*q)[0],
                                     "lon": M.fake_latlon(*q)[1], "decl": M.DECL_DEG} for q in (a, b)]).rows)
        brg, nm, unit = R.leg(geo, a, b)
        self.assertEqual(unit, "M")
        self.assertAlmostEqual(brg, 90.0 + M.CONV_DEG - M.DECL_DEG, 0)    # mock: +5 deg convergence
        self.assertAlmostEqual(nm, 1000 / 1852.0, 3)

    def test_without_geo_falls_back_to_labelled_grid(self):
        brg, nm, unit = R.leg(None, (0, 0), (0, 1852.0))
        self.assertEqual((round(brg), round(nm, 3), unit), (90, 1.0, "G"))

    def test_mag_north_arrow(self):
        pages = P.build_pages(DATA)
        ang, decl = R.mag_north_angle(fake_geo(pages), pages[1])
        # the mock's lat/lon is a flat approximation, so allow a fraction of a degree
        self.assertAlmostEqual(ang, M.DECL_DEG - M.CONV_DEG, delta=0.5)
        self.assertAlmostEqual(decl, M.DECL_DEG, 6)


class Water(unittest.TestCase):
    def test_water_mask_finds_blue_areas_not_text(self):
        img = np.zeros((120, 160, 3), np.uint8)
        img[:] = (90, 120, 70)                         # land
        img[:, 80:] = (40, 95, 115)                    # sea
        img[10:13, 10:14] = (40, 40, 160)              # a few blue text pixels on land
        m = R.water_mask(img)
        self.assertTrue(m[60, 120] and not m[60, 30])
        self.assertFalse(m[11, 12])

    def test_contours_are_cut_at_the_shore(self):
        water = np.zeros((50, 100), bool)
        water[:, 50:] = True
        runs = R._runs_on_land([(x, 25) for x in range(0, 100, 5)], water)
        self.assertEqual(len(runs), 1)
        self.assertLess(max(p[0] for p in runs[0]), 50)


class Airfields(unittest.TestCase):
    PAGES = [{"id": "00_overview", "kind": "overview", "cn": 0.0, "ce": 0.0, "height_m": 1e6, "width_m": 1e6},
             {"id": "a", "kind": "medium", "cn": 0.0, "ce": 0.0, "height_m": 3000.0, "width_m": 2000.0},
             {"id": "b", "kind": "close", "cn": 20000.0, "ce": 0.0, "height_m": 1200.0, "width_m": 900.0}]

    def test_pages_near_an_airfield(self):
        ad = [{"id": "1", "name": "X", "n": 2000.0, "e": 500.0}]
        self.assertEqual([p for p, _ in C.pages_near_airfields(self.PAGES, ad)], ["a"])   # not overview, not b

    def test_layer_change_detection(self):
        a = np.zeros((100, 100, 3), np.uint8)
        b = a.copy()
        self.assertFalse(C.layer_changed(a, b))
        b[40:60, 40:60] = 200
        self.assertTrue(C.layer_changed(a, b))

    def test_second_pass_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, MOCK_STATE=os.path.join(d, "state.json"))
            ctl = os.path.join(d, "controls.json")
            with open(ctl, "w") as f:
                json.dump(DATA, f)
            cli = [sys.executable, "-I", os.path.join(KB, "kneeboard.py")]
            cache = os.path.join(d, "cache")
            common = ["--cache", cache, "--settle", "0", "--again", "0.02", "--only", "02c_Tiny_Pad_ultra",
                      "--exe", "|".join(mock_cmd())]
            r = subprocess.run(cli + ["capture", ctl] + common + ["--scratch"], capture_output=True,
                               text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # layer still off: the second pass must notice and save nothing
            r = subprocess.run(cli + ["capture", ctl, "--airfields"] + common, capture_output=True,
                               text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("02c_Tiny_Pad_ultra", r.stdout)
            self.assertIn("does not look switched on", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(cache, "base_af", "02c_Tiny_Pad_ultra.png")))
            # layer on: saved, and render uses it
            env2 = dict(env, MOCK_AIRFIELDS="1")
            r = subprocess.run(cli + ["capture", ctl, "--airfields", "--recapture"] + common, capture_output=True,
                               text=True, env=env2)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            af = os.path.join(cache, "base_af", "02c_Tiny_Pad_ultra.png")
            self.assertTrue(os.path.exists(af))
            with Image.open(af) as a2, Image.open(os.path.join(cache, "base", "02c_Tiny_Pad_ultra.png")) as a1:
                self.assertTrue(C.layer_changed(np.asarray(a1.convert("RGB")), np.asarray(a2.convert("RGB"))))


class Stale(unittest.TestCase):
    def test_changed_zoom_discards_old_capture(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, MOCK_STATE=os.path.join(d, "state.json"))
            ctl = os.path.join(d, "controls.json")
            with open(ctl, "w") as f:
                json.dump(DATA, f)
            cli = [sys.executable, "-I", os.path.join(KB, "kneeboard.py")]
            cache = os.path.join(d, "cache")
            common = ["--cache", cache, "--settle", "0", "--again", "0.02", "--only", "02c_Tiny_Pad_ultra",
                      "--exe", "|".join(mock_cmd())]
            r = subprocess.run(cli + ["capture", ctl] + common + ["--scratch"], capture_output=True,
                               text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tile = os.path.join(cache, "tiles", "02c_Tiny_Pad_ultra_t0.png")
            self.assertTrue(os.path.exists(tile))
            r = subprocess.run(cli + ["capture", ctl] + common + ["--ultra-m", "400"], capture_output=True,
                               text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("02c_Tiny_Pad_ultra changed since it was last captured", r.stdout)


class Bake(unittest.TestCase):
    def test_pages_added_to_miz_with_backup(self):
        import zipfile
        import kneeboard as KBM
        with tempfile.TemporaryDirectory() as d:
            miz = os.path.join(d, "Race.miz")
            with zipfile.ZipFile(miz, "w") as z:
                z.writestr("mission", "mission = {}")
                z.writestr("KNEEBOARD/IMAGES/mine.png", b"keep me")
                z.writestr("KNEEBOARD/IMAGES/00_overview.png", b"old")
            imgs = os.path.join(d, "pages")
            os.makedirs(imgs)
            for n in ("00_overview.png", "01a_X_medium.png"):
                Image.new("RGB", (8, 8), (1, 2, 3)).save(os.path.join(imgs, n))
            with open(os.path.join(imgs, "INSTALL.txt"), "w") as f:
                f.write("x")
            n, bak = KBM.bake_into_miz(miz, imgs)
            self.assertEqual(n, 2)
            self.assertTrue(os.path.exists(bak))
            with zipfile.ZipFile(miz) as z:
                names = set(z.namelist())
                self.assertEqual(names, {"mission", "KNEEBOARD/IMAGES/mine.png", "KNEEBOARD/IMAGES/00_overview.png",
                                         "KNEEBOARD/IMAGES/01a_X_medium.png"})
                self.assertEqual(z.read("mission"), b"mission = {}")
                self.assertEqual(z.read("KNEEBOARD/IMAGES/mine.png"), b"keep me")
                self.assertNotEqual(z.read("KNEEBOARD/IMAGES/00_overview.png"), b"old")
            with zipfile.ZipFile(bak) as z:                    # the backup is the untouched original
                self.assertEqual(z.read("KNEEBOARD/IMAGES/00_overview.png"), b"old")
            n2, bak2 = KBM.bake_into_miz(miz, imgs)            # a second run never overwrites a backup
            self.assertNotEqual(bak, bak2)


class OneCommand(unittest.TestCase):
    def test_go_runs_every_step(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, MOCK_STATE=os.path.join(d, "state.json"))
            ctl = os.path.join(d, "controls.json")
            with open(ctl, "w") as f:
                json.dump(DATA, f)
            cli = [sys.executable, "-I", os.path.join(KB, "kneeboard.py")]
            out = os.path.join(d, "pages")
            r = subprocess.run(cli + ["go", ctl, "--yes", "--cache", os.path.join(d, "cache"),
                                      "--render-dir", os.path.join(d, "render"), "--out", out,
                                      "--settle", "0", "--again", "0.02", "--only", "02c_Tiny_Pad_ultra,00_overview",
                                      "--exe", "|".join(mock_cmd())], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for step in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "Done."):
                self.assertIn(step, r.stdout)
            self.assertEqual(sorted(os.listdir(out)), ["00_overview.png", "02c_Tiny_Pad_ultra.png", "INSTALL.txt"])
            # run again: nothing to redo, still fine (resumable)
            r = subprocess.run(cli + ["go", ctl, "--yes", "--cache", os.path.join(d, "cache"),
                                      "--render-dir", os.path.join(d, "render"), "--out", out,
                                      "--settle", "0", "--again", "0.02", "--only", "02c_Tiny_Pad_ultra,00_overview",
                                      "--exe", "|".join(mock_cmd())], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("keeping it", r.stdout)


class GoWithMiz(unittest.TestCase):
    def test_miz_in_folder_out_then_optional_bake(self):
        import shutil
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, MOCK_STATE=os.path.join(d, "state.json"))
            miz = os.path.join(d, "Race.miz")
            shutil.copyfile(os.path.join(os.path.dirname(HERE), "examples", "Orienteering_Test.miz"), miz)
            cli = [sys.executable, "-I", os.path.join(KB, "kneeboard.py")]
            args = ["go", miz, "--cache", os.path.join(d, "cache"), "--render-dir", os.path.join(d, "render"),
                    "--settle", "0", "--again", "0.02", "--only", "00_overview", "--exe", "|".join(mock_cmd())]
            # interactive: Enter at steps 1-3, then answer n to the bake question
            r = subprocess.run(cli + args, input="\n\n\nn\n", capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = os.path.join(d, "Race_kneeboards")
            self.assertTrue(os.path.exists(os.path.join(out, "00_overview.png")))
            self.assertIn("Also add these pages", r.stdout)
            self.assertIn("The .miz was not changed", r.stdout)
            with zipfile.ZipFile(miz) as z:
                self.assertNotIn("KNEEBOARD/IMAGES/00_overview.png", z.namelist())
            self.assertFalse(os.path.exists(miz + ".bak"))
            # answer y (via --bake): folder stays, the miz gains the page, a backup exists
            r = subprocess.run(cli + args + ["--yes", "--bake"], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(os.path.join(out, "00_overview.png")))
            self.assertTrue(os.path.exists(miz + ".bak"))
            with zipfile.ZipFile(miz) as z:
                self.assertIn("KNEEBOARD/IMAGES/00_overview.png", z.namelist())
                self.assertIn("mission", z.namelist())


class EndToEnd(unittest.TestCase):
    def test_capture_render_package(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, MOCK_STATE=os.path.join(d, "state.json"))
            ctl = os.path.join(d, "controls.json")
            with open(ctl, "w") as f:
                json.dump(DATA, f)
            cli = [sys.executable, "-I", os.path.join(KB, "kneeboard.py")]
            cache, out = os.path.join(d, "cache"), os.path.join(d, "out")
            only = "02c_Tiny_Pad_ultra,00_overview"
            r = subprocess.run(cli + ["capture", ctl, "--cache", cache, "--scratch", "--settle", "0",
                                      "--again", "0.02", "--only", only,
                                      "--exe", "|".join(mock_cmd())],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("declination at START: +6.58", r.stdout)
            for pid in only.split(","):
                self.assertTrue(os.path.exists(os.path.join(cache, "base", pid + ".png")))
                self.assertTrue(os.path.exists(os.path.join(cache, "heights", pid + ".npz")))
            r = subprocess.run(cli + ["render", ctl, "--cache", cache, "--out", out],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with Image.open(os.path.join(out, "1536x2048", "02c_Tiny_Pad_ultra.png")) as big, \
                    Image.open(os.path.join(out, "768x1024", "02c_Tiny_Pad_ultra.png")) as small:
                self.assertEqual(big.size, (1536, 2048))
                self.assertEqual(small.size, (768, 1024))
                self.assertEqual(big.size[0] * 4, big.size[1] * 3)            # 3:4 as required
                self.assertFalse((np.asarray(big.convert("RGB")) == (255, 0, 255)).all(axis=2).any())

            pkg = os.path.join(d, "pkg")
            r = subprocess.run(cli + ["package", out, "--out", pkg], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(sorted(os.listdir(pkg)), ["00_overview.png", "02c_Tiny_Pad_ultra.png", "INSTALL.txt"])
            with Image.open(os.path.join(pkg, "00_overview.png")) as im:
                self.assertEqual(im.size, (1536, 2048))
            r = subprocess.run(cli + ["package", out, "--out", pkg], capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0)                       # won't silently overwrite
            r = subprocess.run(cli + ["package", out, "--out", pkg, "--size", "768x1024", "--replace"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with Image.open(os.path.join(pkg, "00_overview.png")) as im:
                self.assertEqual(im.size, (768, 1024))


if __name__ == "__main__":
    unittest.main(verbosity=2)
