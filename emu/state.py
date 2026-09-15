"""Ground truth about the running game, read out of the emulator instead of the screen.

The screen is a guess; this is a fact. np2kai does not expose the game's memory through
the libretro memory interface (`get_memory_data(2)` hands back 3 MB of extended RAM that
the DOS game barely touches -- 3.2% non-zero, no script in it, and every other region
reports size 0). But `serialize()` dumps the whole machine in 2.8 ms, and the AI5 engine
keeps the `.mes` script it is executing in there **byte for byte**, so the compiled files
in `en/` are themselves the lookup table.

Two things come out of that, every turn, with no vision model in the loop:

  identify()  which scene is loaded -- START.MES, FLOOR05C.MES, ... All 84 scripts have
              distinct fingerprints, so this is exact, not a similarity score. Use it for
              "did the room change", which pixel hashes can only approximate.
  churn()     whether the machine is idle-waiting-for-input, busy, or genuinely wedged.
              A pixel hash cannot tell those apart, which is why "no new screen for N
              turns" kept firing on a game that was simply waiting for a keypress.

Scripts load back-to-back in a stack: START.MES sits at 0x095f0, is 11103 bytes, and the
next slot begins at 0x0c150 -- exactly len+1, rounded up to even. So one anchor is enough
to walk the whole stack; the expensive rescan only runs when the anchor no longer matches.

Self-test: `.venv/bin/python state.py [some.state]`
"""
import hashlib, os, pathlib
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "en"          # translated, compiled
ORIGINALS = HERE.parent / "work"      # the untouched Japanese .MES

FIRST_SLOT = 0x095f0      # where the first (system) script sits; verified across runs
SCAN_LIMIT = 0x40000      # rescan window: the game lives in low DOS memory
FP_OFF, FP_LEN = 64, 64   # fingerprint window -- all 84 scripts are distinct here
CHURN_BLOCK = 0x10000     # 64 KB blocks for the idle/busy/wedged classification
CHURN_SPAN = 0x100000     # classify over the low 1 MB only
VRAM_LO, VRAM_HI = 0xa0000, 0xc0000   # PC-98 text + graphics planes: the liveness signal
VM_LO = 0x09000           # AI5 interpreter working area, just below the first script slot


def _load_scripts():
    """Index BOTH builds.

    ⚠️ Indexing only the translated files made this sensor blind on any image carrying an
    original script -- an experiment that swapped one .mes in reported `scene=START.MES`
    forever, because the resident Japanese script matched nothing. Names of originals are
    suffixed `:ja`, so a lookup also tells you WHICH BUILD is actually loaded, which is
    precisely the check such an experiment needs and which had to be done by hand before.
    """
    out = {}
    for p in sorted(SCRIPTS.iterdir()):
        if p.name.endswith(".rkt.mes"):
            out[p.name[:-len(".rkt.mes")]] = p.read_bytes()
    if ORIGINALS.is_dir():
        for p in sorted(ORIGINALS.iterdir()):
            if p.name.endswith(".MES") and p.is_file():
                out[p.name + ":ja"] = p.read_bytes()
    # ⚠️ Knowing two builds isn't enough. An experiment that ASSEMBLES a third script
    # variant (emu/size_ladder.py, emu/bisect_crash.py) puts a file into the image that's in
    # neither en/ nor work/ -- and then identify() honestly fails to recognize the scene and
    # silently falls back to just START.MES. That reads as "the game never reached the
    # location", but actually means "the sensor doesn't know these bytes". The exact same
    # blindness the docstring above warns about, just one step further.
    # → the experiment passes its own .mes through WW_EXTRA_MES (":"-separated is fine).
    for extra in filter(None, os.environ.get("WW_EXTRA_MES", "").split(":")):
        q = pathlib.Path(extra)
        if q.is_file():
            out[q.name + ":var"] = q.read_bytes()
    return out


SCRIPT_BYTES = _load_scripts()
# fingerprint -> [names]. Near-identical variants DO collide in one 64-byte window
# (SENTO10A.MES and SENTO11.MES differ by 40 bytes in 22 KB), so a collision resolves by
# comparing more bytes at the slot rather than raising -- a sensor that dies on an
# unexpected pair is worse than one that looks a little harder.
_FP = {}
for _n, _b in SCRIPT_BYTES.items():
    if len(_b) >= FP_OFF + FP_LEN:
        _FP.setdefault(hashlib.blake2b(_b[FP_OFF:FP_OFF + FP_LEN],
                                       digest_size=8).digest(), []).append(_n)
