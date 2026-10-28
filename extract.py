#!/usr/bin/env python3
"""Orienteering race extractor/validator for DCS .miz files.

Reads trigger zones from a .miz, validates START + control zones, prints a
summary table, and writes controls.json (consumed later by the Lua generator
and the kneeboard pipeline).

Zone naming:
    START
    ## NAME Mx [minFT] [maxFT] [msl]      e.g. "07 Under Bridge 1.5x max100"

Usage: python3 -I extract.py MISSION.miz [--out DIR] [--points-per-unit N]
"""
import argparse, json, math, os, re, sys, zipfile

M_PER_NM = 1852.0
M_PER_FT = 0.3048


# ---------------------------------------------------------------- Lua parsing
class LuaParseError(Exception):
    pass


class _Lua:
    """Minimal parser for the Lua table literals DCS writes in `mission`."""

    def __init__(self, s):
        self.s, self.i = s, 0

    def ws(self):
        s = self.s
        while self.i < len(s):
            if s[self.i].isspace():
                self.i += 1
            elif s.startswith("--", self.i):
                j = s.find("\n", self.i)
                self.i = len(s) if j < 0 else j + 1
            else:
                break

    def value(self):
        self.ws()
        c = self.s[self.i]
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string(c)
        m = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?").match(self.s, self.i)
        if m:
            self.i = m.end()
            t = m.group()
            return float(t) if any(ch in t for ch in ".eE") else int(t)
        m = re.compile(r"[A-Za-z_]\w*").match(self.s, self.i)
        if m:
            self.i = m.end()
            return {"true": True, "false": False, "nil": None}.get(m.group(), m.group())
        raise LuaParseError("unexpected %r at %d" % (c, self.i))

    def string(self, q):
        self.i += 1
        out = []
        s = self.s
        while s[self.i] != q:
            if s[self.i] == "\\":
                self.i += 1
                out.append({"n": "\n", "t": "\t"}.get(s[self.i], s[self.i]))
            else:
                out.append(s[self.i])
            self.i += 1
        self.i += 1
        return "".join(out)

    def table(self):
        self.i += 1
        t, n = {}, 1
        while True:
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                return t
            if self.s[self.i] == "[":
                self.i += 1
                k = self.value()
                self.ws()
                self.i += 1  # ]
            else:
                m = re.compile(r"[A-Za-z_]\w*").match(self.s, self.i)
                if m and re.compile(r"\s*=(?!=)").match(self.s, m.end()):
                    k = m.group()
                    self.i = m.end()
                else:
                    k, n = n, n + 1
                    t[k] = self.value()
                    self.ws()
                    if self.s[self.i] in ",;":
                        self.i += 1
                    continue
            self.ws()
            if self.s[self.i] == "=":
                self.i += 1
            t[k] = self.value()
            self.ws()
            if self.s[self.i] in ",;":
                self.i += 1


def parse_mission(text):
    m = re.search(r"\bmission\s*=", text)
    if not m:
        raise LuaParseError("no 'mission =' assignment found")
    p = _Lua(text)
    p.i = m.end()
    return p.value()


# ------------------------------------------------------------- zone handling
CONTROL_RE = re.compile(r"^(\d+)\s+(.+)$")
MULT_RE = re.compile(r"^(\d+(?:\.\d+)?)x$", re.I)
ALT_RE = re.compile(r"^(min|max)(\d+(?:\.\d+)?)$", re.I)


def zone_geometry(z):
    """Return ('circle'|'quad', center(x,y), data). DCS: type 0 = circle, 2 = quad."""
    if z.get("type") == 2 and z.get("verticies"):
        v = [(p["x"], p["y"]) for _, p in sorted(z["verticies"].items())]
        cx = sum(p[0] for p in v) / len(v)
        cy = sum(p[1] for p in v) / len(v)
        return "quad", (cx, cy), v
    return "circle", (z["x"], z["y"]), z["radius"]


