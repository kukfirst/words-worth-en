#!/usr/bin/env python3
"""Workshop: the agent fixes not the game, but ITSELF.

Borrowed from working systems, not invented on the spot:

  * **autoagent** (thirdlayerinc) -- two roles, not one: the playing agent and a meta-agent
    that edits the harness. A HUMAN writes the directive for the meta-agent (they have
    `program.md`, we have `HARNESS.md`), every experiment lands in a journal with a
    keep/discard column, acceptance rules are strict, and there's a separate rule that "on a
    tie, the simpler thing wins" plus a ban on overfitting to one case.
  * **Voyager** -- a skill only enters the library after it has run ONCE and its stated
    condition held. Self-check before it's recorded.
  * **Darwin Goedel Machine / SICA** -- a change is accepted by MEASUREMENT, not by the
    model's opinion; rejected attempts are kept, because they're knowledge too.

Warning: what's DELIBERATELY missing here, and why. In DGM the meta-agent rewrites arbitrary
Python, and the benchmark is then re-run from scratch. Here the run is single and live, and a
broken harness kills the very thing we measure results with. So the editable surface is narrow
and typed: levers with bounds. The model doesn't write code -- it moves a lever, and every
lever's bounds are such that the run survives ANY value allowed within them.

Storage -- `tuning.json`: levers, the open experiment, and the attempt log.
"""
import json, pathlib, time

HERE = pathlib.Path(__file__).resolve().parent
STORE = HERE / "tuning.json"
DIRECTIVE = HERE / "HARNESS.md"
MAX_LEDGER = 60