_AMBIG = sum(1 for v in _FP.values() if len(v) > 1)

# corpus-unique needles, for the rescan that finds the anchor again after it moves
_seen = {}
for _n, _b in SCRIPT_BYTES.items():
    for _i in range(0, len(_b) - 24, 23):
        _seen.setdefault(_b[_i:_i + 24], []).append((_n, _i))
_NEEDLES = {}
for _g, _where in _seen.items():
    if len(_where) == 1:
        _n, _i = _where[0]
        _NEEDLES.setdefault(_n, []).append((_i, _g))
_NEEDLES = {n: v[:12] for n, v in _NEEDLES.items()}
del _seen


def _at(state, base):
    """Name of the script whose bytes start at `base`, or None. O(1) in the common case.

    A name ending in `:ja` is an ORIGINAL Japanese script -- so this also answers "which
    build is actually loaded", which is exactly what an experiment that swaps one .mes has
    to verify and previously had to check by hand.
    """
    if base < 0 or base + FP_OFF + FP_LEN > len(state):
        return None
    cands = _FP.get(hashlib.blake2b(state[base + FP_OFF:base + FP_OFF + FP_LEN],
                                    digest_size=8).digest())
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    for n in cands:                       # ambiguous window: compare a longer prefix
        b = SCRIPT_BYTES[n]
        k = min(len(b), 4096)
        if state[base:base + k] == b[:k]:
            return n
    return cands[0]


PROBE_AHEAD = 512         # ⚠️ not a guess, a measurement: see the comment in _next_slot


def _next_slot(base, name):
    """Start of the next slot — an ASSUMPTION that must be verified.

    ⚠️ "Back-to-back, rounded up to even" — a rule calibrated on ONE image. On the QA build
    it misses: `START.MES` (11,103 b) sits at 0x95f0, the arithmetic gives 0xC150, but
    `FLOOR05.MES:ja` actually sits at **0xC170** — 32 bytes further out. The miss didn't
    cause an error: walk() simply cut off and returned ['START.MES'], which reads as "the
    room hasn't loaded". Exactly the same thing would read if the game genuinely hadn't
    loaded the room — meaning the sensor was silently conflating two different situations.
    """
    n = len(SCRIPT_BYTES[name])
    return base + n + (n & 1)


def walk(state, first=FIRST_SLOT):
    """Script stack from `first`: [(base, name), ...], outermost first.

    Returns (stack, truncated?). `truncated` = reached a spot where the script isn't
    recognized, and it wasn't found within PROBE_AHEAD either. The caller must distinguish
    "the stack ended" from "the sensor lost the trail" — they used to look identical.
    """
    out, base, seen = [], first, set()
    while base not in seen:
        seen.add(base)
        name = _at(state, base)
        if name is None:
            # the arithmetic missed -- probe nearby for the next slot's start before
            # declaring the stack ended
            for d in range(2, PROBE_AHEAD, 2):
                name = _at(state, base + d)
                if name is not None:
                    base += d
                    break
            if name is None:
                return out, bool(out)
        out.append((base, name))
        base = _next_slot(base, name)
    return out, False


def resident(state, limit=SCAN_LIMIT):
    """All known scripts actually sitting in memory. ~120 ms, so only on a miss.

    ⚠️ Needle agreement is NOT ENOUGH. On the QA state, voting turned up five scripts, three
    of which were ghosts: `START.MES:ja` at 0x9625 (11 agreeing needles!) and `FLOOR05.MES`
    at 0xc1e9. Two builds of the same script share long byte runs, and needles land on a
    shifted base. So every candidate gets DOUBLE-CHECKED byte by byte — the ghosts drop out.
    """
    low = state[:limit]
    cands = {}
    for name, needles in _NEEDLES.items():
        agree = {}
        for off, g in needles:
            j = low.find(g)
            if j != -1:
                agree[j - off] = agree.get(j - off, 0) + 1
        for base, n in agree.items():
            if n >= 3 and base >= 0:
                cands.setdefault(base, []).append((n, name))
    out = []
    for base in sorted(cands):
        for _, name in sorted(cands[base], reverse=True):
            b = SCRIPT_BYTES[name]
            k = min(len(b), 4096)
            if state[base:base + k] == b[:k]:
                out.append((base, name))
                break
    return out


