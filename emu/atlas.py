#!/usr/bin/env python3
"""Floor map and walkthrough — what a human keeps in their head, and the model didn't have.

Why this is separate from `knowledge.py`. That module holds goals, facts and bugs — knowledge
about the WORLD, not tied to a place. Here it's knowledge tied to a CELL: what's visible from
here, where you can go from here, what's been tried, which spot is key. Mixing them loses
both: a list without coordinates can't be drawn, and a map without goals isn't worth reading.

⚠️ Why this only became possible now. The old cell key was a perceptual hash of the view
window, and it didn't match on return to the same spot — torches are animated. So the
direction table barely filled in. Now both coordinates are read from memory
(`state.where()`), the key is exact, and the map finally accumulates instead of falling apart.

Storage — `atlas.json`, survives deaths, reloads and restarts.

  floors[scene].cells["x,y"]   seen, first_turn, exits{heading: open|wall}, note, key
  walkthrough[]                steps: text, scene, cell, status (todo|now|done), turn

The map is drawn twice, differently: `render()` — a compact grid for the model's prompt,
`as_json()` — the same for the panel. One dataset, two consumers.
"""
import heapq, json, os, pathlib, re, time

import meta

HERE = pathlib.Path(__file__).resolve().parent
# WW_ATLAS: a run that must not touch the tree (play_accept.py) keeps its walk elsewhere.
STORE = pathlib.Path(os.environ.get("WW_ATLAS") or HERE / "atlas.json")
MAX_STEPS = 40
MAX_NOTE = 90

# what a cell looks like on the grid
KEY_CH, SEEN_CH, EDGE_CH, VOID = "*", "·", "?", " "
# The player is drawn as an ARROW, not a dot: the heading is known exactly, and "where am I"
# without "which way am I facing" forces a guess at exactly what's already been read from
# memory. The grid grows right along X and down along Y, compass (state.STEP):
# 0 = x+1, 1 = y-1, 2 = x-1, 3 = y+1.
ARROW = {0: ">", 1: "^", 2: "<", 3: "v"}
HERE_CH = "@"                      # if the heading is somehow unknown


def load():
    try:
        d = json.loads(STORE.read_text())
    except Exception:
        d = {}
    d.setdefault("floors", {})
    d.setdefault("walkthrough", [])
    d.setdefault("alias", {})
    _merge_by_sig(d)
    return d


def _merge_by_sig(d):
    """One floor — one map, no matter what the running scene is called.

    ⚠️ Floors were stored by `.mes` name, and the name changes at the slightest thing: step
    into a shop — `SHP_5A.MES`, into combat — `SENTO00.MES` (SENTO = 戦闘 "battle", not 銭湯
    "bathhouse": caught it from a combat frame, signal_audit), and every time a NEW floor was
    created with the same 225 cells and zeroed-out visited state. In live data this piled up
    into seven "floors" sharing the same map signature `9c91b26648be4385`: 126 visited cells
    in one and one each in the rest. Distances, the frontier and the walkthrough restarted
    from scratch after every shop visit — hence "navigation is kind of bad". The real floor
    key is the map's own signature (`state.floormap()['sig']`), the scene name is just a label.
    """
    by = {}
    for k, f in d["floors"].items():
        sig = f.get("map_sig")
        if sig:
            by.setdefault(sig, []).append(k)
    for sig, keys in by.items():
        if len(keys) < 2:
            continue
        keys.sort(key=lambda k: (-sum(1 for c in d["floors"][k]["cells"].values()
                                      if c.get("seen")),
                                 -sum(c.get("seen", 0) for c in d["floors"][k]["cells"].values()),
                                 len(k), k))
        main, rest = keys[0], keys[1:]
        for k in rest:
            _merge_floor(d["floors"][main], d["floors"].pop(k))
            d["alias"][k] = main
        d["floors"][main].setdefault("scenes", [])
        for k in [main] + rest:
            if k not in d["floors"][main]["scenes"]:
                d["floors"][main]["scenes"].append(k)


def _merge_floor(dst, src):
    for k, c in src.get("cells", {}).items():
        t = dst.setdefault("cells", {}).setdefault(k, {"seen": 0, "exits": {}, "note": "",
                                                       "key": ""})
        t["seen"] = t.get("seen", 0) + c.get("seen", 0)
        if c.get("first_turn") is not None:
            t["first_turn"] = min(t.get("first_turn", c["first_turn"]), c["first_turn"])
        for fld in ("note", "key", "event", "kind"):
            if c.get(fld) and not t.get(fld):
                t[fld] = c[fld]
        for side, m in (c.get("meas") or {}).items():
            tm = t.setdefault("meas", {}).setdefault(side, {"ok": 0, "no": 0})
            tm["ok"] += m.get("ok", 0); tm["no"] += m.get("no", 0)
        if c.get("poi"):
            tp = t.setdefault("poi", {"kind": "other", "title": "", "info": []})
            tp["kind"] = c["poi"].get("kind") or tp["kind"]
            tp["title"] = tp["title"] or c["poi"].get("title", "")
            for line in c["poi"].get("info", []):
                if line not in tp["info"]:
                    tp["info"].append(line)
            del tp["info"][:-8]
    for name, m in (src.get("monsters") or {}).items():
        t = dst.setdefault("monsters", {}).setdefault(name, {})
        for fld, v in m.items():
            if isinstance(v, int):
                t[fld] = t.get(fld, 0) + v
            elif v and not t.get(fld):
                t[fld] = v


