#!/usr/bin/env python3
"""Check the exact tour solver against brute force, and the heuristic against the exact solver."""
import itertools, math, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import extract as e

def mk(n, rng):
    return dict(start=dict(x=0.0, z=0.0),
                controls=[dict(num=i + 1, name="C%d" % (i + 1), x=rng.uniform(-50000, 50000), z=rng.uniform(-50000, 50000))
                          for i in range(n)])

def brute(r):
    pts = [(r["start"]["x"], r["start"]["z"])] + [(c["x"], c["z"]) for c in r["controls"]]
    d = e._dist_matrix(pts)
    return min(e._tour_len(d, [0] + list(p) + [0]) for p in itertools.permutations(range(1, len(pts)))) / e.M_PER_NM

rng = random.Random(7)
bad = 0
for n in (1, 2, 3, 4, 5, 6, 7, 8):
    for _ in range(5):
        r = mk(n, rng)
        t = e.shortest_tour(r)
        ok = abs(t["distance_nm"] - brute(r)) < 1e-6 and t["exact"] and t["order"][0] == t["order"][-1] == "START" \
            and sorted(t["control_order"]) == list(range(1, n + 1))
        bad += not ok
        if not ok: print("MISMATCH n=%d: %.4f vs %.4f" % (n, t["distance_nm"], brute(r)))
print("exact vs brute force (n=1..8, 40 cases):", "OK" if not bad else "%d BAD" % bad)

# heuristic quality vs exact on 12 controls
gaps = []
for _ in range(10):
    r = mk(12, rng)
    ex = e.shortest_tour(r)["distance_nm"]
    pts = [(r["start"]["x"], r["start"]["z"])] + [(c["x"], c["z"]) for c in r["controls"]]
    d = e._dist_matrix(pts)
    h = e._tour_len(d, e._heuristic(d)) / e.M_PER_NM
    gaps.append((h - ex) / ex * 100)
print("heuristic vs exact, 12 controls x10: worst gap %.3f%%, mean %.3f%%" % (max(gaps), sum(gaps) / len(gaps)))
sys.exit(1 if bad else 0)