def rescan(state, limit=SCAN_LIMIT):
    """The stack when the anchor has drifted. Now a verified scan, not walk() from the lowest base.

    ⚠️ The old version found the lowest base and walked from it using the SAME arithmetic as
    walk(). So the "fallback path" relied on the same unverified rule and failed right along
    with the primary one: on the QA state both returned ['START.MES'], even though
    `FLOOR05.MES:ja` sat in memory at 0xC170.
    """
    return resident(state, limit)


def identify(state, first=FIRST_SLOT):
    """What is loaded right now.

    {"scene": <current scene>, "stack": [...], "anchor": <base to reuse next turn>,
     "rescanned": bool}

    `scene` is the innermost script -- the outermost slot is the always-resident system
    script (START.MES), the one on top of the stack is the room/event actually playing.
    Pass last turn's `anchor` back in to keep this on the O(1) path.
    """
    # ⚠️ The fast arithmetic path was removed FOR GOOD, and here's why. It relied on the
    # "next slot is back-to-back" rule, which isn't true on all images (see _next_slot), and
    # its misses looked like a ready-made short answer. Trying to fix that with a
    # "truncated" flag didn't help: the stack ALWAYS ends somewhere, the flag was always
    # true, and a full scan ran every time regardless -- just now disguised as an
    # optimization.
    # 118 ms against seconds per agent turn. Accuracy is cheaper here than a guess.
    stack = resident(state)
    rescanned = True
    return {"scene": stack[-1][1] if stack else None,
            "stack": [n for _, n in stack],
            "anchor": stack[0][0] if stack else first,
            "rescanned": rescanned}


# --- is it wedged, or just waiting for me? -----------------------------------------
def churn(states):
    """Classify a run of snapshots taken with NO input in between.

    A pixel hash says "nothing changed" for all four of these; they need different
    responses.

      busy    VRAM is being written -> the game is drawing. Healthy. Wait.
      idle    VRAM still, but the interpreter's working area moves -> the game is alive
              and waiting for a keypress. Healthy. Press something.
      dead    the machine still ticks (BIOS timers in low memory) but NOTHING draws and
              the interpreter is static -> the game has died and what is on screen is a
              stale framebuffer nobody ever cleared. Only a reload gets out of this.
      frozen  not a byte moves anywhere -> the emulator itself is wedged.

    ⚠️ `dead` is the case that cost us a whole debugging session. It looks exactly like a
    working game on screen -- the last frame is still sitting in VRAM, complete with the
    status panel -- so the agent kept "exploring" a corpse for 44 turns. Measured against
    a known-alive control on the same save-state:

        alive : blocks 0x0,0x10000,0x20000,0x60000,0xa0000,0xb0000,0xe0000 -> 2329 bytes
        dead  : block 0x0 only                                             ->  456 bytes

    VRAM (0xa0000/0xb0000 -- PC-98 text and graphics planes) is the liveness signal, and
    the AI5 interpreter's own working area just below the script buffers is the second.
    Movement in low memory alone is only the BIOS heartbeat and proves nothing.

    Returns {"class": ..., "blocks": {block_index: bytes_differing}, "total": int,
             "vram": int, "vm": int}.
    """
    if len(states) < 2:
        return {"class": "unknown", "blocks": {}, "total": 0}
    nblocks = min(min(len(s) for s in states), CHURN_SPAN) // CHURN_BLOCK
    span = nblocks * CHURN_BLOCK
    arrs = [np.frombuffer(s[:span], np.uint8).reshape(nblocks, CHURN_BLOCK) for s in states]
    acc = np.zeros(nblocks, np.int64)
    for k in range(1, len(arrs)):
        acc += (arrs[k] != arrs[k - 1]).sum(axis=1)
    blocks = {b: int(v) for b, v in enumerate(acc) if v}
    total = int(acc.sum())

    # VRAM planes: anything drawing at all touches these
    vram = sum(n for b, n in blocks.items() if VRAM_LO <= b * CHURN_BLOCK < VRAM_HI)
    # the interpreter's working area sits just below the first script slot
    vm = 0
    for k in range(1, len(states)):
        a = np.frombuffer(states[k - 1][VM_LO:FIRST_SLOT], np.uint8)
        c = np.frombuffer(states[k][VM_LO:FIRST_SLOT], np.uint8)
        vm += int(np.count_nonzero(a != c))

    if total == 0:
        cls = "frozen"
    elif vram:
        cls = "busy"
    elif vm:
        cls = "idle"
    else:
        cls = "dead"
    return {"class": cls, "blocks": blocks, "total": total, "vram": vram, "vm": vm}


