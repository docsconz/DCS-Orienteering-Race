# DCS Orienteering Race

## Workflow

1. In the Mission Editor draw trigger zones:
   - `START` — quad-point zone (start + finish area).
   - Controls — circle or quad, named `## NAME Mx [minFT] [maxFT] [msl]`
     e.g. `01 Under Rail Bridge 1.5x max100`, `12 Peak 2x min1500 msl`.
     Points = round(straight-line nm from START centre × multiplier × points-per-unit).
     Altitude limits are feet, AGL unless `msl` is given.
2. Generate the script and check the summary table:
   `python3 -I extract.py MyMission.miz --out build --race-minutes 60 [--penalty N] [--points-per-unit 10] [--grace-minutes 5]`
   (`--penalty` defaults to 8 × points-per-unit.) It also prints the shortest route START → all controls → START in nm
   (exact up to 14 controls, heuristic above; centre-to-centre straight lines) and saves it in `controls.json`.
   Outputs `build/orienteering_race.lua` and `build/controls.json`.
3. In the mission add one trigger: `MISSION START` → `DO SCRIPT FILE orienteering_race.lua`
   (or load it with a `DO SCRIPT` that `dofile`s it). Start the mission **paused**; unpause at 00:00.
4. **Re-run step 2 whenever you move/rename/add a zone.** The script compares a hash of the zones
   with what it was generated from and shouts (every 30 s) if they differ.
5. After the event (or if the mission was ended early):
   `python3 -I parse_log.py "Saved Games/DCS/Logs/dcs.log" --out results.json`

Edit the class tables (`CLASS_BY_TYPE`, `BANNED_TYPES`) near the top of `race_template.lua` before generating.
Other defaults (arm distance, despawn delay, smoke colour, GM menu mode, ...) are in `DEFAULTS` there and can be
overridden from the generated `SETTINGS` block.

## Rules as implemented

- Race clock = mission time (0 at mission time 0), whenever the script loads. A `TIME MORE 1` trigger therefore works, but the script (and its map markers) only exists once the mission is unpaused.
- Control: once per player; the path between 1 Hz samples is tested against the zone, so fast jets can't skip it.
  Altitude limits are checked where the path is inside the zone.
- Finish: enter START after having been > 500 m (`arm_dist_m`) outside it. Penalty = P × (1 + whole minutes late);
  late = after race end. Finished players are told; aircraft are never destroyed (optional `despawn_delay_s` setting, off by default).
- Leaving your slot and spawning again (any aircraft) resets that player's score to 0 and lets them re-earn controls. Finished, DNF and DQ players are never reset. The player is told, and `resets` is recorded in the snapshots.
- Death/ejection before finishing: keeps points, status `DNF-died`, no rank. After finishing: normal.
- Broadcasts at 10 and 5 minutes before race end (`warn_before_end_s`; calls longer than the race are skipped).
- Finish message goes to the finisher only (set `announce_finish = true` to tell everyone).
- Hard stop (end + grace): unfinished players become `DQ`; final results written; timers stop.
- Ranking per class: net points, then earlier finish time; exact ties share a rank. Finishers rank above
  unfinished players on equal points. One joint scoreboard, podiums per class in the FINAL block.
- Scoreboard snapshot (`ORIENTEERING_SNAPSHOT {json}`) written to dcs.log on every control, finish, death,
  join/leave, halftime, race end, hard stop and GM request. `ORIENTEERING_FINAL` at the hard stop.

## Tests

`python3 -I extract.py X.miz --out build --penalty 10 --race-minutes 30`
`python3 -I test/gen_mock_zones.py X.miz build/mock_zones.lua`
`texlua test/run_tests.lua build/orienteering_race.lua build/mock_zones.lua build/dcs_test.log`

(`texlua` is a stand-in Lua 5.3; any Lua works. The mock DCS in `test/harness.lua` only exercises *our* logic —
it cannot prove how DCS itself behaves, hence the list below.)

## In-DCS test plan (not yet run — everything here is unverified until tried)

| # | Test | Pass condition |
|---|------|----------------|
| 1 | Dedicated server, mission loads paused | `ORIENTEERING: loaded, N controls, zone hash ...` in dcs.log |
| 2 | Client connects while paused | Sees START + all control markers on F10 |
| 3 | Client connects after unpause | Same markers |
| 4 | Server restart / mission rotation | Script and markers return each time |
| 5 | Nothing runs while paused | No "RACE STARTED" message until unpause; clock starts at 0 |
| 6 | Server F10 view options (map only / allies / myself) | Markers visible with the setting you'll run |
| 7 | First player slots in and out | No duplicate or missing markers |
| 8 | `DO SCRIPT FILE` on MISSION START vs inline `DO SCRIPT` | Both fire; note which is more reliable |
| 9 | Zone data: edit a zone, don't regenerate | Hash-mismatch warning appears |
| 10 | Geometry in-sim: slow helo and 400+ kt jet over a small circle and a narrow quad | Both score; low-altitude bridge pass scores, high pass doesn't |
| 11 | Quad corner coordinates | Smoke at the START corners; log shows `smoke placed at race time ...` at 00:00 and again every ~4:30 (also after the hard stop). A race + grace shorter than ~5 min used to stop this. |
| 12 | Finish flow | Armed after leaving; re-entry finishes; message shown; aircraft left alone |
| 13 | Death handling | Crash/eject/PILOT_DEAD before finish -> DNF; destroy-after-finish does not change status |
| 14 | Leave slot mid-race, spawn again | Score reset to 0, message shown, controls scoreable again; finished/dead players not reset |
| 15 | Game Master slot F10 | "Show Orienteering Scoreboard" visible to the GM; works (coalition menu — otherwise try `gm_menu="group"`) |
| 16 | Kill the server process mid-race | Last `ORIENTEERING_SNAPSHOT` present in dcs.log; parser reads it |
| 17 | Long log lines | Snapshot lines with a full field of players are not truncated by env.info |
| 18 | Unclassified / banned types | Slotting an unlisted type and a GPS type each produce the warning |
| 19 | Player-name edge cases | Non-ASCII names and duplicate names behave sensibly |