def parse_control_name(name):
    """-> (num, label, mult, min_ft, max_ft, msl) or raises ValueError."""
    m = CONTROL_RE.match(name.strip())
    if not m:
        raise ValueError("does not start with a control number")
    num, rest = int(m.group(1)), m.group(2).split()
    min_ft = max_ft = None
    msl = False
    while rest:
        tok = rest[-1]
        am = ALT_RE.match(tok)
        if tok.lower() == "msl":
            if msl:
                raise ValueError("duplicate 'msl'")
            msl = True
        elif am:
            if am.group(1).lower() == "min":
                if min_ft is not None:
                    raise ValueError("duplicate 'min'")
                min_ft = float(am.group(2))
            else:
                if max_ft is not None:
                    raise ValueError("duplicate 'max'")
                max_ft = float(am.group(2))
        else:
            break
        rest.pop()
    if not rest or not MULT_RE.match(rest[-1]):
        raise ValueError("missing multiplier like '1.5x' (after the name, before any min/max/msl)")
    mult = float(MULT_RE.match(rest.pop()).group(1))
    if mult <= 0:
        raise ValueError("multiplier must be > 0")
    if not rest:
        raise ValueError("missing control name")
    if min_ft is not None and max_ft is not None and min_ft > max_ft:
        raise ValueError("min altitude is above max altitude")
    if msl and min_ft is None and max_ft is None:
        raise ValueError("'msl' given with no min/max altitude")
    return num, " ".join(rest), mult, min_ft, max_ft, msl


def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def lua_round(v):  # matches the Lua side: math.floor(v + 0.5)
    return int(math.floor(v + 0.5))


def hash_str(text):
    """h = (h*257 + byte) mod 2147483629 -- trivially reproducible in Lua 5.1."""
    h = 5381
    for b in text.encode("utf-8"):
        h = (h * 257 + b) % 2147483629
    return "%08x" % h


def zone_hash(zones):
    """Hash of everything the race depends on (name/type/geometry, 0.01 m).
    race_template.lua rebuilds the identical canonical string at runtime."""
    parts = []
    for z in sorted(zones, key=lambda z: z["name"]):
        kind, (cx, cy), data = zone_geometry(z)
        geo = ("%.2f,%.2f" % (cx, cy)) + (
            ",r%.2f" % data if kind == "circle" else "," + ";".join("%.2f,%.2f" % p for p in data)
        )
        parts.append("%s|%s|%s" % (z["name"], kind, geo))
    return hash_str("\n".join(parts))