def stats(st):
    """Player stats straight out of the machine -- no vision, no OCR, no guessing.

    Addresses came from a value search across the original Japanese build and the
    translated one booted to the same first room (see wordsworth.json). They are offsets
    into the serialize() blob, not physical RAM.

    ⚠️ These five fields are how the translation defect was caught: the translated build
    starts with level 0, STR 33164 and DEF 28740 (the panel prints those as "MAX") where
    the original has level 1, STR 8, DEF 7.

    ⚠️ HP wasn't found by a value search, but by READING THE SCRIPT that draws the panel
    (START.MES): `(number (+ (~ M 1) 1)) "/" (number (+ (~ M 9) 1))`. The player block is
    the scripts' M array (uint16, stride 2): M0 level, M1 HP-1, M2 exp, M3 STR, M4 DEF,
    M6 gold, M9 max HP-1. HP is stored ONE LESS than what's shown -- which is why every past
    search for "12 at 12/12" found nothing. On death the panel prints 0000, meaning M1 = -1:
    read it signed. Confirmed by measurement in combat (emu/signal_audit.py).

    ⚠️ Outside the dungeon the block is reinitialized: on the title screen and in G_OVER,
    memory gives STR 0 / DEF 0 / GOLD 100 while the panel shows the old values. These
    numbers can only be trusted in-game (`boot.in_game`).
    """
    import struct
    m = lambda i, fmt="<H": struct.unpack_from(fmt, st, M_BASE + 2 * i)[0]
    return {"level": m(0), "exp": m(2), "str": m(3), "def": m(4), "gold": m(6),
            "hp": m(1, "<h") + 1, "hp_max": m(9, "<h") + 1}


M_BASE = 0x1790e     # the scripts' M array = the player block (START.MES draws the panel from it)


X_AXIS = 0x37438
POS_AXIS = 0x3743a   # = Y_AXIS; name kept because old code refers to it
Y_AXIS = 0x3743a     # position along the direction of travel; the OTHER axis is not found
FACING = 0x3743c
COMPASS = ("north", "east", "south", "west")   # order unverified; the CYCLE is what is proven


def facing(st):
    """Which way the player is looking, 0..3. Read, not guessed from the picture.

    Found by pressing left eight times and looking for a byte that cycled 3,0,1,2,3,0,1,2,3 --
    a period-4 pattern repeated twice, which essentially nothing else in memory does.
    ⚠️ Which number means north is NOT established; only that the four values are the four
    directions and that `left` steps through them in order. Use it for "have I already faced
    this way from here", which needs no compass rose, not for absolute bearings.
    """
    return st[FACING] if len(st) > FACING else None


# Where a step forward leads for each heading. Derived from a closed loop (see where()).
STEP = {0: (1, 0), 1: (0, -1), 2: (-1, 0), 3: (0, 1)}