# --- levers -----------------------------------------------------------------------------
# Each one must have bounds within which the run survives any value inside them.
# `what` explains what the lever controls; `tradeoff` -- what you pay by moving it either way.
# Without a tradeoff the model moves the lever blindly: it has no way to guess what it's giving up.
LEVERS = {
    "RECAP_EVERY": {
        "default": 20, "min": 8, "max": 60,
        "what": "turns between journal recaps (a recap rewrites goals, facts and the plan)",
        "tradeoff": "lower -- the plan stays fresher, but each recap costs a model call and "
                  "clears the accumulated conversation; higher -- cheaper, but the plan goes stale"},
    "REVIEW_EVERY": {
        "default": 18, "min": 8, "max": 60,
        "what": "turns between strategy reviews (rewrites the playbook)",
        "tradeoff": "lower -- you notice a broken tactic sooner; higher -- cheaper"},
    "AUTO_ADVANCE_MAX": {
        "default": 6, "min": 0, "max": 20,
        "what": "how many replies in a row can be skipped with space without calling the model",
        "tradeoff": "higher -- dialogues scroll by cheaply, but you can sleep through a fork "
                  "or a fight; 0 -- think over every line, expensive"},
    "NO_PROGRESS_LIMIT": {
        "default": 14, "min": 6, "max": 40,
        "what": "turns without a screen change before emergency recovery kicks in",
        "tradeoff": "lower -- you escape a hang faster, but more often break a legitimate "
                  "pause (shop menu, a long cutscene); higher -- you hang longer"},
    "OVERRIDE_AFTER": {
        "default": 4, "min": 2, "max": 12,
        "what": "how many times a key must be pressed and fail before the harness stops "
               "letting the model choose it",
        "tradeoff": "lower -- you break out of a dead end faster, but you override a plan "
                  "that was never actually tested; higher -- you keep banging longer"},
    "GOTO_BUDGET": {
        "default": 26, "min": 6, "max": 60,
        "what": "how many steps `goto` covers in one model turn",
        "tradeoff": "higher -- long trips in one call; but the longer you walk blind, "
                  "the later you notice you've been intercepted"},
    "GOTO_REPLANS": {
        "default": 3, "min": 0, "max": 8,
        "what": "how many times `goto` will re-route around a closed edge before "
               "handing control back to the model",
        "tradeoff": "higher -- it fights through the maze itself; lower -- it calls the "
                  "model sooner when something's off"},
    "BLOCK_AFTER": {
        "default": 2, "min": 1, "max": 6,
        "what": "how many CLEAN failed steps close an edge on the map",
        "tradeoff": "1 -- the map learns fast, but one fluke closes a live passage; "
                  "higher -- more reliable, but you keep hitting the same door longer"},
    "DOOR_COST": {
        "default": 4.0, "min": 1.0, "max": 12.0, "float": True,
        "what": "how many ordinary steps the router estimates a never-opened door crossing "
               "is worth",
        "tradeoff": "higher -- you'd rather detour; 1.0 -- a door is no different from a "
                  "corridor and the route keeps running into it again"},
    "QUIET_CONFIRM": {
        "default": 42, "min": 12, "max": 120,
        "what": "how many frames without a single VRAM write are needed to believe the "
               "game finished drawing the screen (first threshold -- 12, then a confirm pass)",
        "tradeoff": "lower -- the turn is faster, but the model more often sees a half-drawn "
                  "screen: an empty text box, half a palette; higher -- every turn costs "
                  "a few dozen more frames. The `screen woke up after silence` counter "
                  "shows whether this happens at all"},
    "HIST_TURNS": {
        "default": 24, "min": 4, "max": 60,
        "what": "how many recent turns stay in the model's conversation in full",
        "tradeoff": "higher -- the model remembers context, but the prompt grows and the call costs more"},
    "FORCE_MODEL_AFTER": {
        "default": 8, "min": 0, "max": 40,
        "what": "hard ceiling: how many turns in a row the harness can resolve ITSELF by any "
               "means (skipping dialogue, a black screen, a lost frame) before the model "
               "gets called by force. 0 means no ceiling",
        "tradeoff": "lower -- the model sees the screen more often and notices sooner that "
                  "something's wrong; but every call costs ~20s and tokens. Higher -- cheaper "
                  "and faster, but the agent goes blind for longer. Note: AUTO_ADVANCE_MAX caps "
                  "ONE burst and resets after every model reply; this lever sits on top of "
                  "all the mechanisms at once. Watch the 'consecutive without model, max' counter",
    },
    "THINK": {
        # Warning: the cap of 2 is BY EXPLICIT HUMAN REQUEST: xhigh must never be set on this model.
        # The lever's bounds are exactly what makes editing safe, so the ban lives
        # here, not in a reminder to the model.
        "default": 1, "min": 0, "max": 2,
        "what": "how much the model is allowed to think before answering: 0 -- no thought at "
               "all (the template closes <think> right away), 1 -- low, 2 -- medium. xhigh is "
               "forbidden. The sampling recipe changes together with this lever, as the model card dictates",
        "tradeoff": "0 -- the turn is cheaper and shorter, but the model stops noticing things "
                  "visible only after reasoning (a trap, a repeat, 'I've been here before'); "
                  "3 -- reasoning is thorough, but the answer may not fit the token budget. "
                  "Warning: the Qwen3.8 card warns that in multi-turn agentic tasks "
                  "lower effort doesn't necessarily save time -- the number of retries grows"},
    "TEMP": {
        "default": 1.0, "min": 0.2, "max": 1.2, "float": True,
        "what": "sampling temperature; 1.0 is the Qwen3.8 card's recipe for the thinking mode",
        "tradeoff": "lower -- answers are more predictable and repetitive: in a maze that means "
                  "stubbornly repeating the same non-working move; higher -- more varied "
                  "and riskier"},
}