def build(mission, ppu):
    errors, warnings = [], []
    zones = list(mission.get("triggers", {}).get("zones", {}).values())

    starts = [z for z in zones if z["name"].strip().upper() == "START"]
    if len(starts) != 1:
        errors.append("need exactly one START zone, found %d" % len(starts))
        return None, errors, warnings
    start = starts[0]
    kind, s_center, s_poly = zone_geometry(start)
    if kind != "quad" or len(s_poly) != 4:
        errors.append("START must be a quad-point zone")
        return None, errors, warnings

    controls, seen = [], {}
    for z in zones:
        if z is start:
            continue
        name = z["name"]
        if not CONTROL_RE.match(name.strip()):
            warnings.append("ignored zone %r (not START or a control)" % name)
            continue
        try:
            num, label, mult, mn, mx, msl = parse_control_name(name)
        except ValueError as e:
            errors.append("zone %r: %s" % (name, e))
            continue
        if num in seen:
            errors.append("duplicate control number %d (%r and %r)" % (num, seen[num], name))
            continue
        seen[num] = name
        ckind, center, data = zone_geometry(z)
        dist_m = math.hypot(center[0] - s_center[0], center[1] - s_center[1])
        dist_nm = dist_m / M_PER_NM
        pts = lua_round(dist_nm * mult * ppu)
        c = dict(
            num=num, name=label, zone_name=name, shape=ckind, x=center[0], z=center[1],
            multiplier=mult, dist_nm=dist_nm, points=pts,
            min_ft=mn, max_ft=mx, min_m=None if mn is None else mn * M_PER_FT,
            max_m=None if mx is None else mx * M_PER_FT, ref="MSL" if msl else "AGL",
        )
        if ckind == "circle":
            c["radius_m"] = data
        else:
            c["vertices"] = [{"x": p[0], "z": p[1]} for p in data]
        if point_in_poly(center, s_poly):
            warnings.append("control %d %r is inside START (scores ~0)" % (num, label))
        elif pts == 0:
            warnings.append("control %d %r is worth 0 points" % (num, label))
        elif dist_nm < 0.5:
            warnings.append("control %d %r is only %.2f nm from START" % (num, label, dist_nm))
        controls.append(c)

    controls.sort(key=lambda c: c["num"])
    for i, a in enumerate(controls):
        for b in controls[i + 1:]:
            if math.hypot(a["x"] - b["x"], a["z"] - b["z"]) < 1.0:
                warnings.append("controls %d and %d have the same position" % (a["num"], b["num"]))
    nums = [c["num"] for c in controls]
    if nums and nums != list(range(1, len(nums) + 1)):
        warnings.append("control numbers are not contiguous from 1: %s" % nums)
    if not controls:
        errors.append("no control zones found")

    used = [z for z in zones if z is start or z["name"] in seen.values()]
    out = dict(
        zone_hash=zone_hash(used), points_per_unit=ppu,
        zone_names=sorted(z["name"] for z in used), theatre=mission.get("theatre"),
        start=dict(x=s_center[0], z=s_center[1],
                   vertices=[{"x": p[0], "z": p[1]} for p in s_poly]),
        controls=controls,
    )
    return out, errors, warnings


def to_lua(v, ind=0):
    pad = "  " * (ind + 1)
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid Lua
    if isinstance(v, list):
        if not v:
            return "{}"
        return "{\n" + "".join(pad + to_lua(x, ind + 1) + ",\n" for x in v) + "  " * ind + "}"
    items = [(k, x) for k, x in v.items() if x is not None]
    return "{\n" + "".join("%s%s = %s,\n" % (pad, k, to_lua(x, ind + 1)) for k, x in items) + "  " * ind + "}"


def lua_data(r, settings):
    controls = []
    for c in r["controls"]:
        d = dict(num=c["num"], name=c["name"], zone=c["zone_name"], mult=c["multiplier"],
                 points=c["points"], dist_nm=round(c["dist_nm"], 3), shape=c["shape"], x=c["x"], z=c["z"],
                 min_ft=c["min_ft"], max_ft=c["max_ft"], min_m=c["min_m"], max_m=c["max_m"], ref=c["ref"])
        if c["shape"] == "circle":
            d["r"] = c["radius_m"]
        else:
            d["verts"] = c["vertices"]
        controls.append(d)
    data = dict(zone_hash=r["zone_hash"], theatre=r["theatre"], ppu=r["points_per_unit"],
                zone_names=r["zone_names"],
                start=dict(x=r["start"]["x"], z=r["start"]["z"], verts=r["start"]["vertices"]),
                controls=controls)
    return "local DATA = %s\nlocal SETTINGS = %s\n" % (to_lua(data), to_lua(settings))


def render_script(template_path, r, settings):
    t = open(template_path, encoding="utf-8").read()
    a, b = "-- >>> GENERATED BEGIN\n", "-- <<< GENERATED END"
    i, j = t.index(a) + len(a), t.index(b)
    return t[:i] + "-- zone hash %s, generated by extract.py\n" % r["zone_hash"] + lua_data(r, settings) + t[j:]


# ------------------------------------------------------------ shortest tour
EXACT_MAX_CONTROLS = 14   # Held-Karp is exact; 2^14 states x 14 x 14 is a few seconds in Python