def where(st):
    """The player's cell and heading. Read from memory, not guessed from the picture.

    Both axes sit next to each other, like fields of one record: X `0x37438`, Y `0x3743a`,
    heading `0x3743c`.

    Y was found first: a human walked forward-forward-back-back twice, and the byte gave
    5,4,3,4,5,4,3,4,5,4,3,2, not budging on eight clean turns.

    X only came on the second attempt, and here's how that one differed. That first pass
    held ONE heading the whole time, so X sat still and was indistinguishable from everything
    else that sat still. Scripted probes from a save didn't help either -- the player just
    hit a wall (`emu/axis_walk.py`). What worked was logging a LIVE walkthrough: 12 real
    steps on headings 0 and 2, where Y didn't move, against 15 steps on headings 1 and 3, 14
    turns, and 2 wall-bumps as a control. The condition "changes only on steps across the
    known axis" was met by EXACTLY ONE byte out of 258,048.

    Confirmation is stronger than the search itself: a human circled a square room clockwise
    three times, and (X, Y, heading) returned to the same values three times -- a 10-press
    cycle, cells X 12..14, Y 1..2. A closed loop can't be faked by coincidence.

    ⚠️ `left` rotates the heading by +1 around the circle (2→3→0→1→2), NOT "down".
    """
    if len(st) <= FACING:
        return None
    f = st[FACING]
    return {"x": st[X_AXIS], "y": st[Y_AXIS], "facing": f,
            "pos": st[Y_AXIS],                       # old name, kept so callers don't break
            "ahead": STEP.get(f)}


def vram_delta(a, b):
    """Bytes differing in the VRAM planes between two snapshots. ~0.01 ms.

    This is the "has the game finished drawing?" question, and it is a different question
    from "have the pixels stopped changing". During a scene load the pixels are perfectly
    still because nothing is being drawn YET -- screenshot then and the model reasons about
    a half-composed image (PC-98 draws plane by plane, so a partial frame has visibly wrong
    colours). While the game is composing, it writes to the planes; when it stops, the
    frame is finished.
    """
    x = np.frombuffer(a[VRAM_LO:VRAM_HI], np.uint8)
    y = np.frombuffer(b[VRAM_LO:VRAM_HI], np.uint8)
    return int(np.count_nonzero(x != y))


if __name__ == "__main__":
    import sys, time
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "game_start.state"
    st = path.read_bytes()
    print(f"{len(SCRIPT_BYTES)} scripts indexed, {len(_FP)} distinct fingerprints")
    print(f"state: {path.name}  {len(st)/1e6:.1f} MB")
    t = time.perf_counter(); info = identify(st); fast = (time.perf_counter() - t) * 1000
    print(f"identify(): {fast:.2f} ms  {info}")
    t = time.perf_counter(); r = resident(st); slow = (time.perf_counter() - t) * 1000
    print(f"resident(): {slow:.1f} ms  {[n for _, n in r]} at {[hex(b) for b, _ in r]}")
    # walk() no longer feeds identify(), but it's kept as a PROBE of the layout: a mismatch
    # with resident() means the "slots are back-to-back" rule doesn't hold on this image.
    # That's information, not a failure -- it's exactly how the 0xC150 -> 0xC170 offset
    # was found.
    w, truncated = walk(st)
    if [n for _, n in w] != [n for _, n in r]:
        print(f"   ⚠️ slot arithmetic disagrees with the bytes: walk()={[n for _, n in w]}"
              f" at {[hex(b) for b, _ in w]}")
    ok = info["scene"] is not None and r == sorted(r)
    print("SELF-TEST:", "PASS" if ok else "FAIL -- nothing recognized in memory")

# --- FLOOR MAP FROM MEMORY --------------------------------------------------------------
# The floor doesn't need to be felt out by walking: the game holds it whole. The image
# carries `FL0..FL12.MP3`, 906 bytes each (the extension is misleading, it's 1993 data, not
# audio), and the floor file is loaded into memory WHOLE -- in the starting state `FL5.MP3`
# is found byte-for-byte at offset 0x15DB0.
#
# The format was cracked without a single fudged parameter. 4-byte header -- `0f 00 0f 00`,
# i.e. 15x15. Then 225 cells, 4 bytes each:
#   byte 0 = (south << 4) | west      byte 2 = event number (stairs, trigger), usually 0
#   byte 1 = (north << 4) | east      byte 3 = flag 0/1
# Nibble: 0 -- open, 3 -- opening or door, 1 -- some other kind of passage (14 per floor),
# 9 -- wall.
#
# ⚠️ How this is proven, because fitting a curve can't prove it. A shared edge is recorded
# TWICE -- at the cell and at the neighbour. If the parse is right, both records must always
# agree: "cell's nibble 0 == south neighbour's nibble 2" gave 210 out of 210, "nibble 3 ==
# east neighbour's nibble 1" also 210 out of 210. The parse was then cross-checked against
# 38 measurements logged on foot by a live agent: 38 out of 38 under the rule
# "passage <=> nibble != 9".
MAP_ADDR = 0x15DF0
MAP_W = MAP_H = 15
MAP_BYTES = 4 + MAP_W * MAP_H * 4
# side (compass STEP) -> nibble index: 0=+x east, 1=-y north, 2=-x west, 3=+y south
SIDE_NIB = {0: 3, 1: 2, 2: 1, 3: 0}
WALL = 9


