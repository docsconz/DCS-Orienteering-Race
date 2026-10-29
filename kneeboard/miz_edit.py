"""Edit a DCS .miz in place: add a MISSION START "DO SCRIPT FILE" trigger and/or kneeboard pages.

The trigger format was copied from what the Mission Editor itself writes (compared on two real
missions that differ only by that trigger):

  mission / trigrules[n]      = {rules = {}, comment, eventlist = "mission start",
                                 actions = {{predicate = "a_do_script_file", file = "ResKey_Action_K"}},
                                 predicate = "triggerOnce"}
  mission / trig              = actions[n], events["mission start"][n], flag[n], conditions[n]
  mission / maxDictId         = K
  l10n/DEFAULT/mapResource    = ["ResKey_Action_K"] = "<script file name>"
  l10n/DEFAULT/<script file>  = the script

Edits are made as text insertions (the file is a Lua table; order and formatting of everything else
is untouched), then the result is parsed back and checked before it replaces the original, and a
backup of the original is kept next to it.
"""
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import extract as X                                       # noqa: E402  (the repo's own Lua table parser)


class MizError(RuntimeError):
    pass


# ---------------------------------------------------------------- tiny Lua-table text scanner
def _skip(text, i):
    """If a string or comment starts at i, return the index just after it; else i."""
    if text[i] == '"':
        j = i + 1
        while text[j] != '"':
            j += 2 if text[j] == "\\" else 1
        return j + 1
    if text.startswith("--[[", i):
        return text.index("]]", i) + 2
    if text.startswith("--", i):
        j = text.find("\n", i)
        return len(text) if j < 0 else j
    if text.startswith("[[", i):
        return text.index("]]", i) + 2
    return i


def match_brace(text, i):
    depth = 0
    n = len(text)
    while i < n:
        j = _skip(text, i)
        if j != i:
            i = j
            continue
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise MizError("unbalanced braces")


_KEY = re.compile(r'\[(?:"((?:[^"\\]|\\.)*)"|(\d+))\]\s*=\s*')


def _children(text, open_idx):
    """[(key, value_start)] of the direct children of the table whose '{' is at open_idx."""
    out, depth, i = [], 0, open_idx
    n = len(text)
    while i < n:
        j = _skip(text, i)
        if j != i:
            i = j
            continue
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return out
        elif c == "[" and depth == 1:
            m = _KEY.match(text, i)
            if m:
                k = m.group(1) if m.group(1) is not None else int(m.group(2))
                out.append((k, m.end()))
                i = m.end()
                continue
        i += 1
    raise MizError("unbalanced braces")


def child_table(text, open_idx, key):
    """(open, close) indices of the child table `key`, or None."""
    for k, v in _children(text, open_idx):
        if k == key and text[v] == "{":
            return v, match_brace(text, v)
    return None


def _line_indent(text, idx):
    ls = text.rfind("\n", 0, idx) + 1
    m = re.match(r"[ \t]*", text[ls:])
    return m.group(0)


def insert_entry(text, open_idx, lines):
    """Insert entry `lines` (list of str, relative indentation kept) as the last child of the table at
    open_idx.  Returns the new text."""
    close = match_brace(text, open_idx)
    base = _line_indent(text, open_idx)
    ent = "".join(base + "\t" + ln + "\n" for ln in lines)
    if text[open_idx + 1:close].strip() == "":              # empty "{}": expand it
        return text[:open_idx] + "{\n" + ent + base + "}" + text[close + 1:]
    ls = text.rfind("\n", 0, close) + 1
    if text[ls:close].strip() != "":                         # closing brace shares a line: split it
        return text[:close] + "\n" + ent + base + text[close:]
    return text[:ls] + ent + text[ls:]


def _lua_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- the trigger
def _table_open(text, name):
    m = re.search(r"\b%s\s*=\s*" % re.escape(name), text)
    if not m or text[m.end()] != "{":
        raise MizError("no %s table found" % name)
    return m.end()


def find_script_trigger(mission_text, mapres_text, filename):
    """True if a trigger already runs `filename` with DO SCRIPT FILE."""
    mission = X.parse_mission(mission_text)
    mp = _parse_table(mapres_text, "mapResource")
    keys = {k for k, v in mp.items() if v == filename}
    for rule in (mission.get("trigrules") or {}).values():
        for act in (rule.get("actions") or {}).values():
            if act.get("predicate") == "a_do_script_file" and act.get("file") in keys:
                return True
    return False


def _parse_table(text, name):
    m = re.search(r"\b%s\s*=" % re.escape(name), text)
    if not m:
        raise MizError("no %s found" % name)
    p = X._Lua(text)
    p.i = m.end()
    return p.value()


