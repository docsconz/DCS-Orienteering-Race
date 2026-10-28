--[[ =========================================================================
  DCS ORIENTEERING RACE  v1.0.7
  Players fly over Controls (trigger zones) for points, then return through
  START to finish. Dependency-free (no MOOSE/MIST). Lua 5.1 safe.

  DO NOT EDIT THE GENERATED BLOCK BY HAND - regenerate it with extract.py.
  Everything else (class tables, defaults) may be edited.

  Mission setup:  one "DO SCRIPT FILE" action on "MISSION START" (or embed in
  the .miz). Start the mission PAUSED; unpause at 00:00 = race time 0.
  Zones:  START (quad)   and   "## NAME Mx [minFT] [maxFT] [msl]" controls.
========================================================================== ]]

-- >>> GENERATED BEGIN
local DATA = nil
local SETTINGS = {}
-- <<< GENERATED END

local VERSION = "1.0.7"

-- Anything not set in SETTINGS (generated block) falls back to these.
local DEFAULTS = {
  race_end_s          = 3600,   -- race clock time when the race ends (late penalty begins)
  penalty             = 0,      -- points lost per started minute late (same scale as points)
  hard_stop_grace_s   = 300,    -- unfinished players are DQ'd this long after race end
  halftime_s          = nil,    -- default: race_end_s / 2
  poll_s              = 1.0,    -- position sampling interval
  t0_mode             = "mission", -- race clock 0 = mission time 0 ("mission") or the moment the script loads ("load")
  arm_dist_m          = 500,    -- must get this far outside START before a return counts as a finish
  despawn_delay_s     = nil,    -- nil/0 = NEVER destroy aircraft. If set, finished players are removed this long after finishing
  smoke_refresh_s     = 270,    -- DCS smoke lasts ~300 s
  smoke_color         = "Green",
  smoke_after_end     = true,   -- keep refreshing START smoke after the hard stop, for as long as the mission runs
  marker_id_base      = 9000,
  announce_finish     = false,  -- true: one broadcast to everyone when a player finishes; false: only the finisher is told
  warn_before_end_s   = { 600, 300 }, -- broadcast "N minutes remaining" this many seconds before race end (skipped if longer than the race)
  msg_s               = 15,     -- default message display time
  board_s             = 30,     -- scoreboard display time
  late_warn_s         = 60,     -- late-player reminder interval
  selfcheck_s         = 30,     -- how often to repeat self-check warnings
  gm_menu             = "coalition",   -- "coalition" | "all" | "group"
  gm_group_name       = "GAME MASTER", -- used when gm_menu = "group"
}

-- Class by DCS type name.  HELO / JET (no INS) / JET_INS.  *** REVIEW FOR YOUR EVENT ***
local CLASS_BY_TYPE = {
  ["Mi-8MT"]="HELO", ["UH-1H"]="HELO", ["Ka-50"]="HELO", ["Ka-50_3"]="HELO", ["Mi-24P"]="HELO",
  ["SA342M"]="HELO", ["SA342L"]="HELO", ["SA342Mistral"]="HELO", ["SA342Minigun"]="HELO",
  ["OH58D"]="HELO", ["UH-60L"]="HELO", ["CH-47Fbl1"]="HELO", ["OH-6A"]="HELO",
  ["MiG-15bis"]="JET", ["F-86F Sabre"]="JET", ["MiG-19P"]="JET", ["MiG-21Bis"]="JET",
  ["F-5E-3"]="JET", ["F-100D"]="JET", ["Su-25"]="JET", ["Su-25T"]="JET", ["L-39C"]="JET", ["L-39ZA"]="JET",
  ["Su-27"]="JET_INS", ["MiG-29A"]="JET_INS", ["MiG-29S"]="JET_INS", ["F-14B"]="JET_INS",
  ["F-14A-135-GR"]="JET_INS", ["M-2000C"]="JET_INS",
  ["Mirage-F1CE"]="JET", ["Mirage-F1BE"]="JET", ["Mirage-F1EE"]="JET_INS",
  -- names below confirmed from Orion_Test.miz; class of these two is a GUESS - confirm:
  ["F-4E-45MC"]="JET_INS", ["MiG-29 Fulcrum"]="JET_INS",
}
-- Types with GPS: warned about (and logged) when slotted. *** REVIEW ***
local BANNED_TYPES = {
  ["F-16C_50"]=true, ["FA-18C_hornet"]=true, ["A-10C"]=true, ["A-10C_2"]=true,
  ["AV8BNA"]=true, ["F-15ESE"]=true, ["AH-64D_BLK_II"]=true, ["JF-17"]=true,
}
local CLASS_NAMES = { HELO="Helo", JET="Jet", JET_INS="Jet-INS", UNK="Unclassified" }
local CLASS_ORDER = { "HELO", "JET", "JET_INS", "UNK" }

local ORI = { version = VERSION }