def _dist_matrix(pts):
    return [[math.hypot(a[0] - b[0], a[1] - b[1]) for b in pts] for a in pts]


def _tour_len(d, order):
    return sum(d[order[i]][order[i + 1]] for i in range(len(order) - 1))


def _held_karp(d):
    """Exact shortest closed tour from node 0 through every other node. Returns the visiting order."""
    m = len(d) - 1                       # controls are nodes 1..m
    INF = float("inf")
    dp = [[INF] * m for _ in range(1 << m)]
    parent = [[-1] * m for _ in range(1 << m)]
    for j in range(m):
        dp[1 << j][j] = d[0][j + 1]
    for mask in range(1, 1 << m):
        row = dp[mask]
        for j in range(m):
            cur = row[j]
            if cur == INF or not (mask >> j) & 1:
                continue
            for k in range(m):
                if (mask >> k) & 1:
                    continue
                nm = mask | (1 << k)
                v = cur + d[j + 1][k + 1]
                if v < dp[nm][k]:
                    dp[nm][k] = v
                    parent[nm][k] = j
    full = (1 << m) - 1
    best, last = INF, -1
    for j in range(m):
        v = dp[full][j] + d[j + 1][0]
        if v < best:
            best, last = v, j
    order, mask = [], full
    while last != -1:
        order.append(last + 1)
        prev = parent[mask][last]
        mask ^= 1 << last
        last = prev
    return [0] + order[::-1] + [0]


def _improve(d, order):
    """2-opt + or-opt until no improvement (order is a closed tour starting/ending at node 0)."""
    improved = True
    while improved:
        improved = False
        n = len(order)
        for i in range(1, n - 2):
            for j in range(i + 1, n - 1):
                a, b, c, e = order[i - 1], order[i], order[j], order[j + 1]
                if d[a][c] + d[b][e] < d[a][b] + d[c][e] - 1e-9:
                    order[i:j + 1] = order[i:j + 1][::-1]
                    improved = True
        for seg in (1, 2, 3):
            i = 1
            while i + seg <= len(order) - 1:
                piece = order[i:i + seg]
                rest = order[:i] + order[i + seg:]
                base = _tour_len(d, order)
                best_len, best_order = base, None
                for p in range(len(rest) - 1):
                    for cand in (piece, piece[::-1]):
                        trial = rest[:p + 1] + cand + rest[p + 1:]
                        L = _tour_len(d, trial)
                        if L < best_len - 1e-9:
                            best_len, best_order = L, trial
                if best_order:
                    order[:] = best_order
                    improved = True
                i += 1
    return order


def _heuristic(d, restarts=60):
    import random
    rng = random.Random(1)
    n = len(d)
    best, best_len = None, float("inf")
    for r in range(restarts):
        left = list(range(1, n))
        if r:
            rng.shuffle(left)
            order = [0] + left + [0]
        else:                               # first try: nearest neighbour
            order, cur = [0], 0
            while left:
                nxt = min(left, key=lambda k: d[cur][k])
                left.remove(nxt)
                order.append(nxt)
                cur = nxt
            order.append(0)
        order = _improve(d, order)
        L = _tour_len(d, order)
        if L < best_len - 1e-9:
            best, best_len = order[:], L
    return best


def shortest_tour(r):
    """Shortest closed route START -> every control -> START, measured centre to centre
    (straight lines, horizontal). Exact for up to EXACT_MAX_CONTROLS controls, heuristic above."""
    pts = [(r["start"]["x"], r["start"]["z"])] + [(c["x"], c["z"]) for c in r["controls"]]
    d = _dist_matrix(pts)
    exact = len(r["controls"]) <= EXACT_MAX_CONTROLS
    if len(pts) == 1:
        order = [0, 0]
    elif len(pts) == 2:
        order = [0, 1, 0]
    else:
        order = _held_karp(d) if exact else _heuristic(d)
    legs = [d[order[i]][order[i + 1]] / M_PER_NM for i in range(len(order) - 1)]
    names = ["START"] + ["%02d %s" % (c["num"], c["name"]) for c in r["controls"]]
    return dict(distance_nm=sum(legs), exact=exact, order=[names[i] for i in order],
                control_order=[r["controls"][i - 1]["num"] for i in order[1:-1]], legs_nm=legs)