# --- prompt paragraphs --------------------------------------------------------------------
# Warning: the second, NON-numeric editable surface. A lever answers "how often / how much",
# a paragraph answers "how to play". Acceptance rules are the same: one experiment, a window,
# a score, keep/discard. The bounds here aren't numeric but length and placement: the
# paragraph is inserted into its OWN spot in the prompt and can neither delete the rest of the
# prompt nor grow large enough to crowd it out.
SLOTS = {
    "APPROACH": {
        "max_len": 1800,
        "what": "the paragraph in the player prompt about HOW to progress through the game: "
               "what counts as progress, in what order to do things, when to fight, when to grind",
        "tradeoff": "more specific -- the agent wanders aimlessly less often, but it's easier to "
                  "lock it into one scenario that won't work on a different floor; more general "
                  "-- more flexible, but it goes back to walking corridors with no plan",
        "default": """HOW THIS GAME ACTUALLY PROGRESSES -- you are PLAYING it, not wandering it.
This is a story RPG: the plot advances only when you make it advance, and it advances in a
FIXED ORDER. Three things move it, and nothing else does:
1. TALK. Speak to every NPC you meet, and keep talking to the same one until the lines start
   repeating -- a new line is progress, a repeat means that person is done for now. After any
   event, the SAME people often have NEW lines: go back to them.
2. TRIGGER. Events sit on specific cells, doors and items. If a door is barred or someone asks
   for something, that IS the next task: find the key/item, then come back to that exact cell.
   Doing things out of order simply does nothing -- if an action changes nothing at all, the
   game is telling you the prerequisite is missing, not that the action is wrong.
3. FIGHT AND GROW. Somewhere ahead is an enemy far stronger than you -- a gate you cannot pass
   at your level. Losing to it costs the run, so LEVEL UP BEFORE the gate: fight the weak
   enemies you can already beat, watch EXP and Level in the status panel, buy/equip better
   gear with the gold, and only then go back to the fight you lost. If HP is low, retreat and
   heal before exploring further; dying is the one thing that actually loses progress.
Exploring a corridor you already mapped is NOT progress. Before you move, ask which of the
three you are doing right now, and say it in "goal".""",
    },
}


def slot_text(name):
    """Current paragraph text: workshop-edited or default."""
    spec = SLOTS.get(name)
    if not spec:
        return ""
    if _CACHE["d"] is None or _mtime() != _CACHE["mtime"]:
        load()
    return ((_CACHE["d"].get("slots") or {}).get(name) or spec["default"]).strip()


def set_slot(d, name, text):
    """Write a paragraph. Returns ((was, now), None) or (None, rejection reason)."""
    spec = SLOTS.get(name)
    if not spec:
        return None, f"no such paragraph: {name}"
    text = str(text or "").strip()
    if len(text) < 80:
        return None, "too short: the paragraph must explain an approach, not a slogan"
    if len(text) > spec["max_len"]:
        return None, f"longer than {spec['max_len']} characters"
    was = slot_text(name)
    if text == was:
        return None, "text unchanged"
    d.setdefault("slots", {})[name] = text
    return (was, text), None


_CACHE = {"d": None, "mtime": None}


def _mtime():
    try:
        return STORE.stat().st_mtime
    except Exception:
        return None


def load():
    """Read workshop state. Warning: returns the SAME object `T()` sees:
    otherwise an in-memory lever edit wouldn't take effect until the file was written, and
    half the harness would keep working off the stale value with no one the wiser."""
    try:
        d = json.loads(STORE.read_text())
    except Exception:
        d = {}
    d.setdefault("levers", {})
    d.setdefault("ledger", [])
    d.setdefault("open", None)
    d.setdefault("meta_turn", 0)
    _CACHE["d"], _CACHE["mtime"] = d, _mtime()
    return d


def save(d):
    tmp = STORE.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    tmp.replace(STORE)
    _CACHE["d"], _CACHE["mtime"] = d, _mtime()


def T(name, fallback=None):
    """Current lever value. The file is re-read if it was edited externally."""
    spec = LEVERS.get(name)
    if _CACHE["d"] is None or _mtime() != _CACHE["mtime"]:
        load()
    v = (_CACHE["d"].get("levers") or {}).get(name)
    if v is None:
        return spec["default"] if spec else fallback
    return v


def clamp(name, value):
    """Value clamped to the lever's bounds, or None if it isn't a number at all."""
    spec = LEVERS.get(name)
    if not spec:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    v = max(spec["min"], min(spec["max"], v))
    return v if spec.get("float") else int(round(v))


def set_lever(d, name, value):
    v = clamp(name, value)
    if v is None:
        return None, f"no such lever «{name}», or the value isn't a number"
    was = T(name)
    d.setdefault("levers", {})[name] = v
    return (was, v), None


# --- measurement --------------------------------------------------------------------------
# Warning: the weights are set HERE and duplicated for the human in HARNESS.md, because there
# is no "objective" metric for the run: finds are rare and clustered, and floor movement is
# frequent and cheap. Honestly naming the weights and handing them to the human beats
# pretending the score is objective.
WEIGHTS = {"finds": 5.0, "new_cells": 1.0, "step_ratio": 6.0,
           "resets": -3.0, "broken_replies": -0.5}