def save(d):
    tmp = STORE.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    tmp.replace(STORE)


def _floor(d, scene):
    key = d.get("alias", {}).get(scene or "?", scene or "?")
    return d["floors"].setdefault(key, {"cells": {}})


def floor_key(d, scene):
    return d.get("alias", {}).get(scene or "?", scene or "?")


def _cell(d, scene, x, y):
    return _floor(d, scene)["cells"].setdefault(f"{x},{y}",
                                                {"seen": 0, "exits": {}, "note": "", "key": ""})


def visit(d, scene, x, y, turn):
    c = _cell(d, scene, x, y)
    c["seen"] += 1
    c.setdefault("first_turn", turn)
    return c


def note_exit(d, scene, x, y, facing, moved):
    """From this cell, facing this way, did the step go through or hit a wall.

    ⚠️ Written ONLY on an actual keypress. A neighbouring cell known only to exist is marked
    on the grid as `?`, not as a passage: "a step there would land" and "you can walk there"
    are different claims, and the model has already conflated them in its own favor once.
    """
    if facing is None:
        return
    ex = _cell(d, scene, x, y)["exits"]
    if not moved and ex.get(str(facing)) == "open":
        return                      # same reasoning: a passage isn't demoted to a wall
    ex[str(facing)] = "open" if moved else "wall"


POI_KINDS = ("shop", "npc", "stairs", "chest", "door", "scene", "danger", "other")


def set_poi(d, scene, x, y, kind=None, title=None, info=None):
    """What this place is and what it offers. Lives forever, survives a summary and a restart.

    ⚠️ Separate from `note`, and this isn't duplication. `note` is an impression of the cell
    on this turn ("wall, torches"), fine to overwrite. POI is what you come back here for:
    a shop and its prices, a staircase down, who's standing here. That can't be erased, so
    `info` gets APPENDED to, never replaced: shop prices get learned one at a time.
    """
    c = _cell(d, scene, x, y)
    poi = c.setdefault("poi", {"kind": "other", "title": "", "info": []})
    if kind in POI_KINDS:
        poi["kind"] = kind
    if title:
        poi["title"] = str(title)[:80]
    if info:
        line = str(info)[:200]
        if line not in poi["info"]:
            poi["info"].append(line)
            del poi["info"][:-8]
    return poi


def note_monster(d, scene, name, outcome=None, note=None, level=None):
    """What lives in this ZONE. Not a POI and not a cell.

    ⚠️ Encounters are random: the same mole pops up now at (9,12), now at (4,7), and logging
    it against a cell just litters the map with dots that won't be there tomorrow. Danger is
    tied to the AREA — a floor has its own bestiary. That's where decisions like "don't go
    here below level N" and "you can grind here" come from.
    """
    m = _floor(d, scene).setdefault("monsters", {})
    name = str(name).strip()[:60]
    if not name:
        return None
    rec = m.setdefault(name, {"seen": 0, "won": 0, "lost": 0, "note": "", "level": None})
    rec["seen"] += 1
    if outcome == "won":
        rec["won"] += 1
    elif outcome in ("lost", "fled"):
        rec["lost"] += 1
    if note:
        rec["note"] = str(note)[:120]
    if level:
        try:
            rec["level"] = int(level)
        except Exception:
            pass
    return rec


def monsters(d, scene):
    return _floor(d, scene).get("monsters") or {}


def pois(d, scene):
    out = []
    for k, c in _floor(d, scene)["cells"].items():
        if c.get("poi") and (c["poi"].get("title") or c["poi"].get("info")):
            x, y = (int(v) for v in k.split(","))
            out.append(((x, y), c["poi"]))
    return sorted(out)


def describe(d, scene, x, y, note=None, key=None):
    c = _cell(d, scene, x, y)
    if note:
        c["note"] = str(note)[:MAX_NOTE]
    if key:
        c["key"] = str(key)[:MAX_NOTE]
    return c


def fix(d, scene, x, y, note=None, key=None, retest=None):
    """Fix a cell's record when the previous one turned out to be wrong.

    ⚠️ DIFFERENT fields are fixed in different ways, and this isn't nitpicking.
      note / key -- the model's opinion of the place. An empty string clears the note.
      exits      -- a MEASUREMENT: this was pressed and it showed whether it passed or hit a
                    wall. Letting an opinion overwrite a measurement means losing the only
                    thing here worth trusting. So a wrong side can't be rewritten -- it can
                    only be RESET to "untried", and then it gets re-measured by a keypress.
    """
    c = _cell(d, scene, x, y)
    if note is not None:
        c["note"] = str(note)[:MAX_NOTE]
    if key is not None:
        c["key"] = str(key)[:MAX_NOTE]
    for side in (retest or []):
        c["exits"].pop(str(side), None)
    return c