def print_tour(t):
    kind = "exact" if t["exact"] else "approximate (heuristic search; not guaranteed shortest)"
    print("\nShortest route START -> all controls -> START: %.2f nm  [%s]" % (t["distance_nm"], kind))
    print("(straight lines between zone centres; ignores terrain, altitude and the size of the zones)")
    for i, leg in enumerate(t["legs_nm"]):
        print("  %-30s -> %-30s %7.2f nm" % (t["order"][i], t["order"][i + 1], leg))


def print_summary(r):
    print("Theatre: %s   zone hash: %s   points/nm unit: %g" % (r["theatre"], r["zone_hash"], r["points_per_unit"]))
    print("START center: x=%.1f z=%.1f (quad, 4 vertices)\n" % (r["start"]["x"], r["start"]["z"]))
    hdr = "%3s  %-22s %-6s %9s %6s %6s  %s" % ("#", "Name", "Shape", "Dist(nm)", "Mult", "Pts", "Altitude")
    print(hdr)
    print("-" * len(hdr) + "-----------")
    for c in r["controls"]:
        if c["min_ft"] is None and c["max_ft"] is None:
            alt = "-"
        else:
            lo = "" if c["min_ft"] is None else "min %g" % c["min_ft"]
            hi = "" if c["max_ft"] is None else "max %g" % c["max_ft"]
            alt = (lo + " " + hi).strip() + " ft " + c["ref"]
        size = ("r=%.0fm" % c["radius_m"]) if c["shape"] == "circle" else "quad"
        print("%3d  %-22s %-6s %9.2f %5.2fx %6d  %s" % (
            c["num"], c["name"], size, c["dist_nm"], c["multiplier"], c["points"], alt))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("miz")
    ap.add_argument("--out", default=".")
    ap.add_argument("--points-per-unit", type=float, default=1.0)
    ap.add_argument("--race-minutes", type=float, default=60, help="race length (late penalty starts here)")
    ap.add_argument("--penalty", type=int, default=None,
                    help="points lost per started minute late (default: 8 x points-per-unit)")
    ap.add_argument("--grace-minutes", type=float, default=5, help="DQ unfinished players this long after the end")
    ap.add_argument("--template", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "race_template.lua"))
    a = ap.parse_args()

    with zipfile.ZipFile(a.miz) as z:
        text = z.read("mission").decode("utf-8", errors="replace")
    r, errors, warnings = build(parse_mission(text), a.points_per_unit)
    if r:
        print_summary(r)
    print()
    for w in warnings:
        print("WARNING:", w)
    for e in errors:
        print("ERROR:  ", e)
    if errors:
        sys.exit(1)
    if a.penalty is None:
        a.penalty = int(round(8 * a.points_per_unit))
        print("Late penalty: %d per started minute (default 8 x points-per-unit)" % a.penalty)
    else:
        print("Late penalty: %d per started minute" % a.penalty)
    tour = shortest_tour(r)
    print_tour(tour)
    r["tour"] = tour
    settings = dict(race_end_s=a.race_minutes * 60, penalty=a.penalty, hard_stop_grace_s=a.grace_minutes * 60)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "controls.json"), "w") as f:
        json.dump(r, f, indent=2)
    lua = os.path.join(a.out, "orienteering_race.lua")
    with open(lua, "w", encoding="utf-8") as f:
        f.write(render_script(a.template, r, settings))
    print("\nwrote %s and %s" % (os.path.join(a.out, "controls.json"), lua))


if __name__ == "__main__":
    main()