def _map_nibbles(block, x, y):
    o = 4 + (y * MAP_W + x) * 4
    b0, b1 = block[o], block[o + 1]
    return [b0 >> 4, b0 & 15, b1 >> 4, b1 & 15]


# ⚠️ Edge agreement is close to an invariant, but NOT absolute: the format has legitimate
# asymmetry, one-way transitions. Measured across all 16 map files (WW/FL*.MP3, 906 b each):
# 36 mismatches out of 6720 edges, i.e. 0.5%, max 8 per floor. Kinds: (1,9) 32 times, (3,9) 3,
# (0,1) 1 -- meaning "passage on one side, wall on the other".
# Requiring an absolute match rejected 10 real maps out of 16, and floormap() on those
# floors went off scanning all of memory for a block instead of taking its own.
# The check remains strong: there are only four nibble values, and a random block will
# never give 97% matching edges out of 420 by chance.
MAP_MAX_ASYM = 12          # 3% of edges; observed max is 8


def map_asymmetry(block):
    """How many edges are recorded differently by the two neighbours. -1 means this isn't a map block at all."""
    if len(block) < MAP_BYTES or block[0:4] != b"\x0f\x00\x0f\x00":
        return -1
    bad = 0
    for y in range(MAP_H):
        for x in range(MAP_W):
            n = _map_nibbles(block, x, y)
            if y + 1 < MAP_H and n[0] != _map_nibbles(block, x, y + 1)[2]:
                bad += 1
            if x + 1 < MAP_W and n[3] != _map_nibbles(block, x + 1, y)[1]:
                bad += 1
    return bad


def _map_consistent(block):
    """Block looks like a floor map: edges agree, apart from a handful of one-way transitions."""
    bad = map_asymmetry(block)
    return 0 <= bad <= MAP_MAX_ASYM


def floormap(snap, addr=MAP_ADDR):
    """The current floor's map from memory, or None.

    Look at the known address first; if that isn't a map, search by header and verify with
    the invariant. The check isn't a formality: a random block won't pass it, so what's found
    is either a real map or nothing.
    """
    block = bytes(snap[addr:addr + MAP_BYTES])
    if not _map_consistent(block):
        block = None
        start = 0
        blob = bytes(snap)
        while True:
            i = blob.find(b"\x0f\x00\x0f\x00", start)
            if i < 0:
                return None
            cand = blob[i:i + MAP_BYTES]
            if _map_consistent(cand):
                block, addr = cand, i
                break
            start = i + 1
    cells = {}
    for y in range(MAP_H):
        for x in range(MAP_W):
            o = 4 + (y * MAP_W + x) * 4
            n = _map_nibbles(block, x, y)
            cells[(x, y)] = {"sides": {s: n[SIDE_NIB[s]] for s in (0, 1, 2, 3)},
                             "event": block[o + 2], "flag": block[o + 3]}
    return {"addr": addr, "w": MAP_W, "h": MAP_H, "cells": cells,
            "sig": hashlib.blake2b(block, digest_size=8).hexdigest()}


def map_side(fm, x, y, side):
    """'open' | 'wall' -- what THE GAME ITSELF says about this side."""
    c = (fm or {}).get("cells", {}).get((x, y))
    if not c:
        return None
    return "wall" if c["sides"][side] == WALL else "open"


