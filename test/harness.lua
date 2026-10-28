-- Minimal mock of the DCS mission scripting environment, enough to run race_template.lua.
-- Run with texlua (Lua 5.3) here; the script itself is written to be Lua 5.1 safe for DCS.
local H = {}

local EV = { S_EVENT_CRASH = 5, S_EVENT_EJECTION = 6, S_EVENT_DEAD = 8, S_EVENT_PILOT_DEAD = 9,
             S_EVENT_BIRTH = 15, S_EVENT_PLAYER_ENTER_UNIT = 20, S_EVENT_PLAYER_LEAVE_UNIT = 21 }
H.EV = EV

function H.new(scriptPath, zonesPath, logfile)
  local W = { now = 0, queue = {}, msgs = {}, marks = {}, smokes = {}, handlers = {}, units = {},
              menus = {}, log = {}, nextId = 100, ground = 100 }

  function W.advance(dt)
    local target = W.now + dt
    while true do
      local bi, bt
      for i, q in ipairs(W.queue) do
        if q.t <= target and (not bt or q.t < bt) then bi, bt = i, q.t end
      end
      if not bi then break end
      local q = table.remove(W.queue, bi)
      if bt > W.now then W.now = bt end
      local nxt = q.fn(q.arg, W.now)
      if type(nxt) == "number" then q.t = nxt; table.insert(W.queue, q) end
    end
    W.now = target
  end

  function W.fire(e)
    for _, h in ipairs(W.handlers) do h.onEvent(h, e) end
  end

  function W.spawn(name, typeName, x, y, z, heli)
    local u = { name = name, id = W.nextId, type = typeName, x = x, y = y, z = z, alive = true, heli = heli }
    W.nextId = W.nextId + 1
    local o = {}
    function o:isExist() return u.alive end
    function o:getPlayerName() return u.name end
    function o:getID() return u.id end
    function o:getTypeName() return u.type end
    function o:getPoint() return { x = u.x, y = u.y, z = u.z } end
    function o:hasAttribute(a) return a == "Helicopters" and (u.heli or false) end
    function o:destroy()
      if u.alive then u.alive = false; u.destroyed = true; W.fire({ id = EV.S_EVENT_DEAD, initiator = o }) end
    end
    u.obj = o
    W.units[name] = u
    W.fire({ id = EV.S_EVENT_BIRTH, initiator = o })
    return u
  end

  function W.set(u, x, y, z) u.x, u.y, u.z = x, y, z end

  -- Fly in a straight line over `secs` seconds (1 s steps).
  function W.fly(u, x, y, z, secs)
    local x0, y0, z0 = u.x, u.y, u.z
    for i = 1, secs do
      local f = i / secs
      u.x, u.y, u.z = x0 + f * (x - x0), y0 + f * (y - y0), z0 + f * (z - z0)
      W.advance(1)
    end
  end

  function W.msgsFor(u)
    local out = {}
    for _, m in ipairs(W.msgs) do if m.to == u.id then out[#out + 1] = m.text end end
    return table.concat(out, "\n")
  end
  function W.allMsgs()
    local out = {}
    for _, m in ipairs(W.msgs) do out[#out + 1] = m.text end
    return table.concat(out, "\n")
  end
  function W.logText() return table.concat(W.log, "\n") end

  function W.load()
    _G.env = {
      info = function(s)
        W.log[#W.log + 1] = s
        if logfile then logfile:write("2026-10-27 12:00:00.000 INFO    SCRIPTING (Main): ", s, "\n") end
      end,
      error = function(s) W.log[#W.log + 1] = "ERROR " .. s end,
      mission = dofile(zonesPath),
    }
    W.env = _G.env
    _G.timer = { getTime = function() return W.now end,
                 scheduleFunction = function(fn, arg, t) table.insert(W.queue, { fn = fn, arg = arg, t = t }) end }
    _G.land = { getHeight = function(v) return type(W.ground) == "function" and W.ground(v.x, v.y) or W.ground end }
    _G.trigger = {
      smokeColor = { Green = 0, Red = 1, White = 2, Orange = 3, Blue = 4 },
      action = {
        outText = function(t, s) W.msgs[#W.msgs + 1] = { to = "all", text = t, t = W.now } end,
        outTextForUnit = function(id, t, s) W.msgs[#W.msgs + 1] = { to = id, text = t, t = W.now } end,
        markToAll = function(id, t, pos, ro) W.marks[id] = { text = t, pos = pos, readOnly = ro } end,
        removeMark = function(id) W.removeCalls = (W.removeCalls or 0) + 1; W.marks[id] = nil end,
        smoke = function(pos, color) W.smokes[#W.smokes + 1] = { t = W.now, pos = pos, color = color } end,
      },
    }
    _G.world = { event = EV, addEventHandler = function(h) W.handlers[#W.handlers + 1] = h end,
                 getMarkPanels = function()
                   if W.noPanels then return nil end
                   local out = {}
                   for id, m in pairs(W.marks) do out[#out + 1] = { idx = id, text = m.text } end
                   return out
                 end }
    _G.coalition = { side = { NEUTRAL = 0, RED = 1, BLUE = 2 },
                     getPlayers = function(side)
                       local out = {}
                       if side == 1 then
                         for _, u in pairs(W.units) do if u.alive then out[#out + 1] = u.obj end end
                       end
                       return out
                     end }
    _G.missionCommands = {
      addCommandForCoalition = function(side, name, path, fn) W.menus[#W.menus + 1] = { side = side, name = name, fn = fn } end,
      addCommand = function(name, path, fn) W.menus[#W.menus + 1] = { name = name, fn = fn } end,
      addCommandForGroup = function(id, name, path, fn) W.menus[#W.menus + 1] = { group = id, name = name, fn = fn } end,
    }
    _G.Group = { getByName = function() return nil end }
    _G.ORI_EXPOSE = true
    _G.ORI = nil
    local chunk = assert(loadfile(scriptPath))
    chunk()
    W.ORI = _G.ORI
    return W
  end

  return W
end

return H