def forget_cell(d, scene, x, y):
    """Remove a cell entirely — when it ended up on the map by mistake."""
    return _floor(d, scene)["cells"].pop(f"{x},{y}", None) is not None


def seed(d, scene, fm, turn=None):
    """Pour into the floor map what the game itself already keeps in memory.

    ⚠️ This supersedes the way the map was built until now -- by stepping. Feeling out walls
    on foot made sense while the layout was unknown; now it's read whole
    (`state.floormap`), and the agent sees the floor right away instead of after a hundred
    turns. Measurements are NOT thrown out in the process: every step is still checked
    against the map, and a mismatch is a finding (a locked door, an address bug, a special
    engine case), not grounds to silently overwrite.
    """
    sig = fm.get("sig")
    key = floor_key(d, scene)
    if sig:
        # same floor under a different scene name -- latch onto what's already accumulated
        for k, f in d["floors"].items():
            if k != key and f.get("map_sig") == sig:
                d.setdefault("alias", {})[scene or "?"] = k
                key = k
                break
    fl = _floor(d, scene)
    fl["from_game"] = True
    fl["map_sig"] = sig
    names = fl.setdefault("scenes", [])
    if scene and scene not in names:
        names.append(scene)
    for (x, y), c in fm["cells"].items():
        cell = _cell(d, scene, x, y)
        # ⚠️ ONLY the game's own layout gets re-poured. Measurements (`meas`) and anything
        # the agent wrote (note/key/poi) stay put: seed is called every turn, and letting it
        # overwrite what's accumulated would mean forgetting which door didn't open, every turn.
        cell["exits"] = {str(s): ("wall" if v == WALL_NIB else "open")
                         for s, v in c["sides"].items()}
        cell["kind"] = {str(s): v for s, v in c["sides"].items()}
        if c["event"]:
            cell["event"] = c["event"]
    return fl


def disagreement(d, scene, x, y, side, measured):
    """Did the step disagree with the game's map? Then it's a finding, not a reason to rewrite the map."""
    fl = _floor(d, scene)
    if not fl.get("from_game"):
        return None
    known = (fl["cells"].get(f"{x},{y}") or {}).get("exits", {}).get(str(side))
    if known and measured and known != measured:
        return f"({x},{y}) side {side}: the game says {known}, the step showed {measured}"
    return None


def unvisited_reachable(d, scene, x, y, limit=10):
    """Cells you CAN reach per the game's map but haven't visited yet. The real frontier."""
    cells = _floor(d, scene)["cells"]
    dist = distances(d, scene, x, y)
    out = []
    for (cx, cy), dv in sorted(dist.items(), key=lambda kv: kv[1]):
        c = cells.get(f"{cx},{cy}")
        if c is not None and not c.get("seen"):
            out.append({"cell": (cx, cy), "steps": dv, "event": c.get("event")})
    return out[:limit]


def unexplored(d, scene, x, y):
    """Headings from this cell that have never been tried."""
    ex = _cell(d, scene, x, y)["exits"]
    return [f for f in (0, 1, 2, 3) if str(f) not in ex]



# --- geometry: edges between cells ----------------------------------------------------
# A cell's side is an EDGE, shared with the neighbour, not a property of the cell. It's
# recorded twice: at (x,y) as side f and at the neighbour as side f+2. It used to draw only
# its own half, and the same wall looked different from two sides. Now the halves merge:
# a "wall" measurement outweighs a "passage" (a wall is seen for certain, a "went through"
# could have been screen noise), and "untried" is weaker than both.
OPP = {0: 2, 1: 3, 2: 0, 3: 1}


def neighbour(x, y, side):
    dx, dy = {0: (1, 0), 1: (0, -1), 2: (-1, 0), 3: (0, 1)}[int(side)]
    return x + dx, y + dy


# --- what the game itself records vs what the steps show -----------------------------
# A cell's side in the game's memory isn't "yes/no", it's a nibble 0..9. FLOOR05 has four
# values in play, and they're DIFFERENT in meaning; verified against the log (132 confirmed
# transitions between neighbouring cells, taken from coordinate changes, not the model's
# opinion):
#     9 -- wall, not a single transition;
#     0 -- clean passage, 83 transitions;
#     3 -- also passable, 46 transitions;
#     1 -- 7 edges on the whole floor and NOT ONE transition.
# ⚠️ FIXED 2026-09-10 BY WATCHING FRAMES, not by counting transitions. The earlier conclusion
# "3 is just another kind of opening, 1 is the door" was wrong and flipped the picture: the
# floor has 106 door sides vs 14, and all of them were drawn as an open passage. Measurement
# (emu, FLOOR05, cell (13,3), four consecutive turns): the side coded 3 is a DOUBLE-LEAF ARCH,
# the side coded 0 is a dark opening and corridor. So: 9 wall, 0 passage, 3 door.
# Code 1 is left as a door: not one passage in 132 observations, so at best a door that
# hasn't been opened, at worst a locked one.
WALL_NIB = 9
DOOR_NIB = (1, 3)


def _block_after():
    # a tuning lever: how many clean failures close an edge (see meta.LEVERS)
    return int(meta.T("BLOCK_AFTER", 2))




BLOCK_AFTER = 2           # default; the live value comes through _block_after()