def add_script_trigger(mission_text, mapres_text, filename, comment):
    """-> (new mission text, new mapResource text, ResKey).  Caller checks find_script_trigger first."""
    root = mission_text.index("{", re.search(r"\bmission\s*=", mission_text).end())
    mission = X.parse_mission(mission_text)
    existing = (mission.get("trigrules") or {})
    n = max([int(k) for k in existing] or [0]) + 1
    m = re.search(r'\["maxDictId"\]\s*=\s*(\d+)', mission_text)
    if not m:
        raise MizError("no maxDictId found")
    mp = _parse_table(mapres_text, "mapResource")
    k = int(m.group(1)) + 1
    while "ResKey_Action_%d" % k in mp:
        k += 1
    reskey = "ResKey_Action_%d" % k

    t = mission_text[:m.start(1)] + str(k) + mission_text[m.end(1):]

    # trigrules
    op = child_table(t, root, "trigrules")
    if not op:
        raise MizError("no trigrules table")
    rule = ["[%d] = " % n, "{",
            "\t[\"rules\"] = {},",
            "\t[\"comment\"] = %s," % _lua_str(comment),
            "\t[\"eventlist\"] = \"mission start\",",
            "\t[\"actions\"] = ", "\t{",
            "\t\t[1] = ", "\t\t{",
            "\t\t\t[\"predicate\"] = \"a_do_script_file\",",
            "\t\t\t[\"file\"] = %s," % _lua_str(reskey),
            "\t\t}, -- end of [1]",
            "\t}, -- end of [\"actions\"]",
            "\t[\"predicate\"] = \"triggerOnce\",",
            "}, -- end of [%d]" % n]
    t = insert_entry(t, op[0], rule)

    # trig.*
    def trig_sub(name):
        tr = child_table(t, root, "trig")
        if not tr:
            raise MizError("no trig table")
        sub = child_table(t, tr[0], name)
        if not sub:
            raise MizError("no trig.%s table" % name)
        return sub

    act = 'a_do_script_file(getValueResourceByKey("%s")); mission.trig.events["mission start"][%d]=nil;' % (reskey, n)
    t = insert_entry(t, trig_sub("actions")[0], ["[%d] = %s," % (n, _lua_str(act))])
    cond = "if mission.trig.conditions[%d]() then mission.trig.actions[%d]() end" % (n, n)
    ev = trig_sub("events")
    sub = child_table(t, ev[0], "mission start")
    if sub:
        t = insert_entry(t, sub[0], ["[%d] = %s," % (n, _lua_str(cond))])
    else:
        t = insert_entry(t, ev[0], ["[\"mission start\"] = ", "{", "\t[%d] = %s," % (n, _lua_str(cond)),
                                    "}, -- end of [\"mission start\"]"])
    t = insert_entry(t, trig_sub("flag")[0], ["[%d] = true," % n])
    t = insert_entry(t, trig_sub("conditions")[0], ["[%d] = %s," % (n, _lua_str("return(true)"))])

    # mapResource
    mo = _table_open(mapres_text, "mapResource")
    mr = insert_entry(mapres_text, mo, ["[%s] = %s," % (_lua_str(reskey), _lua_str(filename))])
    return t, mr, reskey


def _verify(mission_text, mapres_text, filename):
    mission = X.parse_mission(mission_text)
    if not find_script_trigger(mission_text, mapres_text, filename):
        raise MizError("the new trigger is not there after writing")
    trig = mission["trig"]
    rules = mission["trigrules"]
    for name in ("actions", "flag", "conditions"):
        if len(trig[name]) < len(rules):
            raise MizError("trig.%s is shorter than trigrules" % name)
    if len(trig["events"].get("mission start", {})) < 1:
        raise MizError("trig.events has no mission start entry")


# ---------------------------------------------------------------- the .miz
def _backup_name(miz):
    bak = miz + ".bak"
    k = 1
    while os.path.exists(bak):
        k += 1
        bak = "%s.bak%d" % (miz, k)
    return bak


def update_miz(miz, images_dir=None, script_path=None, trigger_comment="Trigger Load Orienteering Race Script"):
    """Add kneeboard PNGs (KNEEBOARD/IMAGES/) and/or a MISSION START trigger that runs the script.

    Returns a dict: backup, pages, script ('added', 'already', 'replaced' or None).  One backup of the
    original is made; on any failure the .miz is left exactly as it was."""
    names = []
    if images_dir:
        names = sorted(n for n in os.listdir(images_dir) if n.lower().endswith(".png"))
        if not names:
            raise MizError("no PNGs in %s" % images_dir)
    script_name = os.path.basename(script_path) if script_path else None
    result = {"backup": None, "pages": len(names), "script": None}
    with zipfile.ZipFile(miz) as zin:
        entries = {i.filename: i for i in zin.infolist()}
        mission = zin.read("mission").decode("utf-8")
        mapres = zin.read("l10n/DEFAULT/mapResource").decode("utf-8") if \
            "l10n/DEFAULT/mapResource" in entries else "mapResource = {}\n"
        new_mission, new_mapres = mission, mapres
        replace = {}
        if script_path:
            with open(script_path, "rb") as f:
                script_bytes = f.read()
            if find_script_trigger(mission, mapres, script_name):
                result["script"] = "replaced"                # trigger exists: just refresh the script
            else:
                new_mission, new_mapres, _ = add_script_trigger(mission, mapres, script_name, trigger_comment)
                _verify(new_mission, new_mapres, script_name)
                result["script"] = "added"
            replace["mission"] = new_mission.encode("utf-8")
            replace["l10n/DEFAULT/mapResource"] = new_mapres.encode("utf-8")
            replace["l10n/DEFAULT/" + script_name] = script_bytes
        newpages = {"KNEEBOARD/IMAGES/" + n for n in names}
        bak = _backup_name(miz)
        shutil.copyfile(miz, bak)
        result["backup"] = bak
        tmp = miz + ".new"
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                written = set()
                for item in zin.infolist():
                    if item.filename in newpages:
                        continue
                    if item.filename in replace:
                        zout.writestr(item.filename, replace[item.filename])
                    else:
                        zout.writestr(item, zin.read(item.filename))
                    written.add(item.filename)
                for fn, data in replace.items():
                    if fn not in written:
                        zout.writestr(fn, data)
                for n in names:
                    zout.write(os.path.join(images_dir, n), "KNEEBOARD/IMAGES/" + n)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    os.replace(tmp, miz)
    return result