def counters(rows, run, lo, hi, finds=0, resets=0, broken=0):
    """What happened on turns (lo, hi] of this run -- from the journal, not the model's opinion.

    Warning: "turns" means hi - lo, NOT the number of journal rows. A row is written only
    where the model decided: autopilot skipping through replies, and a turn dropped because of
    a broken reply, leave no row. On a live run this once produced "4 turns, 4 broken replies"
    over a forty-turn window, and the workshop honestly read that as "the model didn't work the
    whole period" and moved a lever to chase that fiction. A counter that lies is worse than a
    missing one: people adapt to it.
    """
    inwin = [r for r in rows if r.get("run") == run and lo < r.get("turn", 0) <= hi]
    before = {(r.get("scene"), r.get("location")) for r in rows
              if r.get("run") == run and r.get("turn", 0) <= lo}
    cells = {(r.get("scene"), r.get("location")) for r in inwin if r.get("location")}
    turns = max(0, int(hi) - int(lo))
    steps = sum(1 for r in inwin if r.get("stepped"))
    return {"turns": turns,
            "model_decided": len(inwin),
            "new_cells": len(cells - before),
            "step_ratio": round(steps / turns, 3) if turns else 0.0,
            "finds": finds, "resets": resets, "broken_replies": broken}


def score(c):
    return round(sum(WEIGHTS[k] * c.get(k, 0) for k in WEIGHTS), 2)


# --- experiment -----------------------------------------------------------------------------
def _restore(d, e):
    """Put back what was moved: a lever -- into levers, a paragraph -- into slots."""
    if e.get("kind") == "paragraph":
        d.setdefault("slots", {})[e["lever"]] = e["was"]
    else:
        d.setdefault("levers", {})[e["lever"]] = e["was"]


def open_experiment(d, turn, name, was, now, why, expect, baseline, run=None, kind="lever"):
    # `run` -- which run this was opened in. Without it, an experiment that survived a restart
    # would later be closed by comparing ONE run's baseline against ANOTHER run's window;
    # turn numbers reset to zero on restart, counters too, and the verdict came out of thin air.
    d["open"] = {"turn": turn, "lever": name, "was": was, "now": now, "kind": kind,
                 "hypothesis": str(why)[:300], "expect": str(expect)[:200],
                 "baseline": baseline, "run": run, "ts": time.time()}
    return d["open"]


def drop_stale(d, run, turn=0):
    """Drop an experiment opened in a DIFFERENT run. Nothing left to finish counting it with."""
    e = d.get("open")
    if not e or e.get("run") in (None, run):
        return None
    return withdraw(d, turn, f"run restarted ({e.get('run')} -> {run}): window "
                             f"incomplete, nothing to compare")


def close_experiment(d, turn, after):
    """Accept or roll back. Rules are strict and the same for everyone -- like autoagent's.

    Warning: no "seems like it got better". Score went up -- accept. Score didn't go up --
    roll back. Separately, GUARDS: if resets or broken_replies went up, roll back regardless
    of score, because that's a sign the harness got worse at staying on its feet, and a lucky
    find can easily outweigh it.
    """
    e = d.get("open")
    if not e:
        return None
    base, s_before, s_after = e["baseline"], score(e["baseline"]), score(after)
    guard = []
    if after.get("resets", 0) > base.get("resets", 0):
        guard.append("resets increased")
    if after.get("broken_replies", 0) > base.get("broken_replies", 0):
        guard.append("broken replies increased")
    if guard:
        verdict, why = "rolled_back", "; ".join(guard)
    elif s_after > s_before:
        verdict, why = "accepted", f"score {s_before} -> {s_after}"
    elif s_after == s_before:
        # "on a tie, the simpler thing wins". For a lever, simpler = closer to default;
        # for a paragraph there's nothing to compare, so simpler = revert to the old text.
        if e.get("kind") == "paragraph":
            simpler = False
        else:
            dflt = LEVERS[e["lever"]]["default"]
            simpler = abs(e["now"] - dflt) < abs(e["was"] - dflt)
        verdict = "accepted" if simpler else "rolled_back"
        why = f"score tied ({s_after}), " + ("value closer to default" if simpler
                                             else "simpler to revert")
    else:
        verdict, why = "rolled_back", f"score {s_before} -> {s_after}"
    if verdict == "rolled_back":
        _restore(d, e)
    row = {**e, "closed_at": turn, "after": after, "score_before": s_before, "score_after": s_after,
           "verdict": verdict, "reason": why}
    d["ledger"].append(row)
    del d["ledger"][:-MAX_LEDGER]
    d["open"] = None
    return row