def _meas(d, scene, x, y, side):
    return _cell(d, scene, x, y).setdefault("meas", {}).setdefault(str(int(side)),
                                                                  {"ok": 0, "no": 0})


def edge_meas(d, scene, x, y, side):
    """How many times this edge was crossed and how many times it was cleanly blocked — from both ends."""
    cells = _floor(d, scene)["cells"]
    nx, ny = neighbour(x, y, side)
    ok = no = 0
    for cx, cy, sd in ((x, y, int(side)), (nx, ny, OPP[int(side)])):
        m = ((cells.get(f"{cx},{cy}") or {}).get("meas") or {}).get(str(sd)) or {}
        ok += m.get("ok", 0); no += m.get("no", 0)
    return ok, no


def nibble(d, scene, x, y, side):
    """Raw side value from the game's map (at either end of the edge)."""
    cells = _floor(d, scene)["cells"]
    v = ((cells.get(f"{x},{y}") or {}).get("kind") or {}).get(str(int(side)))
    if v is None:
        nx, ny = neighbour(x, y, side)
        v = ((cells.get(f"{nx},{ny}") or {}).get("kind") or {}).get(str(OPP[int(side)]))
    return v


def note_step(d, scene, x, y, side, moved, clean=True):
    """Record a step's MEASUREMENT through an edge — alongside the game's map, not instead of it.

    ⚠️ This is what was missing, and why a locked door was unknowable. The rule "passage
    outweighs wall" (see `edge`) is correct — a failed step is ambiguous, the input could
    have gone into a text box or an encounter. But the consequence was that NO failure could
    make it into the map, and a route was built through the same non-opening door over and
    over. Now a failure counts if it's CLEAN: the screen didn't budge on the keypress, so
    nothing intercepted the input and what we hit was really the opening.
    """
    m = _meas(d, scene, x, y, side)
    if moved:
        m["ok"] += 1
    elif clean:
        m["no"] += 1
    if not _floor(d, scene).get("from_game"):
        note_exit(d, scene, x, y, side, moved)
    return m


def blocked_edges(d, scene):
    """Edges the game treats as a passage but the steps don't. Ready-made findings for the model."""
    cells = _floor(d, scene)["cells"]
    out, seen = [], set()
    for k in cells:
        cx, cy = (int(v) for v in k.split(","))
        for s in (0, 1, 2, 3):
            ok, no = edge_meas(d, scene, cx, cy, s)
            if ok or no < _block_after():
                continue
            if edge(d, scene, cx, cy, s) != "blocked":
                continue
            n = neighbour(cx, cy, s)
            # one edge, two ends -- otherwise the same door shows up twice
            key = tuple(sorted([(cx, cy), n]))
            if key in seen:
                continue
            seen.add(key)
            out.append({"cell": [cx, cy], "side": s, "to": list(n), "tries": no,
                        "nib": nibble(d, scene, cx, cy, s)})
    return out


def edge(d, scene, x, y, side):
    """Edge state: 'open' | 'door' | 'blocked' | 'wall' | None (nothing to say).

    The order of checks is the order of evidence reliability, from strong to weak.
    """
    cells = _floor(d, scene)["cells"]
    a = (cells.get(f"{x},{y}") or {}).get("exits", {}).get(str(side))
    nx, ny = neighbour(x, y, side)
    b = (cells.get(f"{nx},{ny}") or {}).get("exits", {}).get(str(OPP[int(side)]))
    ok, no = edge_meas(d, scene, x, y, side)
    door = nibble(d, scene, x, y, side) in DOOR_NIB
    if ok:
        # ⚠️ A door stays a door even after it's been opened. This used to be an
        # unconditional "open", and a passed-through door would vanish from the map -- in
        # the game the leaf is still there, on the schematic it's a plain opening. What an
        # edge looks like is a property of the game's map, not of our history; "walked it or
        # not" affects the route's COST, see step_cost().
        return "door" if door else "open"  # passed through -- you don't walk through walls, settled
    # ⚠️ PASSAGE OUTWEIGHS WALL, and this rule had to be flipped based on the evidence. It
    # used to be the other way around ("a wall is seen for certain"), and a live run got
    # `0: wall` at (12,3) on an edge the agent had walked through ten turns earlier. The
    # reason is that a failed step is ambiguous: the input could have gone into an open text
    # box, a menu, an encounter, a cutscene -- and all of that got logged as a wall. A
    # successful step, though, is unambiguous: you don't walk through walls. Positive
    # evidence here outweighs negative evidence.
    # ⚠️ "Wall" means DIFFERENT things for the two sources, and they must not be confused. On
    # a floor read from the game's memory, a wall is nibble 9, solid evidence. On a floor
    # mapped on foot, a wall is a failed step, and that's ambiguous (text box, menu,
    # encounter), so the old rule still applies there: passage outweighs wall.
    if "wall" in (a, b) and _floor(d, scene).get("from_game"):
        return "wall"
    if no >= _block_after():
        return "blocked"                   # the game says passage, but the step cleanly doesn't go
    if "open" in (a, b):
        return "door" if door else "open"
    if "wall" in (a, b):
        return "wall"
    return None


