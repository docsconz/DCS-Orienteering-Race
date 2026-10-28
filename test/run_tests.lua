-- usage: texlua run_tests.lua GENERATED_SCRIPT.lua MOCK_ZONES.lua LOGFILE
local here = arg[0]:match("^(.*)/[^/]*$") or "."
package.path = here .. "/?.lua;" .. package.path
local H = require("harness")
local scriptPath, zonesPath, logPath = arg[1], arg[2], arg[3]
local logfile = io.open(logPath, "w")
local shortScript = arg[4]   -- optional: script generated with --race-minutes 8

local pass, fail = 0, 0
local function check(cond, label, extra)
  if cond then pass = pass + 1
  else fail = fail + 1; print("FAIL: " .. label .. (extra and (" -> " .. tostring(extra)) or "")) end
end
local function near(a, b, tol) return a ~= nil and math.abs(a - b) <= tol end

local function fresh() return H.new(scriptPath, zonesPath, logfile).load() end

-- geometry taken straight from the mock mission zones (independent of the script)
local zones = dofile(zonesPath).triggers.zones
local function zoneByPrefix(p)
  for _, z in pairs(zones) do if z.name:sub(1, #p) == p then return z end end
end
local function center(z)
  if z.type == 2 and z.verticies then
    local sx, sz = 0, 0
    for i = 1, #z.verticies do sx = sx + z.verticies[i].x; sz = sz + z.verticies[i].y end
    return sx / #z.verticies, sz / #z.verticies
  end
  return z.x, z.y
end
local SX, SZ = center(zoneByPrefix("START"))
local B = { center(zoneByPrefix("01")) }   -- bridge (quad, max 100 ft AGL)
local C2 = { center(zoneByPrefix("02")) }  -- circle r=30.48
local C3 = { center(zoneByPrefix("03")) }  -- island quad
local FAR = 6000
local END_S = 1800  -- generated with --race-minutes 30, --penalty 10, grace 5 min

-- A pass across the bridge, perpendicular to its long axis, `alt` metres above ground.
local bridgeDir = { -89.2 / 157.5, 129.8 / 157.5 }
local function bridgePass(W, u, altAGL)
  W.set(u, B[1] - bridgeDir[1] * 400, W.ground + altAGL, B[2] - bridgeDir[2] * 400)
  W.advance(1)
  W.set(u, B[1] + bridgeDir[1] * 400, W.ground + altAGL, B[2] + bridgeDir[2] * 400)
  W.advance(1)
end

-- ---------------------------------------------------------------- A: load / markers / smoke
do
  local W = fresh()
  check(W.ORI ~= nil, "A script loaded")
  check(W.logText():find("ORIENTEERING: loaded, 3 controls", 1, true), "A loaded log line")
  check(W.marks[9000] and W.marks[9000].text == "START / FINISH", "A start marker", W.marks[9000] and W.marks[9000].text)
  check(W.marks[9001] and W.marks[9001].text == "01 Under Rail Bridge (4 pts, max 100 ft AGL)", "A marker 1", W.marks[9001] and W.marks[9001].text)
  check(W.marks[9002] and W.marks[9002].text == "02 Intersection (11 pts)", "A marker 2", W.marks[9002] and W.marks[9002].text)
  check(W.marks[9003] and W.marks[9003].text == "03 Island (7 pts)", "A marker 3", W.marks[9003] and W.marks[9003].text)
  check(W.marks[9001].readOnly == true, "A markers read-only")
  check(near(W.marks[9002].pos.x, C2[1], 0.01) and W.marks[9002].pos.y == 100, "A marker position/ground height")
  check(#W.ORI.selfCheck() == 0, "A self-check clean", table.concat(W.ORI.selfCheck(), "; "))
  check(#W.smokes == 0, "A no smoke while paused")
  W.advance(1)
  check(#W.smokes == 4, "A smoke at 4 corners at T0", #W.smokes)
  check(W.smokes[1].color == 0, "A smoke colour green")
  W.advance(280)
  check(#W.smokes == 8, "A smoke refreshed once by ~280 s", #W.smokes)
  W.spawn("Probe", "F-5E-3", SX + FAR, 1000, SZ)
  local n = 0; for _ in pairs(W.marks) do n = n + 1 end
  check(n == 4, "A markers not duplicated by re-placement", n)
end

-- ---------------------------------------------------------------- A2: marker robustness, clock origin
do
  local W = fresh()
  W.spawn("P", "F-100D", SX + FAR, 1000, SZ); W.advance(40)
  check((W.removeCalls or 0) == 0, "A2 normal operation never removes marks", W.removeCalls)
  W.marks[9002] = nil                      -- simulate DCS silently dropping a mark
  W.advance(35)
  check(W.marks[9002] and W.marks[9002].text == "02 Intersection (11 pts)", "A2 dropped mark is re-placed by the self-check")
  check(W.ORI.buildBoard()[1].class == "JET", "A2 F-100D classified as JET")
  local function classOf(name, typ)
    W.spawn(name, typ, SX + FAR, 1000, SZ + 1000)
    for _, r in ipairs(W.ORI.buildBoard()) do if r.name == name then return r.class end end
  end
  check(classOf("m1", "Mirage-F1CE") == "JET", "A2 Mirage F1CE = JET")
  check(classOf("m2", "Mirage-F1BE") == "JET", "A2 Mirage F1BE = JET")
  check(classOf("m3", "Mirage-F1EE") == "JET_INS", "A2 Mirage F1EE = JET_INS")
  check(classOf("m4", "mirage f1ee") == "JET_INS", "A2 type names matched ignoring case/punctuation")
  check(classOf("m5", "Totally-Unknown") == "UNK", "A2 unknown type -> UNK")
  -- exact type names read from Orion_Test.miz
  local real = { ["F-100D"]="JET", ["F-14B"]="JET_INS", ["F-4E-45MC"]="JET_INS", ["F-5E-3"]="JET",
    ["F-86F Sabre"]="JET", ["M-2000C"]="JET_INS", ["MiG-21Bis"]="JET", ["MiG-29 Fulcrum"]="JET_INS",
    ["Mirage-F1BE"]="JET", ["Mirage-F1CE"]="JET", ["Mirage-F1EE"]="JET_INS" }
  local i = 0
  for typ, want in pairs(real) do
    i = i + 1
    check(classOf("real" .. i, typ) == want, "A2 real type " .. typ .. " -> " .. want)
  end
  -- no getMarkPanels: place once, never spam
  local W2 = H.new(scriptPath, zonesPath, nil); W2.noPanels = true; W2.load()
  W2.advance(100)
  check(W2.marks[9001] ~= nil, "A2 markers placed without getMarkPanels")
  -- loading late (e.g. a 'time more than 1' trigger) must not shift the race clock
  local W3 = H.new(scriptPath, zonesPath, nil); W3.now = 1.5; W3.load()
  W3.advance(10)
  check(W3.ORI.state.T0 == 0, "A2 race clock origin is mission time 0", W3.ORI.state.T0)
end

-- ---------------------------------------------------------------- A3: "minutes remaining" announcements
do
  local W = fresh()
  local function count(pat) local n = 0; for _, m in ipairs(W.msgs) do if m.to == "all" and m.text:find(pat) then n = n + 1 end end; return n end
  W.spawn("P", "F-100D", SX + FAR, 1000, SZ)
  W.advance(END_S - 600 - 5)
  check(count("MINUTES REMAINING") == 0, "A3 nothing before the 10-minute mark")
  W.advance(10)
  check(count("10 MINUTES REMAINING") == 1, "A3 10-minute announcement", count("10 MINUTES REMAINING"))
  W.advance(300)
  check(count("5 MINUTES REMAINING") == 1 and count("10 MINUTES REMAINING") == 1, "A3 5-minute announcement, no repeat")
  W.advance(100)
  check(count("MINUTES REMAINING") == 2, "A3 exactly two announcements overall", count("MINUTES REMAINING"))
  if shortScript then   -- an 8-minute race can only have the 5-minute call
    local W8 = H.new(shortScript, zonesPath, nil).load()
    W8.advance(8 * 60)
    local n10, n5 = 0, 0
    for _, m in ipairs(W8.msgs) do
      if m.text:find("10 MINUTES REMAINING", 1, true) then n10 = n10 + 1 end
      if m.text:find("5 MINUTES REMAINING", 1, true) then n5 = n5 + 1 end
    end
    check(n10 == 0 and n5 == 1, "A3 short race: only the 5-minute call", n10 .. "/" .. n5)
    W8.advance(8 * 60 + 5 * 60 + 60 - W8.now)   -- run past the hard stop (13 min)
    check(#W8.smokes >= 12, "A3 smoke still refreshing past 4:30 and past the hard stop", #W8.smokes)
  end
end

-- ---------------------------------------------------------------- B: fast jet through small circle
do
  local W = fresh()
  local j = W.spawn("Viper", "F-5E-3", C2[1] - 3000, 200, C2[2])
  check(W.ORI.buildBoard()[1].class == "JET", "B class JET")
  W.advance(2)
  W.set(j, C2[1] - 125, 200, C2[2]); W.advance(1)
  check(j and #W.msgsFor(j) == 0, "B no award before reaching zone")
  W.set(j, C2[1] + 125, 200, C2[2]); W.advance(1)   -- 250 m in one poll, zone is 61 m wide
  check(W.msgsFor(j):find("CONTROL 02 %- Intersection: %+11 pts"), "B 250 m step still catches 61 m circle", W.msgsFor(j))
  W.set(j, C2[1] - 125, 200, C2[2]); W.advance(1); W.set(j, C2[1] + 125, 200, C2[2]); W.advance(1)
  local _, count = W.msgsFor(j):gsub("CONTROL 02", "")
  check(count == 1, "B control awarded only once", count)
  check(W.ORI.buildBoard()[1].pts == 11, "B points = 11")
  -- near miss: pass 80 m abeam of a 30 m circle
  local j2 = W.spawn("Miss", "F-5E-3", C2[1] - 3000, 200, C2[2] + 80)
  W.advance(1); W.set(j2, C2[1] + 125, 200, C2[2] + 80); W.advance(1)
  W.set(j2, C2[1] - 125, 200, C2[2] + 80); W.advance(1)
  check(W.msgsFor(j2) == "", "B near miss not awarded")
end

-- ---------------------------------------------------------------- C: altitude bands
do
  local W = fresh()
  local hi = W.spawn("High", "F-5E-3", B[1], 400, B[2] + 5000)
  local lo = W.spawn("Low", "F-5E-3", B[1], 400, B[2] - 5000)
  W.advance(2)
  bridgePass(W, hi, 150)   -- 150 m AGL > 30.48 m ceiling
  check(W.msgsFor(hi) == "", "C 150 m AGL pass under bridge ceiling rejected", W.msgsFor(hi))
  bridgePass(W, lo, 20)    -- 20 m AGL
  check(W.msgsFor(lo):find("CONTROL 01"), "C 20 m AGL pass awarded", W.msgsFor(lo))
  bridgePass(W, hi, 15)
  check(W.msgsFor(hi):find("CONTROL 01"), "C later low pass awarded")
  -- MSL floor on island: patch the control table in place
  local isl
  for _, c in ipairs(W.ORI.ctrls()) do if c.num == 3 then isl = c end end
  isl.min_m, isl.ref, isl.hasAlt = 500, "MSL", true
  local m = W.spawn("Msl", "F-5E-3", C3[1] - 3000, 1000, C3[2])
  W.advance(2)
  W.set(m, C3[1] - 400, 300, C3[2]); W.advance(1); W.set(m, C3[1] + 400, 300, C3[2]); W.advance(1)
  check(not W.msgsFor(m):find("CONTROL 03"), "C MSL floor: 300 m rejected")
  W.set(m, C3[1] - 400, 700, C3[2]); W.advance(1); W.set(m, C3[1] + 400, 700, C3[2]); W.advance(1)
  check(W.msgsFor(m):find("CONTROL 03"), "C MSL floor: 700 m accepted", W.msgsFor(m))
end

-- ---------------------------------------------------------------- D: arming + finish
do
  local W = fresh()
  local h = W.spawn("Huey", "UH-1H", SX, 100, SZ)
  check(W.ORI.buildBoard()[1].class == "HELO", "D class HELO")
  W.advance(5)
  check(W.ORI.buildBoard()[1].status == "racing", "D sitting in START is not a finish")
  W.fly(h, SX, 100, SZ + 465 + 200, 30)   -- just outside the quad, < 500 m from edge
  W.fly(h, SX, 100, SZ, 30)               -- straight back through
  check(W.ORI.buildBoard()[1].status == "racing", "D hovering near START does not arm")
  W.fly(h, SX, 100, SZ + 1600, 60)        -- well clear
  W.fly(h, SX, 100, SZ - 1600, 120)       -- back through START
  local r = W.ORI.buildBoard()[1]
  check(r.status == "finished", "D finished after armed return", r.status)
  check(r.finishT and r.finishT > 160 and r.finishT < 175, "D finish time interpolated", r.finishT)
  check(r.penalty == 0, "D on-time: no penalty")
  local nfin = 0
  for _, m in ipairs(W.msgs) do if m.text:find("Huey FINISHED", 1, true) then nfin = nfin + 1 end end
  check(nfin == 1, "D finisher gets exactly ONE finish message", nfin)
  W.advance(25)
  check(h.alive and not h.destroyed, "D aircraft is never destroyed")
  W.advance(3600)
  check(h.alive and not h.destroyed, "D still not destroyed after hard stop")
  check(W.ORI.buildBoard()[1].status == "finished", "D status stays finished")
end

-- ---------------------------------------------------------------- E: late penalty
do
  local W = fresh()
  local function lateFinisher(name, atTime)
    local u = W.spawn(name, "UH-1H", SX, 100 + 0, SZ + FAR)
    return u, atTime
  end
  local a = W.spawn("OnTime", "UH-1H", SX, 100, SZ + FAR)
  local b = W.spawn("Late10s", "UH-1H", SX, 100, SZ + FAR)
  local c = W.spawn("Late65s", "UH-1H", SX, 100, SZ + FAR)
  W.advance(1790)
  W.set(a, SX, 100, SZ - FAR); W.advance(5)          -- crosses START around t=1790..1795
  W.advance(10)
  W.set(b, SX, 100, SZ - FAR); W.advance(15)         -- ~1805..1810
  W.advance(50)
  W.set(c, SX, 100, SZ - FAR); W.advance(15)         -- ~1865..1870
  local rec = {}
  for _, r in ipairs(W.ORI.buildBoard()) do rec[r.name] = r end
  local function expect(r) local E = END_S; local ft = r.finishT
    return ft <= E and 0 or 10 * (1 + math.floor((ft - E) / 60)) end
  check(rec.OnTime.status == "finished" and rec.OnTime.finishT < END_S and rec.OnTime.penalty == 0, "E on-time", rec.OnTime.finishT)
  check(rec.Late10s.finishT > END_S and rec.Late10s.finishT < END_S + 60 and rec.Late10s.penalty == 10,
        "E <1 min late costs 1x penalty", (rec.Late10s.finishT or 0) .. "/" .. rec.Late10s.penalty)
  check(rec.Late65s.finishT > END_S + 60 and rec.Late65s.finishT < END_S + 120 and rec.Late65s.penalty == 20,
        "E 1-2 min late costs 2x penalty", (rec.Late65s.finishT or 0) .. "/" .. rec.Late65s.penalty)
  check(W.allMsgs():find("Late65s FINISHED.-LATE penalty %-20"), "E finish message shows penalty")
  -- race-end announcements reached the still-flying helos earlier
  check(W.allMsgs():find("RACE TIME IS OVER"), "E end-of-race broadcast")
end

-- ---------------------------------------------------------------- F: ranking / tie-break
do
  local W = fresh()
  local function run(name, typ, heli, delay, extraC3)
    local u = W.spawn(name, typ, C2[1] - 3000, 200, C2[2]); u.heli = heli
    return u
  end
  local a = run("A", "F-5E-3", false)
  local c = run("C", "F-5E-3", false)
  local b = run("B", "F-5E-3", false)
  local hel = W.spawn("Hel", "UH-1H", C2[1] - 3000, 200, C2[2], true)
  W.advance(2)
  -- identical flights for A, C (and Hel), B starts 100 s later but also grabs control 3
  local function leg(us, x, z, secs)
    local starts = {}
    for i, u in ipairs(us) do starts[i] = { u.x, u.y, u.z } end
    for s = 1, secs do
      local f = s / secs
      for i, u in ipairs(us) do u.x = starts[i][1] + f * (x - starts[i][1]); u.z = starts[i][3] + f * (z - starts[i][3]) end
      W.advance(1)
    end
  end
  leg({ a, c, hel }, C2[1] + 500, C2[2], 40)       -- through control 2 (11 pts)
  leg({ a, c, hel }, SX, SZ + 1500, 40)     -- clear of START (armed)
  leg({ a, c, hel }, SX, SZ - 1500, 40)            -- through START => all three finish together
  W.advance(100)
  leg({ b }, C2[1] + 500, C2[2], 40)
  leg({ b }, C3[1] - 600, C3[2], 40); leg({ b }, C3[1] + 600, C3[2], 40)   -- control 3 (+7)
  leg({ b }, SX, SZ + 1500, 40)
  leg({ b }, SX, SZ - 1500, 40)
  local rk = {}
  for _, r in ipairs(W.ORI.buildBoard()) do rk[r.name] = r end
  check(rk.A.status == "finished" and rk.C.status == "finished" and rk.B.status == "finished", "F all finished")
  check(rk.B.pts == 18 and rk.A.pts == 11, "F points", rk.B.pts .. "/" .. rk.A.pts)
  check(rk.B.rank == 1, "F more points beats earlier finish", rk.B.rank)
  check(rk.A.rank == 2 and rk.C.rank == 2, "F exact tie shares rank 2", rk.A.rank .. "/" .. rk.C.rank)
  check(rk.Hel.rank == 1, "F ranks are per class", rk.Hel.rank)
  -- earlier-finisher-wins on equal points
  local W2 = fresh()
  local p1 = W2.spawn("Early", "F-5E-3", SX, 200, SZ + FAR)
  local p2 = W2.spawn("LateOne", "F-5E-3", SX, 200, SZ + FAR)
  W2.advance(20)
  W2.set(p1, SX, 200, SZ - FAR); W2.advance(10)
  W2.set(p2, SX, 200, SZ - FAR); W2.advance(10)
  local rr = {}
  for _, r in ipairs(W2.ORI.buildBoard()) do rr[r.name] = r end
  check(rr.Early.rank == 1 and rr.LateOne.rank == 2, "F tie on points: first finisher wins", rr.Early.rank .. "/" .. rr.LateOne.rank)
  check(W2.ORI.buildBoard()[1].name == "Early", "F board order")
end

-- ---------------------------------------------------------------- G: death / leave / rejoin
do
  local W = fresh()
  local d1 = W.spawn("DiesFirst", "F-5E-3", C2[1] - 3000, 200, C2[2])
  local d2 = W.spawn("DiesAfter", "F-5E-3", SX, 200, SZ + FAR)
  local d3 = W.spawn("Reslots", "F-5E-3", C2[1] - 3000, 200, C2[2])
  W.advance(2)
  W.set(d1, C2[1] + 125, 200, C2[2]); W.advance(1)
  W.set(d3, C2[1] + 125, 200, C2[2]); W.advance(1)
  W.fire({ id = H.EV.S_EVENT_CRASH, initiator = d1.obj }); d1.alive = false
  W.set(d2, SX, 200, SZ - FAR); W.advance(10)
  W.fire({ id = H.EV.S_EVENT_PILOT_DEAD, initiator = d2.obj }); d2.alive = false
  local rk = {}
  for _, r in ipairs(W.ORI.buildBoard()) do rk[r.name] = r end
  check(rk.DiesFirst.status == "DNF-died" and rk.DiesFirst.pts == 11 and rk.DiesFirst.rank == nil, "G died before finishing: DNF, keeps points, no rank")
  check(rk.DiesAfter.status == "finished" and rk.DiesAfter.rank == 2 and rk.DiesAfter.finishT, "G died after finishing: normal score (ranked below the 11-pt racer)", rk.DiesAfter.rank)
  check(W.ORI.buildBoard()[#W.ORI.buildBoard()].name == "DiesFirst", "G flagged player sorted below")
  -- leave and rejoin keeps progress
  W.fire({ id = H.EV.S_EVENT_PLAYER_LEAVE_UNIT, initiator = d3.obj }); d3.alive = false
  check(W.ORI.buildBoard()[1].name == "Reslots" or rk.Reslots.status == "left", "G left slot flagged", rk.Reslots.status)
  local d3b = W.spawn("Reslots", "F-5E-3", C2[1], 200, C2[2] + 4000)
  local after
  for _, r in ipairs(W.ORI.buildBoard()) do if r.name == "Reslots" then after = r end end
  check(after.status == "racing" and after.pts == 0 and #after.hitList == 0, "G leaving the slot and respawning resets points to 0", after.status .. "/" .. after.pts)
  check(W.msgsFor(d3b):find("RESET to 0 %(you had 11%)"), "G player told about the reset", W.msgsFor(d3b))
  check(W.logText():find("Reslots respawned: points reset", 1, true), "G reset logged")
  W.set(d3b, C2[1] - 125, 200, C2[2]); W.advance(1); W.set(d3b, C2[1] + 125, 200, C2[2]); W.advance(1)
  check(after.pts == 11, "G controls can be scored again after a reset", after.pts)
  -- finished and dead players are not reset by re-slotting
  local fin = rk.DiesAfter
  local fin2 = W.spawn("DiesAfter", "F-5E-3", SX, 200, SZ + FAR)
  check(fin.status == "finished" and fin.finishT ~= nil and fin.resets == nil, "G finished player not reset by re-slotting")
  local dead = rk.DiesFirst
  W.spawn("DiesFirst", "F-5E-3", SX, 200, SZ + FAR)
  check(dead.status == "DNF-died" and dead.pts == 11, "G DNF player not reset by re-slotting")
  -- same unit re-registered by the roster poll is NOT a respawn
  W.advance(20)
  check(after.pts == 11 and (after.resets or 0) == 1, "G roster polling does not cause extra resets", after.resets)
end

-- ---------------------------------------------------------------- H: timeline, DQ, final block
do
  local W = fresh()
  local r = W.spawn("Stuck", "F-5E-3", C2[1] - 3000, 200, C2[2])
  local w = W.spawn("Winner", "F-5E-3", SX, 200, SZ + FAR)
  W.advance(60)
  W.set(w, SX, 200, SZ - FAR); W.advance(5)
  W.advance(900 - W.now + 5)
  check(W.allMsgs():find("SCOREBOARD %- HALFTIME"), "H halftime scoreboard")
  W.advance(END_S - W.now + 2)
  local endMsgs = 0
  for _, m in ipairs(W.msgs) do if m.text:find("RACE TIME IS OVER", 1, true) then endMsgs = endMsgs + 1 end end
  check(endMsgs == 1, "H race-end message appears exactly once (no duplicate personal copy)", endMsgs)
  W.advance(62)
  check(W.msgsFor(r):find("RACE TIME IS OVER %- you are"), "H personal reminder after one minute", W.msgsFor(r))
  local before = select(2, W.msgsFor(r):gsub("RACE TIME IS OVER", ""))
  W.advance(180)
  local after = select(2, W.msgsFor(r):gsub("RACE TIME IS OVER", ""))
  check(after - before >= 2, "H reminders every minute", before .. "->" .. after)
  check(W.logText():find('"penalty_if_finished_now":', 1, true), "H snapshot shows penalty_if_finished_now")
  W.advance(END_S + 300 - W.now + 2)
  local rec = {}
  for _, x in ipairs(W.ORI.buildBoard()) do rec[x.name] = x end
  check(rec.Stuck.status == "DQ", "H DQ at hard stop", rec.Stuck.status)
  check(rec.Winner.status == "finished", "H winner unaffected")
  check(W.logText():find("ORIENTEERING_FINAL ", 1, true), "H FINAL block logged")
  check(W.logText():find('"podiums":{"JET":[{', 1, true), "H podium has the finisher")
  local before = #W.smokes
  W.advance(600)
  check(#W.smokes >= before + 8, "H smoke keeps refreshing after the hard stop", (#W.smokes - before))
  check(W.logText():find("smoke placed at race time", 1, true), "H smoke placement is logged")
  local rec2 = {}
  for _, x in ipairs(W.ORI.buildBoard()) do rec2[x.name] = x end
  check(rec2.Stuck.status == "DQ" and rec2.Winner.status == "finished", "H scoring frozen after hard stop")
  check(W.allMsgs():find("SCOREBOARD %- FINAL"), "H final board shown")
end

-- ---------------------------------------------------------------- I: stale zone data detection
do
  local W = fresh()
  for _, z in pairs(_G.env.mission.triggers.zones) do
    if z.name:sub(1, 2) == "02" then z.x = z.x + 1 end
  end
  local p = table.concat(W.ORI.selfCheck(), "; ")
  check(p:find("REGENERATE"), "I moved zone -> hash mismatch flagged", p)
  local W2 = fresh()
  table.insert(_G.env.mission.triggers.zones, { name = "04 Extra 1.0x", type = 0, x = 0, y = 0, radius = 10 })
  local p2 = table.concat(W2.ORI.selfCheck(), "; ")
  check(p2:find("not in the generated data"), "I new control zone flagged", p2)
end

-- ---------------------------------------------------------------- J: GM scoreboard command
do
  local W = fresh()
  check(#W.menus == 2, "J GM menu registered for both coalitions", #W.menus)
  W.spawn("X", "F-5E-3", SX, 200, SZ + FAR)
  W.menus[1].fn()
  check(W.allMsgs():find("SCOREBOARD %- GAME MASTER REQUEST"), "J GM command shows board")
  check(W.logText():find('"reason":"gm_request"', 1, true), "J GM command logs snapshot")
end

-- ---------------------------------------------------------------- K: snapshot on every scoring event
do
  local W = fresh()
  local function snaps() local n = 0; for _, l in ipairs(W.log) do if l:find("ORIENTEERING_SNAPSHOT", 1, true) then n = n + 1 end end; return n end
  local j = W.spawn("Snap", "F-5E-3", C2[1] - 3000, 200, C2[2])
  local n0 = snaps()
  W.advance(2)
  local n1 = snaps()
  W.set(j, C2[1] + 125, 200, C2[2]); W.advance(1)
  check(snaps() == n1 + 1 and W.log[#W.log]:find('"reason":"control"', 1, true), "K snapshot on control award")
  W.fire({ id = H.EV.S_EVENT_DEAD, initiator = j.obj }); j.alive = false
  check(W.log[#W.log]:find('"reason":"death"', 1, true), "K snapshot on death")
end

logfile:close()
print(string.format("%d passed, %d failed", pass, fail))
os.exit(fail == 0 and 0 or 1)
