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
        top, bot = P.HEADER_PX, P.overview_table_px(3)
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
            img[:, 700:703] = 0                                   # vertical rail through the centre column
            img[540:543, :1000] = 0                               # horizontal rail through the centre row
            img[300:900, 600:604] = (0, 0, 0)
            self.assertEqual(S.detect_pane(img), (58, 71, 1630, 981))

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