PASSABLE = ("open", "door")


def step_cost(d, scene, x, y, side):
    """What this step costs when building a route, or None — can't go here.

    A door isn't forbidden, just expensive: a detour around it up to four extra cells long
    wins out. This removes the main source of dead ends — a plan through a door that's never
    been opened, when a working detour corridor exists.
    """
    st = edge(d, scene, x, y, side)
    if st not in PASSABLE:
        return None
    ok, _ = edge_meas(d, scene, x, y, side)
    # A door is expensive exactly until it's been opened: an opened one is no worse than an opening.
    c = 1.0 if (st == "open" or ok) else float(meta.T("DOOR_COST", 4.0))
    return c if ok else c + 0.05           # prefer the well-trodden path when lengths are equal


def distances(d, scene, x, y):
    """Steps from the player to every known cell — only along MEASURED passages.

    This is the single most useful thing the model didn't have: with it, a route doesn't
    need to be invented — just follow the decreasing numbers. (Borrowed from the
    llm_pokemon_scaffold scaffold, where it's called StepsToReach.)
    """
    if x is None or y is None:
        return {}
    seen = {(x, y): 0}
    q = [(x, y)]
    while q:
        cx, cy = q.pop(0)
        for side in (0, 1, 2, 3):
            if edge(d, scene, cx, cy, side) not in PASSABLE:
                continue
            n = neighbour(cx, cy, side)
            if n not in seen:
                seen[n] = seen[(cx, cy)] + 1
                q.append(n)
    return seen


def orphan_exits(d, scene):
    """Edges marked as a PASSAGE that lead to no cell — a sign the map was written by
    something other than coordinates again.

    The invariant is simple: if a step there went through, we ENDED UP there, so the cell is
    marked visited. Both map bugs found so far (movement keyed off an image hash, and a
    heading read before it turned within a keypress sequence) produced exactly this kind of
    orphan — a passage into (12,2) while (12,2) was never actually reached. One honest
    exception: a step that led into another scene (a staircase) — then there's not supposed
    to be a cell on THIS floor at all.
    """
    cells = _floor(d, scene)["cells"]
    out = []
    for k, c in cells.items():
        cx, cy = (int(v) for v in k.split(","))
        for side, st in (c.get("exits") or {}).items():
            if st != "open":
                continue
            n = neighbour(cx, cy, side)
            if f"{n[0]},{n[1]}" not in cells:
                out.append({"cell": [cx, cy], "side": int(side), "to": list(n)})
    return out


def path(d, scene, x, y, tx, ty):
    """List of sides leading from (x,y) to (tx,ty) along ALREADY MEASURED passages, or None.

    Exists exactly because the model now has distances: with them, a route doesn't need to
    be invented. Still has to be walked with verification, though -- see Agent.do_goto.
    """
    if None in (x, y, tx, ty):
        return None
    # ⚠️ Not a breadth-first search, but Dijkstra by COST. The difference shows exactly where
    # it used to break: the shortest path through an unverified door and the longer one via a
    # well-trodden corridor were "equally good" to a BFS, but in practice the first one hit
    # the door and the agent got stuck. Costs live in `step_cost`.
    prev = {(x, y): None}
    best = {(x, y): 0.0}
    pq = [(0.0, (x, y))]
    while pq:
        cost, cur = heapq.heappop(pq)
        if cur == (tx, ty):
            out = []
            while prev[cur] is not None:
                cur, side = prev[cur]
                out.append(side)
            return out[::-1]
        if cost > best.get(cur, 1e9):
            continue
        for side in (0, 1, 2, 3):
            c = step_cost(d, scene, cur[0], cur[1], side)
            if c is None:
                continue
            n = neighbour(cur[0], cur[1], side)
            nc = cost + c
            if nc < best.get(n, 1e9):
                best[n] = nc
                prev[n] = (cur, side)
                heapq.heappush(pq, (nc, n))
    return None


def frontier(d, scene, x=None, y=None):
    """Where else you can go, sorted by proximity. Two DIFFERENT things, don't mix them up:

      open  — a step there already passed, but that cell has never been stood on. A sure bet.
      untried — that side has never been tried. Might turn out to be a wall.
    """
    cells = _floor(d, scene)["cells"]
    dist = distances(d, scene, x, y)
    out = []
    for k, c in cells.items():
        cx, cy = (int(v) for v in k.split(","))
        for side in (0, 1, 2, 3):
            st = edge(d, scene, cx, cy, side)
            n = neighbour(cx, cy, side)
            if st in PASSABLE and f"{n[0]},{n[1]}" not in cells:
                out.append({"kind": "open", "cell": (cx, cy), "side": side,
                            "to": n, "steps": dist.get((cx, cy))})
            elif st is None:
                out.append({"kind": "untried", "cell": (cx, cy), "side": side,
                            "to": n, "steps": dist.get((cx, cy))})
    out.sort(key=lambda e: (e["kind"] != "open",
                            999 if e["steps"] is None else e["steps"]))
    return out


def unexplored(d, scene, x, y):
    """Headings from this cell that have never been tried (counting both ends of the edge)."""
    return [f for f in (0, 1, 2, 3) if edge(d, scene, x, y, f) is None]