# --- hunting for health -------------------------------------------------------------------
# HP wasn't found next to the player block, nor by searching for a pair of 16-bit "12"s: in
# all 305 turns where the status panel made it into the text, health was 0012/0012, and
# there's nothing to tell identical numbers apart by. So what's needed is a moment where HP
# DIFFERS from the max -- that is, combat. Then the (current, max) pair becomes distinctive,
# and intersecting candidates across several such moments leaves a single address. Same
# trick used to find the coordinates.
def hp_candidates(snap, cur, mx, span=2):
    """Offsets where `cur` and `mx` sit next to each other as 16-bit words."""
    import struct
    out = []
    blob = bytes(snap)
    needle = struct.pack("<H", int(cur))
    i = blob.find(needle)
    while i >= 0:
        for gap in range(0, (span + 1) * 2, 2):
            j = i + 2 + gap
            if j + 2 <= len(blob) and struct.unpack_from("<H", blob, j)[0] == int(mx):
                out.append(i)
                break
        i = blob.find(needle, i + 1)
        if len(out) > 4000:
            break
    return out


# --- game state via the layout read from its own scripts -----------------------------------
# ⚠️ Everything below was derived not by a value search but by READING THE SCRIPTS that draw
# and set it, and confirmed by measurement (emu/signal_audit.py, STATUS.md §18):
#
#   the FLAG0..4 save file (3072 b) is a DIRECT memory snapshot from SAVE_BASE. So one
#   decoder reads both the live game and the save on disk (`saved()`).
#   +0x000  current scene name as a string ("floor05.mes") -- no 118 ms needle scan via identify()
#   +0x020  scripts' (: N) registers -- NIBBLES, low first: register N sits in nibble N.
#           That's why every past search for items in bytes and words came up empty; PARA.MES
#           sets a new game to 900=8 901=2 902=0 903=1, and this pattern is unique across the
#           whole snapshot.
#   +0x31e  the M array = the player block (stats above), equipment M15/M17/M18/M19.
SAVE_BASE = 0x175f0
SAVE_SIZE = 3072
REG_BASE = SAVE_BASE + 0x20
ITEM_REGS = {900: 'Heal Herb', 901: 'Stamina Herb', 902: 'Gold Bar', 903: 'Ascension Stone'}
EQUIP_SLOTS = {'weapon': 15, 'armor': 17, 'helm': 18, 'shield': 19}


def _equip_names():
    """Equipment names -- from the same script that draws the panel (START.MES), not by hand."""
    import re
    src = HERE.parent / 'en' / 'START.MES.rkt'
    names = {}
    try:
        for m, v, name in re.findall(r'\(if \(== \(~ M (\d+)\) (\d+)\) \(<> \(str " ([^"]+?) *"\)\)\)',
                                     src.read_text(encoding='utf-8')):
            names.setdefault(int(m), {})[int(v)] = name.strip()
    except OSError:
        pass
    return names


EQUIP_NAMES = _equip_names()


def scene_name(snap, base=SAVE_BASE):
    """Name of the scene the game itself considers current: the string at the start of the save area."""
    raw = bytes(snap[base:base + 13]).split(b'\0')[0]
    try:
        return raw.decode('ascii').upper() or None
    except UnicodeDecodeError:
        return None


def reg(snap, n, base=REG_BASE):
    """Scripts' register (: n) -- a nibble, low first."""
    b = snap[base + n // 2]
    return (b >> 4) if n % 2 else (b & 15)


def items(snap, base=REG_BASE):
    return {name: reg(snap, n, base) for n, name in ITEM_REGS.items()}


def equipment(snap):
    import struct
    out = {}
    for slot, m in EQUIP_SLOTS.items():
        v = struct.unpack_from('<H', snap, M_BASE + 2 * m)[0]
        out[slot] = EQUIP_NAMES.get(m, {}).get(v, f'#{v}')
    return out


def names(snap, base=SAVE_BASE):
    """Hero names as the player typed them: the same fields `savenames` overwrites."""
    import sys as _sys
    _sys.path.insert(0, str(HERE.parent / 'tools'))
    import savenames
    out = {}
    for off, default in savenames.NAME_SLOTS.items():
        got = savenames.decode(bytes(snap[base + off:base + off + savenames.NAME_FIELD]))
        out[default] = got or None
    return out


def saved(flag_bytes):
    """Read a FLAG* save file with the same decoder used for the live game."""
    pad = bytes(SAVE_BASE) + bytes(flag_bytes) + bytes(0x1000)
    return {'scene': scene_name(pad), 'stats': stats(pad), 'items': items(pad),
            'equipment': equipment(pad)}