local function cfg(k)
  local v = SETTINGS[k]
  if v == nil then v = DEFAULTS[k] end
  return v
end

local function log(s) env.info("ORIENTEERING: " .. s) end

-- ================================================================ JSON
local function jstr(s)
  s = tostring(s)
  s = s:gsub('[%c"\\]', function(c)
    if c == '"' then return '\\"' elseif c == '\\' then return '\\\\'
    elseif c == '\n' then return '\\n' elseif c == '\r' then return '\\r'
    elseif c == '\t' then return '\\t' end
    return string.format("\\u%04x", c:byte())
  end)
  return '"' .. s .. '"'
end

local function jnum(n)
  if n ~= n or n == math.huge or n == -math.huge then return "null" end
  if n == math.floor(n) and math.abs(n) < 1e15 then return string.format("%d", n) end
  return string.format("%.2f", n)
end

local function jenc(v)
  local t = type(v)
  if t == "nil" then return "null"
  elseif t == "boolean" then return v and "true" or "false"
  elseif t == "number" then return jnum(v)
  elseif t == "string" then return jstr(v)
  elseif t == "table" then
    if next(v) == nil or #v > 0 then
      local out = {}
      for i = 1, #v do out[i] = jenc(v[i]) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    local out = {}
    for _, k in ipairs(keys) do out[#out + 1] = jstr(k) .. ":" .. jenc(v[k]) end
    return "{" .. table.concat(out, ",") .. "}"
  end
  return "null"
end

-- ================================================================ geometry
-- Zones: {shape="circle", x, z, r} or {shape="quad", verts={{x,z}..}}
local EPS = 1e-9

local function pointInPoly(x, z, poly)
  local inside = false
  local j = #poly
  for i = 1, #poly do
    local xi, zi, xj, zj = poly[i].x, poly[i].z, poly[j].x, poly[j].z
    if (zi > z) ~= (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi) + xi then
      inside = not inside
    end
    j = i
  end
  return inside
end

local function segEdgeT(x0, z0, x1, z1, ax, az, bx, bz)
  local rx, rz = x1 - x0, z1 - z0
  local sx, sz = bx - ax, bz - az
  local den = rx * sz - rz * sx
  if math.abs(den) < 1e-12 then return nil end
  local qx, qz = ax - x0, az - z0
  local t = (qx * sz - qz * sx) / den
  local u = (qx * rz - qz * rx) / den
  if t >= 0 and t <= 1 and u >= 0 and u <= 1 then return t end
  return nil
end

-- Fractions {t0,t1} of the segment P0->P1 that lie inside the zone.
local function insideIntervals(zone, x0, z0, x1, z1)
  local dx, dz = x1 - x0, z1 - z0
  local len2 = dx * dx + dz * dz
  if zone.shape == "circle" then
    local fx, fz = x0 - zone.x, z0 - zone.z
    local c = fx * fx + fz * fz - zone.r * zone.r
    if len2 < EPS then
      if c <= 0 then return { { 0, 0 } } end
      return {}
    end
    local b = 2 * (fx * dx + fz * dz)
    local disc = b * b - 4 * len2 * c
    if disc < 0 then return {} end
    local sq = math.sqrt(disc)
    local t0, t1 = (-b - sq) / (2 * len2), (-b + sq) / (2 * len2)
    if t1 < 0 or t0 > 1 then return {} end
    return { { math.max(t0, 0), math.min(t1, 1) } }
  end
  local poly = zone.verts
  if len2 < EPS then
    if pointInPoly(x0, z0, poly) then return { { 0, 0 } } end
    return {}
  end
  local ts = { 0, 1 }
  local j = #poly
  for i = 1, #poly do
    local t = segEdgeT(x0, z0, x1, z1, poly[j].x, poly[j].z, poly[i].x, poly[i].z)
    if t then ts[#ts + 1] = t end
    j = i
  end
  table.sort(ts)
  local res = {}
  for i = 1, #ts - 1 do
    local a, b = ts[i], ts[i + 1]
    if b - a > EPS then
      local m = (a + b) / 2
      if pointInPoly(x0 + m * dx, z0 + m * dz, poly) then
        local last = res[#res]
        if last and math.abs(last[2] - a) < EPS then last[2] = b else res[#res + 1] = { a, b } end
      end
    end
  end
  return res
end

local function distPointSeg(px, pz, ax, az, bx, bz)
  local dx, dz = bx - ax, bz - az
  local l2 = dx * dx + dz * dz
  local t = 0
  if l2 > 0 then
    t = ((px - ax) * dx + (pz - az) * dz) / l2
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
  end
  local cx, cz = ax + t * dx, az + t * dz
  return math.sqrt((px - cx) ^ 2 + (pz - cz) ^ 2)
end

local function distToPolyEdges(x, z, poly)
  local best = math.huge
  local j = #poly
  for i = 1, #poly do
    local d = distPointSeg(x, z, poly[j].x, poly[j].z, poly[i].x, poly[i].z)
    if d < best then best = d end
    j = i
  end
  return best
end

ORI.geo = { pointInPoly = pointInPoly, insideIntervals = insideIntervals,
            distToPolyEdges = distToPolyEdges }

-- ================================================================ hash (matches extract.py)
local function hashStr(s)
  local h = 5381
  for i = 1, #s do h = (h * 257 + s:byte(i)) % 2147483629 end
  return string.format("%08x", h)
end

local function zoneCanon(z)
  local name = z.name
  if z.type == 2 and z.verticies then
    local n, sx, sz = 0, 0, 0
    local parts = {}
    for i = 1, #z.verticies do
      local v = z.verticies[i]
      n = n + 1; sx = sx + v.x; sz = sz + v.y
      parts[#parts + 1] = string.format("%.2f,%.2f", v.x, v.y)
    end
    return string.format("%s|quad|%.2f,%.2f,%s", name, sx / n, sz / n, table.concat(parts, ";"))
  end
  return string.format("%s|circle|%.2f,%.2f,r%.2f", name, z.x, z.y, z.radius)
end

local function missionZoneHash(names)
  local want, list = {}, {}
  for _, n in ipairs(names) do want[n] = true end
  local zones = env.mission and env.mission.triggers and env.mission.triggers.zones or {}
  local unexpected = {}
  for _, z in pairs(zones) do
    if want[z.name] then
      list[#list + 1] = z
    else
      local u = (z.name or ""):upper()
      if u == "START" or u:match("^%d+%s") then unexpected[#unexpected + 1] = z.name end
    end
  end
  table.sort(list, function(a, b) return a.name < b.name end)
  local canon = {}
  for i, z in ipairs(list) do canon[i] = zoneCanon(z) end
  return hashStr(table.concat(canon, "\n")), #list, unexpected
end

-- ================================================================ state
local ST = {
  T0 = 0, started = false, hardDone = false, seq = 0,
  halfDone = false, endDone = false, warned = {},
  nextLate = 0, nextSmoke = 0, nextCheck = 0, nextRoster = 0,
  players = {}, problems = {}, menuDone = false, markTry = {}, markMissing = 0,
}
local CTRLS, START = {}, nil
ORI.state = ST

local function rtime() return timer.getTime() - ST.T0 end

local function fmtTime(s)
  s = math.floor(s + 0.5)
  if s < 0 then s = 0 end
  local h, m = math.floor(s / 3600), math.floor((s % 3600) / 60)
  if h > 0 then return string.format("%d:%02d:%02d", h, m, s % 60) end
  return string.format("%02d:%02d", m, s % 60)
end

local function fmtNum(v)
  if v == math.floor(v) then return string.format("%d", v) end
  return string.format("%.1f", v)
end

local function toAll(text, sec) pcall(trigger.action.outText, text, sec or cfg("msg_s")) end
local function toPlayer(rec, text, sec)
  if rec.unitId then pcall(trigger.action.outTextForUnit, rec.unitId, text, sec or cfg("msg_s")) end
end

-- ================================================================ setup
local function altText(c)
  local parts = {}
  if c.min_ft then parts[#parts + 1] = "min " .. fmtNum(c.min_ft) end
  if c.max_ft then parts[#parts + 1] = "max " .. fmtNum(c.max_ft) end
  if #parts == 0 then return nil end
  return table.concat(parts, " / ") .. " ft " .. c.ref
end

local function prepare()
  CTRLS = {}
  local s = DATA.start
  START = { shape = "quad", verts = s.verts, x = s.x, z = s.z }
  for _, d in ipairs(DATA.controls) do
    local zone
    if d.shape == "circle" then
      zone = { shape = "circle", x = d.x, z = d.z, r = d.r }
    else
      zone = { shape = "quad", verts = d.verts }
    end
    local nm = math.sqrt((d.x - s.x) ^ 2 + (d.z - s.z) ^ 2) / 1852
    local pts = math.floor(nm * d.mult * DATA.ppu + 0.5)
    if pts ~= d.points then
      ST.problems[#ST.problems + 1] = string.format("control %d points differ: script %d vs generator %d", d.num, pts, d.points)
    end
    local c = { num = d.num, name = d.name, mult = d.mult, points = pts, zone = zone, x = d.x, z = d.z,
                min_ft = d.min_ft, max_ft = d.max_ft, min_m = d.min_m, max_m = d.max_m,
                ref = d.ref or "AGL" }
    c.hasAlt = (c.min_m ~= nil) or (c.max_m ~= nil)
    CTRLS[#CTRLS + 1] = c
  end
  table.sort(CTRLS, function(a, b) return a.num < b.num end)
end

local function selfCheck()
  local probs = {}
  for _, p in ipairs(ST.problems) do probs[#probs + 1] = p end  -- carried from prepare()
  if not DATA then return { "NO DATA: script was not generated by extract.py" } end
  if #CTRLS == 0 then probs[#probs + 1] = "no controls loaded" end
  local ok, h, n, unexpected = pcall(missionZoneHash, DATA.zone_names)
  if not ok then
    probs[#probs + 1] = "could not read env.mission zones: " .. tostring(h)
  else
    if n ~= #DATA.zone_names then
      probs[#probs + 1] = string.format("mission has %d of %d expected zones", n, #DATA.zone_names)
    end
    if h ~= DATA.zone_hash then
      probs[#probs + 1] = string.format("zones changed since generation (hash %s, expected %s) - REGENERATE", h, DATA.zone_hash)
    end
    for _, u in ipairs(unexpected) do probs[#probs + 1] = "zone '" .. u .. "' is not in the generated data" end
  end
  if cfg("penalty") == 0 then probs[#probs + 1] = "late penalty is 0 (not set)" end
  if ST.markMissing > 0 and ST.started and timer.getTime() - (ST.markFirst or 0) > 5 then
    probs[#probs + 1] = ST.markMissing .. " map marker(s) missing - retrying (" .. tostring(ST.markStatus) .. ")"
  end
  return probs
end

local function reportProblems()
  ST.lastProblems = selfCheck()
  if #ST.lastProblems > 0 then
    local txt = "ORIENTEERING SCRIPT WARNING:\n- " .. table.concat(ST.lastProblems, "\n- ")
    log(txt:gsub("\n", " | "))
    toAll(txt, 20)
  end
end

-- ================================================================ map markers + smoke
local function groundY(x, z)
  local ok, h = pcall(land.getHeight, { x = x, y = z })
  return ok and h or 0
end

local function wantedMarks()
  local base = cfg("marker_id_base")
  local w = { { id = base, text = "START / FINISH", x = START.x, z = START.z } }
  for _, c in ipairs(CTRLS) do
    local at = altText(c)
    w[#w + 1] = { id = base + c.num, x = c.x, z = c.z,
                  text = string.format("%02d %s (%d pts%s)", c.num, c.name, c.points, at and (", " .. at) or "") }
  end
  return w
end

-- world.getMarkPanels() lists the marks that really exist; nil if unavailable.
local function existingMarks()
  if not (world and world.getMarkPanels) then return nil end
  local ok, panels = pcall(world.getMarkPanels)
  if not ok or type(panels) ~= "table" then return nil end
  local set = {}
  for _, p in pairs(panels) do if p.idx then set[p.idx] = p.text or "" end end
  return set
end

-- Only (re-)adds marks that are missing; never removes a mark that is already correct.
-- Safe to call as often as you like (adds are spaced >= 3 s apart per mark).
local function placeMarkers()
  local present = existingMarks()
  local now = timer.getTime()
  local want = wantedMarks()
  local have = 0
  for _, m in ipairs(want) do
    if present and present[m.id] == m.text then
      have = have + 1
    else
      if present and present[m.id] ~= nil then pcall(trigger.action.removeMark, m.id) end  -- stale text
      local last = ST.markTry[m.id]
      if (present or not last) and (not last or now - last >= 3) then
        pcall(trigger.action.markToAll, m.id, m.text, { x = m.x, y = groundY(m.x, m.z), z = m.z }, true)
        ST.markTry[m.id] = now
      end
    end
  end
  ST.markStatus = present and string.format("%d of %d markers present%s", have, #want,
                    (have < #want and now - (ST.markFirst or now) < 3) and " (just placed, DCS lists them a moment later)" or "")
                  or "marker check unavailable (world.getMarkPanels missing)"
  if ST.markStatus ~= ST.markLogged then
    ST.markLogged = ST.markStatus
    log("map markers: " .. ST.markStatus)
  end
  ST.markMissing = present and (#want - have) or 0
end

local function placeSmoke()
  local col = trigger.smokeColor and trigger.smokeColor[cfg("smoke_color")]
  if col == nil then col = 0 end
  local n = 0
  for _, v in ipairs(START.verts) do
    local ok = pcall(trigger.action.smoke, { x = v.x, y = groundY(v.x, v.z), z = v.z }, col)
    if ok then n = n + 1 end
  end
  log(string.format("smoke placed at race time %s (%d of %d corners, next refresh in %d s)",
    fmtTime(timer.getTime() - ST.T0), n, #START.verts, cfg("smoke_refresh_s")))
end

-- ================================================================ players / ranking
local function newRec(name)
  return { name = name, class = "UNK", status = "racing", pts = 0, penalty = 0, hits = {}, hitList = {},
           armed = false }
end

local function net(r) return r.pts - r.penalty end
local function r2(v) return math.floor((v or 0) * 100 + 0.5) end

local function grp(r)
  if r.status == "DNF-died" then return 2 elseif r.status == "DQ" then return 3 end
  return 1
end

local function lessThan(a, b)
  local ga, gb = grp(a), grp(b)
  if ga ~= gb then return ga < gb end
  if net(a) ~= net(b) then return net(a) > net(b) end
  local fa, fb = a.status == "finished", b.status == "finished"
  if fa ~= fb then return fa end
  if fa and r2(a.finishT) ~= r2(b.finishT) then return r2(a.finishT) < r2(b.finishT) end
  return a.name < b.name
end

local function sameRank(a, b)
  if net(a) ~= net(b) then return false end
  local fa, fb = a.status == "finished", b.status == "finished"
  if fa ~= fb then return false end
  if fa and r2(a.finishT) ~= r2(b.finishT) then return false end
  return true
end

-- Sorted list of player records, each with .rank set (rank in class; nil if flagged)
local function buildBoard()
  local list = {}
  for _, r in pairs(ST.players) do list[#list + 1] = r end
  table.sort(list, lessThan)
  local seen, prev, prevRank = {}, {}, {}
  for _, r in ipairs(list) do
    r.rank = nil
    if grp(r) == 1 then
      local n = (seen[r.class] or 0) + 1
      seen[r.class] = n
      if prev[r.class] and sameRank(prev[r.class], r) then r.rank = prevRank[r.class]
      else r.rank = n end
      prev[r.class], prevRank[r.class] = r, r.rank
    end
  end
  return list
end

local function statusLabel(r)
  if r.status == "finished" then
    local s = "FINISHED " .. fmtTime(r.finishT)
    if r.penalty > 0 then s = s .. string.format(" (late -%d)", r.penalty) end
    return s
  elseif r.status == "DNF-died" then return "DNF (died)"
  elseif r.status == "DQ" then return "DQ (did not finish)"
  elseif r.status == "left" then return "left slot" end
  return "racing"
end

local function boardText(title)
  local lines = { string.format("SCOREBOARD - %s (race time %s)", title, fmtTime(rtime())) }
  local list = buildBoard()
  if #list == 0 then lines[#lines + 1] = "(no players)" end
  for i, r in ipairs(list) do
    lines[#lines + 1] = string.format("%d. %s [%s %s] %d pts, %d ctl - %s", i, r.name,
      CLASS_NAMES[r.class] or r.class, r.rank and ("#" .. r.rank) or "-", net(r), #r.hitList, statusLabel(r))
  end
  return table.concat(lines, "\n")
end

local function penaltyAt(t)
  local E = cfg("race_end_s")
  if t <= E then return 0 end
  return cfg("penalty") * (1 + math.floor((t - E) / 60))
end

local function playerJson(r, rt)
  local ctl = {}
  for _, h in ipairs(r.hitList) do ctl[#ctl + 1] = { n = h.n, t = h.t } end
  local o = { name = r.name, class = r.class, type = r.typeName, status = r.status,
              points = r.pts, penalty = r.penalty, net = net(r), controls = ctl,
              finish_t = r.finishT, rank_in_class = r.rank, died_t = r.diedT, resets = r.resets }
  if rt > cfg("race_end_s") and (r.status == "racing" or r.status == "left") then
    o.penalty_if_finished_now = penaltyAt(rt)
  end
  return o
end

local function snapshot(reason, who)
  ST.seq = ST.seq + 1
  local rt = rtime()
  local list = buildBoard()
  local pj = {}
  for i, r in ipairs(list) do pj[i] = playerJson(r, rt) end
  local o = { seq = ST.seq, t = rt, reason = reason, player = who, race_end_s = cfg("race_end_s"),
              zone_hash = DATA and DATA.zone_hash, players = pj }
  env.info("ORIENTEERING_SNAPSHOT " .. jenc(o))
end

local function showBoard(title)
  toAll(boardText(title), cfg("board_s"))
end

-- ================================================================ events / registry
-- Type names are matched ignoring case, spaces and punctuation ("Mirage-F1CE" == "Mirage F1CE").
local function normType(t) return (tostring(t):lower():gsub("[^%w]", "")) end
local CLASS_NORM, BANNED_NORM = {}, {}
for k, v in pairs(CLASS_BY_TYPE) do CLASS_NORM[normType(k)] = v end
for k in pairs(BANNED_TYPES) do BANNED_NORM[normType(k)] = true end

local function classify(u)
  local tn = u:getTypeName()
  local c = CLASS_NORM[normType(tn)]
  if c then return c, tn end
  local ok, heli = pcall(u.hasAttribute, u, "Helicopters")
  if ok and heli then return "HELO", tn end
  return "UNK", tn
end

local function registerUnit(u)
  local ok, name = pcall(function()
    if u and u:isExist() then return u:getPlayerName() end
  end)
  if not ok or not name or name == "" then return nil end
  local rec = ST.players[name]
  local isNew = (rec == nil)
  if isNew then rec = newRec(name); ST.players[name] = rec end
  local uid = u:getID()
  local changed = (rec.unitId ~= uid)
  -- Leaving the slot and spawning again (or appearing in a different aircraft) restarts the player at 0.
  -- Finished / DNF / DQ players are never reset.
  local fresh = (not isNew) and (rec.status == "racing" or rec.status == "left")
                and (changed or rec.status == "left")
  rec.unit, rec.unitId = u, uid
  rec.class, rec.typeName = classify(u)
  if changed then rec.lx = nil end
  if rec.status == "left" then rec.status = "racing" end
  if fresh then
    local lost, n = rec.pts, #rec.hitList
    rec.pts, rec.penalty, rec.hits, rec.hitList, rec.armed, rec.lx = 0, 0, {}, {}, false, nil
    rec.resets = (rec.resets or 0) + 1
    log(string.format("%s respawned: points reset (was %d pts, %d controls)", name, lost, n))
    toPlayer(rec, string.format("You left your aircraft and spawned a new one - your score has been RESET to 0%s.",
      lost > 0 and string.format(" (you had %d)", lost) or ""), 20)
  end
  if isNew or changed then
    if rec.class == "UNK" then
      toAll(string.format("%s slotted unclassified type '%s' - add it to CLASS_BY_TYPE", name, tostring(rec.typeName)), 20)
      log("unclassified type " .. tostring(rec.typeName))
    end
    if BANNED_NORM[normType(rec.typeName)] then
      toAll(string.format("WARNING: %s is in a %s (GPS-equipped - not allowed)", name, rec.typeName), 20)
      log("banned type " .. tostring(rec.typeName) .. " slotted by " .. name)
    end
    snapshot("join", name)
  end
  return rec
end

local function recFromUnit(u)
  local ok, uid = pcall(function() return u:getID() end)
  if not ok or not uid then return nil end
  for _, r in pairs(ST.players) do if r.unitId == uid then return r end end
  return nil
end

local function markDead(rec)
  if rec.status ~= "racing" and rec.status ~= "left" then return end
  rec.status, rec.diedT = "DNF-died", rtime()
  toAll(string.format("%s is down (%d pts, DNF)", rec.name, net(rec)), 10)
  snapshot("death", rec.name)
end

local function onEvent(e)
  local ev = world.event
  local id = e.id
  if id == ev.S_EVENT_BIRTH or id == ev.S_EVENT_PLAYER_ENTER_UNIT then
    registerUnit(e.initiator)
    pcall(placeMarkers)
  elseif id == ev.S_EVENT_PLAYER_LEAVE_UNIT then
    local rec = recFromUnit(e.initiator)
    if rec then
      if rec.status == "racing" then rec.status = "left" end
      rec.unit, rec.unitId, rec.lx = nil, nil, nil
      snapshot("leave", rec.name)
    end
  elseif id == ev.S_EVENT_DEAD or id == ev.S_EVENT_CRASH or id == ev.S_EVENT_EJECTION
      or id == ev.S_EVENT_PILOT_DEAD then
    local rec = e.initiator and recFromUnit(e.initiator)
    if rec then markDead(rec) end
  end
end

-- ================================================================ scoring
local function awardControl(rec, c, t)
  rec.hits[c.num] = t
  rec.hitList[#rec.hitList + 1] = { n = c.num, t = t }
  rec.pts = rec.pts + c.points
  toPlayer(rec, string.format("CONTROL %02d - %s: +%d pts (total %d)", c.num, c.name, c.points, rec.pts), 10)
  snapshot("control", rec.name)
end

local function altOk(c, y, ground)
  local a = (c.ref == "MSL") and y or (y - ground)
  if c.min_m and a < c.min_m then return false end
  if c.max_m and a > c.max_m then return false end
  return true
end

-- First fraction of the step at which the aircraft is inside the zone AND within the altitude band.
local function firstPassing(c, ivs, x0, y0, z0, x1, y1, z1)
  for _, iv in ipairs(ivs) do
    if not c.hasAlt then return iv[1] end
    for k = 1, 3 do
      local f = (k == 1) and iv[1] or ((k == 2) and (iv[1] + iv[2]) / 2 or iv[2])
      local x, y, z = x0 + f * (x1 - x0), y0 + f * (y1 - y0), z0 + f * (z1 - z0)
      local g = (c.ref == "AGL") and groundY(x, z) or 0
      if altOk(c, y, g) then return f end
    end
  end
  return nil
end

local function finishPlayer(rec, ft)
  rec.status, rec.finishT = "finished", ft
  rec.penalty = penaltyAt(ft)
  local msg = string.format("%s FINISHED at %s with %d control points", rec.name, fmtTime(ft), rec.pts)
  if rec.penalty > 0 then
    msg = msg .. string.format(" - LATE penalty -%d = %d pts", rec.penalty, net(rec))
  else
    msg = msg .. string.format(" = %d pts", net(rec))
  end
  local dd = cfg("despawn_delay_s")
  local removing = dd and dd > 0
  if cfg("announce_finish") then
    toAll(msg, 20)    -- the finisher sees this too, so no separate personal copy
    if removing then toPlayer(rec, "You will be removed from the aircraft shortly.", 20) end
  else
    toPlayer(rec, msg .. (removing and "\nYou will be removed from the aircraft shortly." or ""), 20)
  end
  local u = rec.unit
  if u and dd and dd > 0 then
    timer.scheduleFunction(function() pcall(function() if u:isExist() then u:destroy() end end) end,
      nil, timer.getTime() + cfg("despawn_delay_s"))
  end
  snapshot("finish", rec.name)
end

local function processPlayer(rec, rt)
  if rec.status ~= "racing" then return end
  local u = rec.unit
  if not u or not u:isExist() then return end
  local p = u:getPoint()
  local x1, y1, z1 = p.x, p.y, p.z
  local x0, y0, z0, t0 = rec.lx, rec.ly, rec.lz, rec.lt
  if not x0 then x0, y0, z0, t0 = x1, y1, z1, rt end

  for _, c in ipairs(CTRLS) do
    if not rec.hits[c.num] then
      local ivs = insideIntervals(c.zone, x0, z0, x1, z1)
      if #ivs > 0 then
        local f = firstPassing(c, ivs, x0, y0, z0, x1, y1, z1)
        if f then awardControl(rec, c, t0 + f * (rt - t0)) end
      end
    end
  end

  if not rec.armed then
    if not pointInPoly(x1, z1, START.verts)
        and distToPolyEdges(x1, z1, START.verts) >= cfg("arm_dist_m") then
      rec.armed = true
    end
  else
    local ivs = insideIntervals(START, x0, z0, x1, z1)
    if #ivs > 0 then finishPlayer(rec, t0 + ivs[1][1] * (rt - t0)) end
  end
  rec.lx, rec.ly, rec.lz, rec.lt = x1, y1, z1, rt
end

-- ================================================================ timeline
local function lateWarnings(rt)
  local E, G = cfg("race_end_s"), cfg("hard_stop_grace_s")
  for _, r in pairs(ST.players) do
    if r.status == "racing" and r.unit then
      local late = rt - E
      toPlayer(r, string.format(
        "RACE TIME IS OVER - you are %s late. Fly through START/FINISH NOW.\nPenalty if you finish now: -%d (grows by %d each minute). DQ in %s.",
        fmtTime(late), penaltyAt(rt), cfg("penalty"), fmtTime(E + G - rt)), 20)
    end
  end
end

local function podiums()
  local out = {}
  local list = buildBoard()
  for _, cls in ipairs(CLASS_ORDER) do
    local pod = {}
    for _, r in ipairs(list) do
      if r.class == cls and r.status == "finished" and r.rank and r.rank <= 3 then
        pod[#pod + 1] = { rank = r.rank, name = r.name, net = net(r), finish_t = r.finishT }
      end
    end
    if #pod > 0 then out[cls] = pod end
  end
  return out
end

local function hardStop(rt)
  ST.hardDone = true
  for _, r in pairs(ST.players) do
    if r.status == "racing" or r.status == "left" then
      r.status = "DQ"
      toPlayer(r, "You did not finish in time - DISQUALIFIED.", 20)
    end
  end
  snapshot("hard_stop")
  local list = buildBoard()
  local pj = {}
  for i, r in ipairs(list) do pj[i] = playerJson(r, rt) end
  env.info("ORIENTEERING_FINAL " .. jenc({ version = VERSION, t = rt, race_end_s = cfg("race_end_s"),
    penalty_per_minute = cfg("penalty"), zone_hash = DATA.zone_hash, podiums = podiums(), players = pj }))
  showBoard("FINAL")
end

local function doTick(now)
  local rt = now - ST.T0
  if ST.hardDone then   -- race is over: only keep the finish area marked
    if not cfg("smoke_after_end") then return nil end
    if rt >= ST.nextSmoke then
      placeSmoke()
      ST.nextSmoke = rt + cfg("smoke_refresh_s")
    end
    return now + 5
  end
  if not ST.started then
    ST.started = true
    placeSmoke()
    ST.nextSmoke = rt + cfg("smoke_refresh_s")
    ST.nextCheck = rt + cfg("selfcheck_s")
    local sum = string.format("ORIENTEERING RACE STARTED - %d controls, ends at %s (zone hash %s)",
      #CTRLS, fmtTime(cfg("race_end_s")), DATA.zone_hash)
    log(sum)
    toAll(sum, 15)
    reportProblems()
    snapshot("start")
  end

  if rt >= ST.nextRoster then
    ST.nextRoster = rt + 5
    for _, side in ipairs({ 0, 1, 2 }) do
      local ok, us = pcall(coalition.getPlayers, side)
      if ok and us then for _, u in ipairs(us) do registerUnit(u) end end
    end
  end

  for _, rec in pairs(ST.players) do
    local ok, err = pcall(processPlayer, rec, rt)
    if not ok then log("player error (" .. tostring(rec.name) .. "): " .. tostring(err)) end
  end

  local E, G = cfg("race_end_s"), cfg("hard_stop_grace_s")
  local half = cfg("halftime_s") or (E / 2)
  if not ST.halfDone and rt >= half then
    ST.halfDone = true
    showBoard("HALFTIME")
    snapshot("halftime")
  end
  for _, w in ipairs(cfg("warn_before_end_s") or {}) do
    local at = E - w
    -- skip if it would fall before the race starts, or if we are already well past it (late load)
    if not ST.warned[w] and at > 0 and rt >= at then
      ST.warned[w] = true
      if rt < at + 30 then
        local mins = w / 60
        toAll(string.format("%s MINUTE%s REMAINING - race ends at %s (now %s).\nFly through START/FINISH before then; after that a late penalty of -%d per minute applies.",
          fmtNum(mins), mins == 1 and "" or "S", fmtTime(E), fmtTime(rt), cfg("penalty")), 20)
        log(string.format("announced %s minutes remaining", fmtNum(mins)))
      end
    end
  end
  if not ST.endDone and rt >= E then
    ST.endDone = true
    toAll(string.format("RACE TIME IS OVER. Late penalty now applies: -%d, then -%d per further minute. Finish before %s or be disqualified.",
      cfg("penalty"), cfg("penalty"), fmtTime(E + G)), 20)
    ST.nextLate = E + cfg("late_warn_s")   -- personal reminders start a minute later (broadcast above covers minute 0)
    snapshot("race_end")
  end
  if ST.endDone and rt >= ST.nextLate and rt < E + G then
    lateWarnings(rt)
    ST.nextLate = ST.nextLate + cfg("late_warn_s")
  end
  if rt >= E + G then
    hardStop(rt)
    return cfg("smoke_after_end") and (now + 5) or nil
  end
  if rt >= ST.nextSmoke then
    placeSmoke()
    ST.nextSmoke = rt + cfg("smoke_refresh_s")
  end
  if rt >= ST.nextCheck then
    ST.nextCheck = rt + cfg("selfcheck_s")
    pcall(placeMarkers)
    reportProblems()
  end
  return now + cfg("poll_s")
end

local function tick(_, now)
  local ok, res = pcall(doTick, now)
  if not ok then
    log("tick error: " .. tostring(res))
    return now + cfg("poll_s")
  end
  return res
end

-- ================================================================ GM menu
local function gmShow()
  local ok, err = pcall(function()
    showBoard("GAME MASTER REQUEST")
    snapshot("gm_request")
  end)
  if not ok then log("gm command error: " .. tostring(err)) end
end

local function addMenu()
  local mode = cfg("gm_menu")
  local title = "Show Orienteering Scoreboard"
  if mode == "all" then
    missionCommands.addCommand(title, nil, gmShow)
  elseif mode == "group" then
    local g = Group.getByName(cfg("gm_group_name"))
    if g then missionCommands.addCommandForGroup(g:getID(), title, nil, gmShow)
    else log("gm_menu=group but group '" .. cfg("gm_group_name") .. "' not found") end
  else
    missionCommands.addCommandForCoalition(coalition.side.RED, title, nil, gmShow)
    missionCommands.addCommandForCoalition(coalition.side.BLUE, title, nil, gmShow)
  end
end

-- ================================================================ init
local function init()
  if not DATA then
    local m = "ORIENTEERING: no generated data - run extract.py"
    env.error(m); trigger.action.outText(m, 60)
    return
  end
  ST.T0 = (cfg("t0_mode") == "load") and timer.getTime() or 0
  prepare()
  log(string.format("loaded, %d controls, zone hash %s, version %s", #CTRLS, DATA.zone_hash, VERSION))
  ST.markFirst = timer.getTime()
  pcall(placeMarkers)
  world.addEventHandler({ onEvent = function(_, e)
    local ok, err = pcall(onEvent, e)
    if not ok then log("event error: " .. tostring(err)) end
  end })
  local ok, err = pcall(addMenu)
  if not ok then log("menu error: " .. tostring(err)) end
  reportProblems()
  timer.scheduleFunction(tick, nil, timer.getTime() + 0.1)
end

ORI.cfg, ORI.jenc, ORI.hashStr, ORI.ctrls = cfg, jenc, hashStr, function() return CTRLS end
ORI.buildBoard, ORI.selfCheck = buildBoard, selfCheck

local okInit, errInit = pcall(init)
if not okInit then
  env.error("ORIENTEERING init failed: " .. tostring(errInit))
  pcall(trigger.action.outText, "ORIENTEERING init failed: " .. tostring(errInit), 60)
end

if rawget(_G, "ORI_EXPOSE") then _G.ORI = ORI end