# --- walkthrough ---------------------------------------------------------------------
_STOP = {"try", "to", "the", "a", "an", "from", "at", "into", "toward", "towards", "and",
         "go", "move", "step", "heading", "head", "dir", "direction", "cell", "find",
         "passage", "through", "check", "see", "if", "is", "there", "explore"}


def _tok(s):
    w = [t for t in re.sub(r"[^a-z0-9]+", " ", str(s).lower()).split() if t not in _STOP]
    return set(w)


def _same(a, b):
    """Same step, even if rephrased.

    ⚠️ The old comparison — the first 60 characters — let "Try heading 3 from cell (12,3)…"
    and "Try dir 3 from (12,3)…" pass as two different steps, and over 40 turns the
    walkthrough grew five lines about the same thing. Compare meaning instead: significant
    words and numbers.
    """
    if a.strip().lower()[:60] == b.strip().lower()[:60]:
        return True
    ta, tb = _tok(a), _tok(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def walk_add(d, text, scene=None, cell=None, turn=None):
    text = str(text).strip()
    if not text:
        return
    for s in d["walkthrough"]:
        if _same(s["text"], text):
            return
    d["walkthrough"].append({"text": text[:160], "scene": scene, "cell": cell,
                             "status": "todo", "turn": turn, "ts": int(time.time())})
    del d["walkthrough"][:-MAX_STEPS]


def walk_done(d, text, turn=None):
    for s in d["walkthrough"]:
        if _same(s["text"], str(text)):
            s["status"] = "done"
            s["done_turn"] = turn
            return True
    return False


def replace_walk(d, steps, scene=None, turn=None):
    """Rewrite the plan wholesale — done by the summary step, not a single turn.

    ⚠️ The walkthrough used to only be APPENDED to, one step per turn, and it turned into a
    junk pile of micro-tasks ("try side 3 from (12,3)") — essentially a duplicate of the map.
    The plan should answer a different question: where we are in the playthrough and what's
    next, including ASSUMPTIONS. Hence three statuses: done — done, now — this is where we
    stand, next — what's ahead; an assumption is flagged in the step's own text.
    """
    out = []
    for i, st in enumerate(steps or []):
        if isinstance(st, str):
            st = {"text": st}
        t = str(st.get("text", "")).strip()[:160]
        if not t:
            continue
        prev = next((x for x in d["walkthrough"] if _same(x["text"], t)), None)
        out.append({"text": t,
                    "status": st.get("status") or (prev or {}).get("status") or "todo",
                    "scene": st.get("scene") or (prev or {}).get("scene") or scene,
                    "cell": st.get("cell") or (prev or {}).get("cell"),
                    "turn": (prev or {}).get("turn", turn),
                    "ts": (prev or {}).get("ts", int(time.time()))})
    d["walkthrough"] = out[:MAX_STEPS]
    return d


def current(d):
    """First unfinished step — what the model is doing right now."""
    for s in d["walkthrough"]:
        if s["status"] != "done":
            return s
    return None


# --- two renderings: words for the model, a picture for the human ---------------------
SIDE_NAME = {0: "0 (X+1)", 1: "1 (Y-1)", 2: "2 (X-1)", 3: "3 (Y+1)"}
STATE_RU = {"open": "PASSAGE", "wall": "WALL", "door": "DOOR",
            "blocked": "BLOCKED (step there doesn't go)", None: "untried"}


def _pl(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def brief(d, scene, x=None, y=None, facing=None, limit=24):
    """The map in WORDS — what the model reads.

    ⚠️ Why not a picture. A grid of glyphs requires counting columns, and the model counted
    them wrong: it was looking at a door and saw a "?" on the grid. llm_pokemon_scaffold
    (cicero225) hit the exact same trap and came out of it the same way: a compact grid for
    the human, an expanded list for the model, where each cell's coordinate is written right
    next to it. Same here. The picture stays, but it's secondary.
    """
    fl = _floor(d, scene)
    cells = fl["cells"]
    if not cells:
        return "floor map is empty — first time here, everything around is unexplored"
    dist = distances(d, scene, x, y)
    if fl.get("from_game"):
        return _brief_from_game(d, scene, x, y, facing, dist)
    lines = [f"MAP {scene}: {len(cells)} "
             f"{_pl(len(cells), 'cell cleared', 'cells passed', 'cells passed')}. "
             f"Sides: 0=X+1, 1=Y-1, 2=X-1, 3=Y+1.",
             "The coordinate is written on each cell — no need to count columns."
             "«steps to you» = path length along already measured passages;"
             "to get anywhere, follow the DECREASING numbers."]
    order = sorted(cells.items(),
                   key=lambda kv: (999 if dist.get(tuple(int(v) for v in kv[0].split(","))) is None
                                   else dist[tuple(int(v) for v in kv[0].split(","))], kv[0]))
    for k, c in order[:limit]:
        cx, cy = (int(v) for v in k.split(","))
        head = f"({cx},{cy})"
        if (cx, cy) == (x, y):
            head += f" YOU ARE HERE, facing side {facing}"
        else:
            dv = dist.get((cx, cy))
            head += f" steps to you: {dv}" if dv is not None else "path not measured yet"
        sides = " | ".join(f"side {SIDE_NAME[s]}: {STATE_RU[edge(d, scene, cx, cy, s)]}"
                           for s in (0, 1, 2, 3))
        lines.append(f"{head} | {sides}")
        if c.get("key"):
            lines.append(f"      ★ KEY POINT: {c['key']}")
        if c.get("note"):
            lines.append(f"      note: {c['note']}")
    fr = frontier(d, scene, x, y)
    if fr:
        lines.append("WHERE TO GO — open directions, nearest first:")
        for e in fr[:8]:
            cx, cy = e["cell"]
            st = ("passage exists, but not yet passed" if e["kind"] == "open"
                  else "side has never been tried")
            near = ("" if e["steps"] is None else
                    "— you are on it" if e["steps"] == 0 else
                    f", {e['steps']} {_pl(e['steps'], 'step', 'step', 'steps')} from here")
            lines.append(f"  from ({cx},{cy}) heading {e['side']} -> "
                         f"({e['to'][0]},{e['to'][1]}): {st}{near}")
    else:
        lines.append("No open directions left on the map — floor completed"
                     "or exit to another scene.")
    return "\n".join(lines)


def _brief_from_game(d, scene, x, y, facing, dist):
    """Map read from the game's memory. Showing all 225 cells at once is a wall of text; show
    what's needed for a decision instead: where we stand, what's nearby, where we haven't gone."""
    cells = _floor(d, scene)["cells"]
    seen = sum(1 for c in cells.values() if c.get("seen"))
    L = [f"MAP {scene} read FROM THE GAME'S MEMORY — the floor layout is known in full, "
         f"no need to guess walls. Cells: {len(cells)}, visited: {seen}.",
         "Sides: 0=X+1, 1=Y-1, 2=X-1, 3=Y+1."]
    if x is not None:
        here = cells.get(f"{x},{y}") or {}
        ways = [s for s in (0, 1, 2, 3) if edge(d, scene, x, y, s) == "open"]
        doors = [s for s in (0, 1, 2, 3) if edge(d, scene, x, y, s) == "door"]
        shut = [s for s in (0, 1, 2, 3) if edge(d, scene, x, y, s) == "blocked"]
        L.append(f"YOU are at ({x},{y}), facing side {facing}. "
                 f"Sides leading out from here: {', '.join(map(str, ways)) or 'nowhere'}."
                 + (f" DOORS (never opened): {', '.join(map(str, doors))}." if doors else "")
                 + (f" BLOCKED (step doesn't go): {', '.join(map(str, shut))}." if shut else ""))
        near = [(k, c) for k, c in cells.items()
                if c.get("seen") or c.get("note") or c.get("key")]
        near = [(k, c) for k, c in near
                if abs(int(k.split(',')[0]) - x) <= 3 and abs(int(k.split(',')[1]) - y) <= 3]
        if near:
            L.append("Near places already visited:")
            for k, c in sorted(near)[:10]:
                bits = []
                if c.get("key"):
                    bits.append("★ " + c["key"])
                if c.get("note"):
                    bits.append(c["note"])
                dv = dist.get(tuple(int(v) for v in k.split(",")))
                L.append(f"  ({k})" + (f" {dv} step(s)" if dv else "")
                         + (" — " + "; ".join(bits) if bits else ""))
    fr = unvisited_reachable(d, scene, x, y)
    if fr:
        L.append("UNVISITED (reachable, go via `goto`):")
        for e in fr:
            ev = f", something's here (event {e['event']})" if e.get("event") else ""
            L.append(f"  ({e['cell'][0]},{e['cell'][1]}) — {e['steps']} step(s){ev}")
    else:
        L.append("No unvisited reachable cells remain: exiting the floor —"
                 "via event (ladder) or plot.")
    ps = pois(d, scene)
    if ps:
        L.append("WHAT'S WHERE ON THIS FLOOR (your own notes, they don't disappear):")
        for (px, py), poi in ps[:12]:
            dv = dist.get((px, py))
            L.append(f"  ({px},{py}) [{poi.get('kind','?')}] {poi.get('title','')}"
                     + (f" — {dv} step(s)" if dv is not None else "")
                     + ("; " + " · ".join(poi.get("info") or []) if poi.get("info") else ""))
    mon = monsters(d, scene)
    if mon:
        L.append("WHO HANGS OUT IN THIS ZONE (skirmishes are random, location varies each time):")
        for nm, r in sorted(mon.items(), key=lambda kv: -kv[1]["seen"])[:8]:
            bits = [f"{r['seen']} encounters", f"won {r['won']}"]
            if r["lost"]:
                bits.append(f"LOST {r['lost']}")
            if r.get("level"):
                bits.append(f"safe from level {r['level']}")
            if r.get("note"):
                bits.append(r["note"])
            L.append(f"  {nm}: " + ", ".join(bits))
    shut = blocked_edges(d, scene)
    if shut:
        L.append("DID NOT OPEN (game map treats it as a passage, but the step to it doesn't go —"
                 "key required, event, or it's not a door at all). No routes are built to here:")
        for e in shut[:8]:
            L.append(f"  ({e['cell'][0]},{e['cell'][1]}) side {e['side']} -> "
                     f"({e['to'][0]},{e['to'][1]}): hit a wall {e['tries']} time(s)")
    ev = [(k, c["event"]) for k, c in cells.items() if c.get("event")]
    if ev:
        L.append(f"Cells with events on this floor: {len(ev)} "
                 f"(stairs, doors, scenes) — for example "
                 + ", ".join(f"({k})" for k, _ in sorted(ev)[:8]))
    return "\n".join(L)


# edges are drawn between cells, like on a real dungeon map:
# solid line — measured wall, gap — measured passage, dotted — untried.
_H = {"wall": "─────", "open": "     ", "door": "──┄──", "blocked": "──╳──",
      None: "╌╌╌╌╌"}
_V = {"wall": "│", "open": " ", "door": "┄", "blocked": "╳", None: "╎"}
ARROW = {0: "▶", 1: "▲", 2: "◀", 3: "▼"}
HERE_CH = "◆"
KEY_CH, SEEN_CH = "★", "·"


def render(d, scene, x=None, y=None, radius=6, facing=None):
    """The real map: coordinates on the outside, walls and passages — BETWEEN cells.

    ⚠️ The old version put one glyph per cell, and that conflated the cell with the walls
    around it: "?" meant "some sides are still untried", and a person standing in a doorway
    saw a question mark on the map. A wall is a property of an EDGE, and it must be drawn on
    the edge. Cells never visited aren't drawn at all — empty space just means "unknown".
    """
    cells = _floor(d, scene)["cells"]
    if not cells:
        return "Floor map is empty — first visit"
    pts = [tuple(int(v) for v in k.split(",")) for k in cells]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
    if x is not None:
        x0, x1 = max(x0, x - radius), min(x1, x + radius)
        y0, y1 = max(y0, y - radius), min(y1, y + radius)

    def known(xx, yy):
        return f"{xx},{yy}" in cells

    def content(xx, yy):
        if not known(xx, yy):
            return "     "
        if (xx, yy) == (x, y):
            ch = ARROW.get(facing, HERE_CH)
        else:
            c = cells[f"{xx},{yy}"]
            # ⚠️ The layout is now known in full, so "the cell is on the map" no longer
            # means "we've been there". A dot only where we stood; empty otherwise.
            ch = (KEY_CH if c.get("key") else "•" if c.get("event")
                  else SEEN_CH if c.get("seen") else " ")
        return f"  {ch}  "

    out = [f"map {scene}", "        " + "".join(f"{i:^6d}" for i in range(x0, x1 + 1))]
    for yy in range(y0, y1 + 1):
        top, mid = "", ""
        for xx in range(x0, x1 + 1):
            up = known(xx, yy) or known(xx, yy - 1)
            left = known(xx, yy) or known(xx - 1, yy)
            top += ("┼" if up else " ") + (_H[edge(d, scene, xx, yy, 1)] if up else "     ")
            mid += (_V[edge(d, scene, xx, yy, 2)] if left else " ") + content(xx, yy)
        top += "┼" if known(x1, yy) or known(x1, yy - 1) else " "
        mid += _V[edge(d, scene, x1, yy, 0)] if known(x1, yy) else " "
        out.append("        " + top)
        out.append(f"  Y={yy:>3d} " + mid)
    bot = ""
    for xx in range(x0, x1 + 1):
        dn = known(xx, y1)
        bot += ("┼" if dn else " ") + (_H[edge(d, scene, xx, y1, 3)] if dn else "     ")
    bot += "┼" if known(x1, y1) else " "
    out.append("        " + bot)
    out.append("───── wall · gap — passage · ──┄── door (never opened) ·"
               "──╳── can't step there · ╌╌╌╌╌ not attempted")
    out.append(f"  {ARROW.get(facing, HERE_CH)} you and which way you're facing · {KEY_CH} key point · "
               f"{SEEN_CH} visited · • something's here (event) · "
               f"empty — layout is known, but we haven't been there · "
               f"up = side 1, right = side 0")
    return "\n".join(out)


def as_json(d, scene, x=None, y=None, facing=None):
    """Same for the panel: cells as-is plus MERGED edges, so the panel doesn't compute them
    itself and drift from the prompt (two different renderers have already shown different
    things once)."""
    f = _floor(d, scene)
    edges = {}
    for k in f["cells"]:
        cx, cy = (int(v) for v in k.split(","))
        edges[k] = {str(s): edge(d, scene, cx, cy, s) for s in (0, 1, 2, 3)}
    return {"scene": scene, "here": [x, y], "facing": facing, "cells": f["cells"],
            "edges": edges, "orphans": orphan_exits(d, scene),
            "scenes": f.get("scenes") or [scene], "blocked": blocked_edges(d, scene),
            "monsters": f.get("monsters") or {}, "dist": {f"{a},{b}": v
                                     for (a, b), v in distances(d, scene, x, y).items()},
            "frontier": [{**e, "cell": list(e["cell"]), "to": list(e["to"])}
                         for e in frontier(d, scene, x, y)[:12]],
            "walkthrough": d["walkthrough"][-MAX_STEPS:]}
