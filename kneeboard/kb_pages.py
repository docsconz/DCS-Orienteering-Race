"""Decides which kneeboard pages exist and where each one is centred / how zoomed it is."""
import math

import kb_geom as G

HEADER_PX = 170        # reserved at the top of the overview page
ROW_PX = 40            # overview table row height
FOOTER_PX = 110        # kept clear at the bottom of the overview (scale bar)


class PageOptions:
    def __init__(self, medium_w_m=3700.0, close_w_m=900.0, ultra_w_m=220.0,
                 close_below_m=1500.0, ultra_below_m=50.0, overview_margin=0.12,
                 min_overview_m=1500.0):
        self.medium_w_m = medium_w_m
        self.close_w_m = close_w_m
        self.ultra_w_m = ultra_w_m
        self.close_below_m = close_below_m
        self.ultra_below_m = ultra_below_m
        self.overview_margin = overview_margin
        self.min_overview_m = min_overview_m


def _page(pid, kind, title, ctrl, cn, ce, width_m, size_m=None):
    scale = G.scale_for_mpp(width_m / float(G.PAGE_W))
    return {"id": pid, "kind": kind, "title": title, "ctrl": ctrl,
            "cn": cn, "ce": ce, "scale": scale,
            "width_m": G.mpp(scale) * G.PAGE_W,
            "height_m": G.mpp(scale) * G.PAGE_H}


def build_pages(data, opts=None):
    """data = controls.json contents.  Returns a list of page dicts in file-name order."""
    opts = opts or PageOptions()
    start = data["start"]
    ctrls = sorted(data["controls"], key=lambda c: c["num"])
    zones = [start] + ctrls

    # ---- overview ----
    mins_n = min(G.zone_extent(z)[0] for z in zones)
    maxs_n = max(G.zone_extent(z)[1] for z in zones)
    mins_e = min(G.zone_extent(z)[2] for z in zones)
    maxs_e = max(G.zone_extent(z)[3] for z in zones)
    h_m = max(maxs_n - mins_n, opts.min_overview_m / 2.0)
    w_m = max(maxs_e - mins_e, opts.min_overview_m / 2.0)
    top = HEADER_PX
    bottom = FOOTER_PX
    avail_h = G.PAGE_H - top - bottom
    k = 1.0 + 2.0 * opts.overview_margin
    need_mpp = max(w_m * k / G.PAGE_W, h_m * k / float(avail_h))
    scale = G.scale_for_mpp(need_mpp)
    m = G.mpp(scale)
    ext_cn = (mins_n + maxs_n) / 2.0
    ext_ce = (mins_e + maxs_e) / 2.0
    y_c = (top + G.PAGE_H - bottom) / 2.0
    cn = ext_cn + (y_c - G.PAGE_H / 2.0) * m
    pages = [{"id": "00_overview", "kind": "overview", "title": "Overview", "ctrl": None,
              "cn": cn, "ce": ext_ce, "scale": scale,
              "width_m": m * G.PAGE_W, "height_m": m * G.PAGE_H}]

    # ---- per control ----
    for c in ctrls:
        n, e = G.zone_centre(c)
        size = G.zone_size(c)
        safe = c["name"].replace(" ", "_")
        base = "%02d" % c["num"]
        title = "%s %s" % (base, c["name"])
        pages.append(_page("%sa_%s_medium" % (base, safe), "medium", title, c["num"], n, e,
                           max(opts.medium_w_m, size * 2.2), size))
        if size < opts.close_below_m:
            pages.append(_page("%sb_%s_close" % (base, safe), "close", title, c["num"], n, e,
                               max(opts.close_w_m, size * 2.2), size))
        if size < opts.ultra_below_m:
            pages.append(_page("%sc_%s_ultra" % (base, safe), "ultra", title, c["num"], n, e,
                               max(opts.ultra_w_m, size * 2.5), size))
    return pages


def describe(pages):
    out = []
    for p in pages:
        out.append("%-34s scale %-7d %6.2f m/px  covers %.2f x %.2f km" % (
            p["id"], p["scale"], G.mpp(p["scale"]), p["width_m"] / 1000.0, p["height_m"] / 1000.0))
    return out
