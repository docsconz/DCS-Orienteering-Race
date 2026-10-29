# Kneeboard pages

Builds 3:4 portrait kneeboard pages from the **real DCS Mission Editor map** (so terrain, rivers,
roads, forests and towns are exactly what players see in game), with contours, zone outlines, point
values and **magnetic** bearings/distances drawn on top.

Pages per mission (file-name order = kneeboard order):

| File | What |
|------|------|
| `00_overview` | START + every control with points, START→control lines, table of magnetic bearing/distance from START and to the two nearest controls, altitude limits, shortest-route length |
| `NNa_<name>_medium` | control in context, about 2.6 km across |
| `NNb_<name>_close` | about 900 m across — only for zones smaller than 300 m |
| `NNc_<name>_ultra` | about 220 m across — only for zones smaller than 50 m |

Every page is 1536x2048 (a 768x1024 copy is written too): the same 3:4 shape for every aircraft.

## Needs

* Windows PC with DCS and [`dcs-sms.exe`](https://github.com/nielsvaes/dcs-sms) installed (its Mission Editor mod; Mission Editor
  menu **DCS-SMS → External execution ON**).
* Python 3 with `pip install pillow numpy`.
* DCS **windowed**, not minimised, nothing covering the window, display scaling 100%, SRS overlay off.
  Do not resize the window during a run.

## Workflow

```
python3 -I extract.py MyMission.miz --out build            # already part of the race workflow
python3 -I kneeboard/kneeboard.py plan    build/controls.json
# open MyMission.miz in the Mission Editor, SAVE it, then:
python3 -I kneeboard/kneeboard.py capture build/controls.json --cache kb_cache --scratch
python3 -I kneeboard/kneeboard.py render  build/controls.json --cache kb_cache --out kb_out
python3 -I kneeboard/kneeboard.py package kb_out --out MyRace_kneeboards
```

* `capture` is the only step that needs DCS. It first reads the declination and lat/lon **while the
  race mission is open** (declination depends on the mission date), then with `--scratch` opens a new
  empty mission on the same map so no units, zone labels or SRS text end up in the images. It refuses
  (rather than discards) if the open mission has unsaved edits. Re-open your mission afterwards.
* It is resumable: tiles and finished pages in `--cache` are reused (`--recapture` forces a redo,
  `--only id1,id2` limits it).
* `render` is offline and takes seconds, so styling can be redone freely from the cache.
  `--contour-ft 10` sets the base interval; it is coarsened automatically on steep pages so lines stay
  at least ~14 px apart. Index lines (every 5th) are labelled in feet MSL. `--no-spokes` removes the
  START→control lines.
* `package` copies the finished pages into one clean folder (`--out`, default `kb_package`) with an
  `INSTALL.txt`. It writes nothing into your mission and never overwrites existing PNGs unless you pass
  `--replace`. `--size 768x1024` packages the smaller copies. DCS degrades oversized images, so check
  which size looks better in the cockpit. (A zip option can be added later.)

## How it works (measured on a real Mission Editor)

* Metres per pixel = `scale / 3500`, exactly, at scale 500, 2000, 10000 and 40000.
* `me camera focus --x N --y E --scale S` puts that point at the centre of the map pane (1630x981 px
  on a 1696x1089 window; found automatically from each screenshot).
* A page is the same scale as the ME, so screenshots are copied onto it with whole-pixel offsets, no
  resampling. About 3 overlapping tiles per page, each pixel taken from the tile where it is furthest
  from the tile edge. The ME scale bar (bottom-left of every screenshot) is excluded.
* If the ME quantises its camera, overlaps are cross-correlated and the tiles re-aligned.
* Heights come from `Terrain.GetHeight` via `dcs-sms exec --target gui` (about 1 ms per 1000 points).
* Bearings: DCS grid north is not true north (several degrees on the Caucasus map). Each leg's true azimuth
  comes from the ME's own lat/lon conversion, minus the ME's own `magvar` declination, so the result
  should match the F10 ruler in magnetic mode. The blue arrow on each page shows magnetic north relative to
  the page (pages are drawn grid-north-up).

## Tests

`python3 -I test/test_kneeboard.py` runs everything against `kneeboard/mock_dcs_sms.py`, a stand-in
`dcs-sms` that serves a synthetic world: stitched pages are compared pixel-for-pixel with the same world
sampled directly (including a camera that rounds to whole metres), contours against a cone of known
size, bearings against a fake projection with 5° convergence, and the whole capture → render → package path.

## Not yet verified in real DCS

The geometry above was measured on real screenshots and a real Mission Editor, and the overlay drawing was checked
against them (the zone quad of the sample mission lands on the rail bridge it was placed on). What the
mock cannot prove, and the first real run should check:

1. `me file new --map <theatre>` behaves (no dialog left open, map stays on the same theatre).
2. The height and lat/lon Lua snippets run in the ME (they use `Terrain.GetHeight`,
   `Terrain.convertMetersToLatLon`, `magvar.get_mag_decl`).
3. Page seams: look for ghosting where tiles join at the finest scales.
4. Magnetic bearings against the F10 ruler (magnetic mode) for a couple of legs.
5. Whether the ME draws anything else on top of the map that needs hiding.
6. Which image size DCS shows best on the kneeboard.