def withdraw(d, turn, reason):
    """Drop the open experiment without scoring it: it was measured wrong, not that the lever is bad.

    Differs from a rollback in that the ledger records the REASON for dropping it, and the
    lever isn't marked "doesn't help in this direction" -- it just wasn't tested.
    """
    e = d.get("open")
    if not e:
        return None
    _restore(d, e)
    row = {**e, "closed_at": turn, "after": None, "score_before": score(e["baseline"]), "score_after": None,
           "verdict": "withdrawn", "reason": reason}
    d["ledger"].append(row)
    del d["ledger"][:-MAX_LEDGER]
    d["open"] = None
    return row


def _short(v, n=70):
    """Value for a ledger line: a paragraph prints its start, not the whole text."""
    s = str(v)
    return s if len(s) <= n else s[:n].replace("\n", " ") + "…"


def render(d, limit=8):
    """The workshop in words -- what the meta-call reads."""
    L = ["HARNESS LEVERS (this is everything that can be changed; a value outside the bounds gets clamped):"]
    for k, spec in LEVERS.items():
        cur = T(k)
        mark = "" if cur == spec["default"] else f"  <- moved (default {spec['default']})"
        L.append(f"  {k} = {cur}  [{spec['min']}..{spec['max']}]{mark}")
        L.append(f"      what: {spec['what']}")
        L.append(f"      tradeoff: {spec['tradeoff']}")
    L.append("")
    L.append("PROMPT PARAGRAPHS (the second, NON-numeric editable surface: not 'how often' "
             "but 'how to play'). Rewritten wholesale, same acceptance rules as a lever:")
    for k, spec in SLOTS.items():
        cur = slot_text(k)
        mark = "" if cur == spec["default"].strip() else "  <- rewritten by the workshop"
        L.append(f"  {k} (up to {spec['max_len']} characters){mark}")
        L.append(f"      what: {spec['what']}")
        L.append(f"      tradeoff: {spec['tradeoff']}")
        L.append("      currently: " + cur.replace("\n", " ")[:600]
                 + ("…" if len(cur) > 600 else ""))
    L.append("")
    L.append("Window SCORE = " + " + ".join(f"{v}×{k}" for k, v in WEIGHTS.items()))
    e = d.get("open")
    if e:
        L.append(f"\nOPEN EXPERIMENT since turn {e['turn']}: {e['lever']} "
                 f"{_short(e['was'])} -> {_short(e['now'])} -- {e['hypothesis']}. "
                 f"Expected: {e['expect']}. "
                 f"Baseline: score {score(e['baseline'])} {e['baseline']}")
    led = d.get("ledger") or []
    if led:
        L.append("\nALREADY TRIED (don't repeat what was rolled back for the same reason):")
        for r in led[-limit:]:
            L.append(f"  turn {r['turn']}: {r['lever']} "
                     f"{_short(r['was'])}->{_short(r['now'])} -- "
                     f"{r['verdict'].upper()} ({r['reason']}). Why it was tried: {r['hypothesis']}")
    else:
        L.append("\nNo experiments yet -- this will be the first.")
    return "\n".join(L)


def directive():
    try:
        return DIRECTIVE.read_text()
    except Exception:
        return ""

