"""Saved key sequences the agent can replay at emulator speed instead of thinking.

The agent's expensive resource is the vision model: 5-20 s per turn. The emulator runs at
~1300 fps, ~22x realtime. So any stretch of play that is the SAME every time -- the startup
menus, walking a corridor already mapped, mashing attack through a fight -- is pure waste
when re-derived a frame at a time by a 27B. Recorded once, it replays in seconds.

Three things make this safe rather than a footgun:

1. **A route verifies that it arrived.** The old blind startup sequence pressed `space` at
   screens that only answer to `return_key`, had no idea it had failed, and left the agent
   staring at a title screen for dozens of turns. Every route here carries a postcondition
   -- the scene it must end in -- and replay() returns False if it is not met, so the caller
   falls back to thinking instead of acting on a fantasy.
2. **A route knows where it starts.** Replaying from the wrong place is worse than not
   replaying, so a route records the scene it was recorded from and refuses to run elsewhere.
3. **`until` routes for the repetitive-but-not-fixed case.** Combat is "hit, hit, hit until
   something changes" -- not a fixed length. Those repeat one key until the scene changes,
   the screen stops matching, or a cap is hit.

Storage is `emu/routes/<name>.json`. Recording happens in the agent: whenever it gets from
one scene to another, the keys that did it become a candidate route.
"""
import json, pathlib, time

HERE = pathlib.Path(__file__).resolve().parent
ROUTES = HERE / "routes"


def _build_agnostic(scene):
    """`FLOOR05.MES:ja` and `FLOOR05.MES` are the same room in two builds.

    A route is a path through the GAME, not through one compilation of it, so matching drops
    the build suffix. This is what lets a route recorded on the translated image walk the
    patched one at emulator speed -- the whole reason the swap experiment was stuck playing
    the intro by hand.
    """
    return scene[:-3] if isinstance(scene, str) and scene.endswith(":ja") else scene


def _slug(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))[:80]


def save(name, keys, from_scene, to_scene, kind="fixed", note="", to_stack_min=None):
    """Persist a route. `keys` is a list of key names, or of [key, hold, then] triples.

    `to_stack_min` is the image-agnostic postcondition: "at least N scripts resident". The
    boot route needs it because the first room is a different scene NAME on a translated
    image (FLOOR05.MES) than on one carrying the original (FLOOR05.MES:ja), and a route
    with no postcondition at all would report success no matter what happened -- the exact
    self-certifying check this module exists to avoid.
    """
    ROUTES.mkdir(exist_ok=True)
    norm = [k if isinstance(k, (list, tuple)) else [k, 6, 36] for k in keys]
    p = ROUTES / f"{_slug(name)}.json"
    p.write_text(json.dumps({
        "name": name, "kind": kind, "keys": norm,
        "from_scene": from_scene, "to_scene": to_scene, "to_stack_min": to_stack_min,
        "note": note, "recorded": time.time(), "runs": 0, "ok": 0}, ensure_ascii=False, indent=1))
    return p


def load_all():
    if not ROUTES.is_dir():
        return []
    out = []
    for p in sorted(ROUTES.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            pass
    return out


def find(from_scene, to_scene=None):
    """Routes that start where we are (and optionally end where we want)."""
    a = _build_agnostic(from_scene)
    b = _build_agnostic(to_scene)
    return [r for r in load_all()
            if _build_agnostic(r.get("from_scene")) == a
            and (b is None or _build_agnostic(r.get("to_scene")) == b)]


def chain(from_scene, to_scene, max_hops=8):
    """A sequence of recorded routes leading from one scene to another, or [].

    Breadth-first over what has actually been walked, so the shortest known path wins. This
    is the alternative to reloading a save-state: a state carries the scripts that were
    resident when it was taken and silently injects them into a run on a different image,
    which invalidated a whole experiment. Replaying the keys instead reaches the same place
    on WHATEVER image is mounted, which is exactly what a test harness wants.
    """
    if _build_agnostic(from_scene) == _build_agnostic(to_scene):
        return []
    all_r = load_all()
    edges = {}
    for r in all_r:
        a = _build_agnostic(r.get("from_scene"))
        if a:
            edges.setdefault(a, []).append(r)
    goal = _build_agnostic(to_scene)
    seen = {_build_agnostic(from_scene)}
    queue = [(_build_agnostic(from_scene), [])]
    while queue:
        here, path = queue.pop(0)
        if len(path) >= max_hops:
            continue
        for r in edges.get(here, []):
            nxt = _build_agnostic(r.get("to_scene"))
            if not nxt or nxt in seen:
                continue
            if nxt == goal:
                return path + [r]
            seen.add(nxt)
            queue.append((nxt, path + [r]))
    return []


def _bump(route, ok):
    """Keep a hit rate on disk. A route that keeps failing is a route to stop trusting."""
    try:
        p = ROUTES / f"{_slug(route['name'])}.json"
        d = json.loads(p.read_text())
        d["runs"] = d.get("runs", 0) + 1
        d["ok"] = d.get("ok", 0) + (1 if ok else 0)
        d["last"] = time.time()
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    except Exception:
        pass


def replay(route, press, run, scene_now, stack_now=None, max_keys=400):
    """Replay a route. Returns (ok, why).

    `press(key, hold, then)` and `run(n)` come from the Game; `scene_now()` returns the
    current scene name (state.identify) -- that is what makes arrival checkable rather than
    assumed. Costs no model time at all.
    """
    here = scene_now()
    if route.get("from_scene") and _build_agnostic(here) != _build_agnostic(route["from_scene"]):
        return False, f"wrong place: in {here}, route starts in {route['from_scene']}"
    keys = route.get("keys", [])[:max_keys]
    if route.get("kind") == "until":
        # combat and other "repeat until something happens" stretches
        k, hold, then = keys[0] if keys else ["space", 6, 36]
        for _ in range(route.get("cap", 60)):
            press(k, hold, then)
            if scene_now() != here:
                _bump(route, True)
                return True, f"scene changed to {scene_now()}"
        _bump(route, False)
        return False, "cap reached, scene never changed"
    for k, hold, then in keys:
        press(k, hold, then)
    run(30)
    got = scene_now()
    want = route.get("to_scene")
    need = route.get("to_stack_min")
    if want is not None:
        hit = _build_agnostic(got) == _build_agnostic(want)
        ok, why = hit, (f"arrived in {got}" if hit else f"expected {want}, got {got}")
    elif need is not None and stack_now is not None:
        n = len(stack_now() or [])
        ok, why = n >= need, f"{n} scripts resident, needed {need}"
    else:
        ok, why = False, "route has no postcondition -- refusing to claim success"
    _bump(route, ok)
    return ok, why


if __name__ == "__main__":
    rs = load_all()
    if not rs:
        print("no routes recorded yet")
    for r in rs:
        runs, ok = r.get("runs", 0), r.get("ok", 0)
        rate = f"{100*ok/runs:.0f}%" if runs else "-"
        print(f"{r['name']:44s} {r.get('kind'):6s} {len(r.get('keys',[])):>3} keys  "
              f"{str(r.get('from_scene'))[:18]:20s} -> {str(r.get('to_scene'))[:18]:20s} {rate}")
