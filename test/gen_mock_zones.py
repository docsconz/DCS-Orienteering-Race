#!/usr/bin/env python3
"""Write a Lua file returning env.mission-shaped zone data from a .miz (for the harness)."""
import sys, zipfile, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import extract

def emit(v):
    if isinstance(v, dict):
        return "{" + ",".join("[%s]=%s" % (emit(k), emit(x)) for k, x in v.items()) + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        import json; return json.dumps(v)
    return repr(v)

miz, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(miz) as z:
    m = extract.parse_mission(z.read("mission").decode())
open(out, "w").write("return " + emit({"triggers": {"zones": m["triggers"]["zones"]}}))
