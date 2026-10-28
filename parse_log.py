#!/usr/bin/env python3
"""Pull the race results out of dcs.log and write results.json.

Uses the ORIENTEERING_FINAL line if the race reached its hard stop; otherwise
the snapshot with the highest seq (the last scoring event before the mission
ended). Only the most recent mission run in the log is considered.

Usage: python3 -I parse_log.py dcs.log [--out results.json]
"""
import argparse, json, re, sys

LOADED = "ORIENTEERING: loaded"
FINAL_RE = re.compile(r"ORIENTEERING_FINAL (\{.*\})\s*$")
SNAP_RE = re.compile(r"ORIENTEERING_SNAPSHOT (\{.*\})\s*$")


def fmt(t):
    t = int(round(t))
    return "%d:%02d:%02d" % (t // 3600, t % 3600 // 60, t % 60) if t >= 3600 else "%02d:%02d" % (t // 60, t % 60)


def extract(lines):
    start = 0
    for i, ln in enumerate(lines):
        if LOADED in ln:
            start = i
    run = lines[start:]
    warnings = []
    if not any(LOADED in ln for ln in lines):
        warnings.append("no 'ORIENTEERING: loaded' line found; scanning the whole log")

    final = snaps = None
    snaps = []
    for ln in run:
        m = FINAL_RE.search(ln)
        if m:
            try:
                final = json.loads(m.group(1))
            except ValueError:
                warnings.append("a FINAL line could not be parsed (truncated?)")
            continue
        m = SNAP_RE.search(ln)
        if m:
            try:
                snaps.append(json.loads(m.group(1)))
            except ValueError:
                warnings.append("a SNAPSHOT line could not be parsed (truncated?); skipped")
    if final:
        return dict(source="final", race_t=final["t"], data=final), warnings
    if snaps:
        best = max(snaps, key=lambda s: s["seq"])
        return dict(source="snapshot", race_t=best["t"], seq=best["seq"], reason=best.get("reason"),
                    data=best), warnings
    return None, warnings + ["no results found in this log"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out", default="results.json")
    a = ap.parse_args()
    with open(a.log, encoding="utf-8", errors="replace") as f:
        res, warnings = extract(f.read().splitlines())
    for w in warnings:
        print("WARNING:", w)
    if not res:
        sys.exit(1)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    if res["source"] == "final":
        print("Using FINAL results (race hard stop reached), race time %s" % fmt(res["race_t"]))
    else:
        print("Mission ended early. Using snapshot #%d (%s) from race time %s -- scores are as of then."
              % (res["seq"], res["reason"], fmt(res["race_t"])))
    print("\n%-4s %-9s %-18s %6s %5s  %s" % ("Rank", "Class", "Player", "Net", "Pen", "Status"))
    for p in res["data"]["players"]:
        print("%-4s %-9s %-18s %6d %5d  %s%s" % (
            p.get("rank_in_class", "-"), p["class"], p["name"], p["net"], p["penalty"], p["status"],
            "" if p.get("finish_t") is None else "  finish " + fmt(p["finish_t"])))
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