# --- summary for the human -------------------------------------------------------------------
def summary(d, meta_every=40, turn=None, last_meta=None):
    """What the workshop has produced -- as one object. The panel and the text report both
    read this same object, so they don't diverge: two different retellings of the same data
    have already once shown something different."""
    led = d.get("ledger") or []
    kept = [r for r in led if r["verdict"] == "accepted"]
    rolled = [r for r in led if r["verdict"] == "rolled_back"]
    dropped = [r for r in led if r["verdict"] == "withdrawn"]
    # who set each moved value -- otherwise "RECAP_EVERY=12" says nothing
    who = {}
    for r in led:
        if r["verdict"] == "accepted":
            who[r["lever"]] = r
    moved = []
    for k, spec in LEVERS.items():
        cur = T(k)
        if cur == spec["default"]:
            continue
        r = who.get(k)
        moved.append({"lever": k, "value": cur, "default": spec["default"],
                      "set_by": (f"experiment at turn {r['turn']}: {r['hypothesis']}"
                                   if r else "human edit or a previous run")})
    # rewritten paragraphs -- alongside moved levers: it's a harness edit too
    for k, spec in SLOTS.items():
        cur = slot_text(k)
        if cur == spec["default"].strip():
            continue
        r = who.get(k)
        moved.append({"lever": k + " (paragraph)", "value": _short(cur, 120),
                      "default": "original text",
                      "set_by": (f"experiment at turn {r['turn']}: {r['hypothesis']}"
                                   if r else "human edit or a previous run")})
    trend = [{"at_turn": r.get("closed_at"), "score": r.get("score_after"),
              "label": f"{r['lever']}={_short(r['now'], 30)}", "verdict": r["verdict"]}
             for r in led if r.get("score_after") is not None]
    e = d.get("open")
    return {"experiments": len(led), "accepted": len(kept), "rolled_back": len(rolled),
            "withdrawn": len(dropped),
            "moved_levers": moved, "window_scores": trend[-10:],
            "running": ({"lever": e["lever"], "was": _short(e["was"], 90),
                             "now": _short(e["now"], 90),
                             "hypothesis": e["hypothesis"], "expect": e["expect"], "since_turn": e["turn"],
                             "baseline": score(e["baseline"])} if e else None),
            # Warning: the count runs from the CURRENT run's `last_meta`, not the file's
            # `meta_turn`: a new run's turn numbers start over, and the leftover file from the
            # previous one gave "next step in 71 turns" against a threshold of 40.
            "next_step_in": (max(0, meta_every - (turn - (last_meta if last_meta
                                                                 is not None else 0)))
                                    if turn is not None else None)}


def report(d, meta_every=40, turn=None, last_meta=None):
    """The same summary in words -- for the terminal."""
    s = summary(d, meta_every, turn, last_meta)
    L = [f"WORKSHOP: experiments {s['experiments']}, "
         f"accepted {s['accepted']}, rolled back {s['rolled_back']}"
         + (f", dropped as unmeasured {s['withdrawn']}" if s.get("withdrawn") else "")]
    if s["running"]:
        e = s["running"]
        L.append(f"\nCURRENTLY TESTING (since turn {e['since_turn']}, baseline {e['baseline']}):")
        L.append(f"  {e['lever']}: {e['was']} -> {e['now']}")
        L.append(f"  why: {e['hypothesis']}")
        L.append(f"  expecting: {e['expect']}")
    elif s["next_step_in"] is not None:
        L.append(f"\nNothing being tested right now; next step in "
                 f"{s['next_step_in']} turns")
    if s["moved_levers"]:
        L.append("\nWHAT'S OFF DEFAULT:")
        for m in s["moved_levers"]:
            L.append(f"  {m['lever']} = {m['value']} (default {m['default']}) "
                     f"-- {m['set_by']}")
    else:
        L.append("\nAll levers at default -- nothing has stuck yet.")
    if s["window_scores"]:
        L.append("\nSCORE BY WINDOW (further right is more recent):")
        for t in s["window_scores"]:
            mark = "+" if t["verdict"] == "accepted" else "-"
            L.append(f"  turn {t['at_turn']}: {t['score']:>7}  {mark} {t['label']}")
    led = d.get("ledger") or []
    if led:
        L.append("\nALREADY TRIED:")
        for r in led[-8:]:
            L.append(f"  turn {r['turn']}-{r['closed_at']}  {r['lever']} {r['was']}->{r['now']}"
                     f"  {r['verdict'].upper()}: {r['reason']}")
            L.append(f"      hypothesis was: {r['hypothesis']}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    t = None
    try:
        t = json.loads((HERE / "agent.json").read_text()).get("turn")
    except Exception:
        pass
    print(report(load(), turn=t))
    sys.exit(0)
