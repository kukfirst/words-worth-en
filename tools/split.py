#!/usr/bin/env python3
"""Split a room over threshold into two scripts: a parent + a companion.

The threshold is `gates.MES_MAX`, and it's per FILE, not per room. The game itself relies
on this: FLOOR08 calls floor08a.mes fifteen times, and together they weigh 72,819 b.
The trick (copied from the shipped FLOOR08/FLOOR08A pair):

  parent:     ((&& (== V 6) ...) (<> (mes-call "floor08b.mes")))
  companion:  ((&& (== V 6) ...) (<> ...the real body...))

The companion RE-CHECKS the same condition -- meaning V and the (: NNN) flags are global
and survive the transfer. Verified on the live pair: FLOOR08A tests exactly the condition
FLOOR08 called it under.

⚠️⚠️ THE COMPANION IS OVERWRITTEN WHOLESALE, not appended to. `build()` assembles it from
scratch out of the currently-selected branches, and `--apply` drops the result over
`--into`. So a SECOND split of the same file into the SAME companion will wipe out the
branches the first split extracted -- while the parent keeps calling them, and those events
die. The trap was caught 2026-09-14 before it got written: `FLOOR02B` had 7 branches and
7,072 b, the proposed companion had 1,458 b. Do a second split ONLY into a new name
(`FLOOR02C.MES` and onward).

✅ A companion CAN itself call (mes-call) -- measured 2026-09-15, not just inferred. There
used to be a ban here: "the game never has nested calls, so relying on them is a guess."
The guess was resolved by experiment. FLOOR08's branch `(&& (== V 4) (== (: 485) 0))` -- the
guard dialogue plus `(mes-call "sento0f.mes")` -- was extracted into a new FLOOR08C.MES, and
the run matched the control CHARACTER FOR CHARACTER across all 50 inputs: the same
dialogue, the battle triggered FROM INSIDE the companion, game over, return to the floor.
This clears 66 of FLOOR08's 71 stuck branches: nearly all its text sits in branches with
their own calls, and without nesting it couldn't be tightened by anything but damaging
the text.

⚠️⚠️ That experiment ended in a game over, so it never RETURNED. A win does, and it breaks:
the engine keeps one file to return to, the battle call overwrites it with the companion's
own name, and when the companion ends the engine reloads the companion at the parent's
offset. After beating Delta (FLOOR08C) the game replayed her lines #8-#12 forever
(2026-09-17). So a branch with a nested call must leave by jumping back into the parent:
`way_home()` below, enforced by tools/checksplit.py and broken on purpose by
tools/selftest.py.

⚠️ A side observation from the same experiment: after the split, `state.identify` names the
floor as START.MES instead of FLOOR08.MES (companions and battles are identified
correctly). So AFTER A SPLIT, the memory-based liveness check lies this way too -- read the
verdict off the screen instead (`WW_SEQ` mode of `emu/goto.py`), as agreed after the §30
crash.

⚠️ A companion does NOT define procedures: neither FLOOR08A nor FLOOR09A contains a
define-proc, even though they call (proc 10). The parent's definitions survive the call.
So a companion only needs the preamble (meta/dict-build/slot/slot) and a dispatcher ending
in (break) -- "ran once and returned." The parent's loop is forever, the companion's is
single-shot.

    tools/split.py FLOOR08.MES --into FLOOR08B.MES          # measure
    tools/split.py FLOOR08.MES --into FLOOR08B.MES --apply  # write
"""
import argparse, pathlib, re, shutil, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gates

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'
LIMIT = gates.MES_MAX


def close(s, i):
    """Index of the closing paren for the one opening at i. Strings and ; are not parens."""
    d, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == '\\' else 1
        elif c == ';':
            i = s.find('\n', i)
            if i < 0:
                return n
        elif c == '(':
            d += 1
        elif c == ')':
            d -= 1
            if d == 0:
                return i
        i += 1
    return n


def sexprs(s, i, end):
    """Spans of top-level s-expressions in the slice [i, end)."""
    out = []
    while i < end:
        if s[i] == '(':
            e = close(s, i)
            out.append((i, e + 1))
            i = e + 1
        else:
            i += 1
    return out


def main_cond(src):
    """Span of the largest (cond ..) -- that's the player-action dispatcher."""
    spans = [(m.start(), close(src, m.start())) for m in re.finditer(r'\(cond\b', src)]
    return max(spans, key=lambda p: p[1] - p[0])


def branches(src):
    """Dispatcher branches, excluding (else ..), parsed into condition and body."""
    a, b = main_cond(src)
    out = []
    for s, e in sexprs(src, a + len('(cond'), b):
        body = src[s:e]
        if body.startswith('(else'):
            continue
        cs, ce = sexprs(src, s + 1, e - 1)[0]        # first s-expression -- the condition
        out.append({'span': (s, e), 'cond_end': ce, 'src': body,
                    'text': sum(len(x) for x in re.findall(r'"((?:[^"\\]|\\.)*)"', body)),
                    'nested': bool(re.search(r'mes-call|mes-jump', body)),
                    'label': re.sub(r'\s+', ' ', src[cs:ce])[:60]})
    return out


def preamble(src):
    """Everything before the first top-level instruction not part of the companion's head."""
    keep = ('(meta', '(dict-build', '(slot', '(set-arr~', '(field')
    i, out = src.index('(mes') + len('(mes'), []
    for s, e in sexprs(src, i, len(src)):
        if not src[s:e].startswith(keep):
            break
        out.append(src[s:e])
        if src[s:e].startswith('(field'):
            break
    return out


def build(parent_src, picked, callee):
    """(new parent, companion source)."""
    call = f'(<> (mes-call "{callee.lower()}"))'
    out, last = [], 0
    for br in sorted(picked, key=lambda b: b['span'][0]):
        s, e = br['span']
        out.append(parent_src[last:br['cond_end']])
        out.append(' ' + call + ')')
        last = e
    out.append(parent_src[last:])
    parent = ''.join(out)

    head = '\n '.join(preamble(parent_src))
    bodies = '\n    '.join(br['src'] for br in sorted(picked, key=lambda b: b['span'][0]))
    comp = (f'(mes\n {head}\n (while\n  (== 1 1)\n  (<>\n   (proc 10)\n   (cond\n    '
            f'{bodies}\n    (else (<>)))\n   (break))))\n')
    return parent, comp


def size_of(src, name):
    """Compiled size. juice answers, not arithmetic."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='split.'))
    (tmp / f'{name}.rkt').write_text(src, encoding='utf-8')
    gates.juice(['-cf', f'{name}.rkt'], tmp)
    mes = tmp / f'{name}.rkt.mes'
    n = mes.stat().st_size if mes.exists() else 0
    shutil.rmtree(tmp, ignore_errors=True)
    return n


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('--into', required=True, help='companion file name, e.g. FLOOR08B.MES')
    ap.add_argument('--margin', type=int, default=2000,
                    help='sub-threshold reserve: proven by existence, not measured')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    # ⚠️ A taken name means wiping out someone else's branches (see the header). The trap
    # fired on 2026-09-14 on FLOOR05C, a legit companion from an already-shipped patch; what
    # saved it that time was only that both files were in git. Now it's a refusal, not
    # just vigilance.
    if (EN / f'{args.into}.rkt').exists():
        sys.exit(f'⛔ {args.into} already exists -- the split would wipe its branches whole.\n'
                 f'   Take a NEW name (…C, …D and onward).')

    src = (EN / f'{args.name}.rkt').read_text(encoding='utf-8')
    size0 = (EN / f'{args.name}.rkt.mes').stat().st_size
    brs = branches(src)
    print(f'{args.name}: {size0} b, over threshold by {size0 - LIMIT} b; branches {len(brs)}')

    # take the heaviest ones until the parent fits -- size comes from the compiler
    # ⚠️ A branch with `mes-jump` isn't extracted: a jump REPLACES the scene, and the
    # companion would have nowhere to return to. `mes-call` is extracted -- nested calls are
    # verified by experiment (see the header).
    movable = [b for b in brs if 'mes-jump' not in b['src']]
    nested = sum(1 for b in movable if b['nested'])
    print(f'  extractable: {len(movable)} of {len(brs)} (of those with a nested call {nested}), '
          f'with {sum(b["text"] for b in movable)} chars of text in them', flush=True)
    # ⚠️ The margin is a TARGET, not a requirement: all the remaining text sits in branches
    # with their own calls, there's nothing deeper to squeeze. We take the best achievable
    # and stop once extracting stops helping -- an empty branch weighs less than the
    # (mes-call ..) put in its place, and the parent grows: measured 30 branches -> 39,172 b,
    # the 31st -> 39,182, the 35th -> 39,222.
    picked, best, stall = [], None, 0
    for br in sorted(movable, key=lambda b: -b['text']):
        picked.append(br)
        parent, comp = build(src, picked, args.into)
        pn, cn = size_of(parent, args.name), size_of(comp, args.into)
        print(f'  extracted {len(picked):2d}: {br["text"]:5d} chars  parent {pn} b, '
              f'companion {cn} b  {br["label"]}', flush=True)
        if best is None or pn < best[0]:
            best, stall = (pn, cn, list(picked), parent, comp), 0
        else:
            stall += 1
        if 0 < pn <= LIMIT - args.margin and 0 < cn <= LIMIT:
            break
        if stall >= 2:
            print('extract no longer shrinks the parent -- stopping', flush=True)
            break
    pn, cn, picked, parent, comp = best
    if not (0 < pn <= LIMIT and 0 < cn <= LIMIT):
        print(f'\ndid not converge: best -- parent {pn} b, companion {cn} b, threshold {LIMIT}')
        return
    if pn > LIMIT - args.margin:
        print(f'\n⚠️ margin {LIMIT - pn} b instead of the requested {args.margin}: the rest of '
              f'the text sits in branches with calls, cannot extract them')
    print(f'\nbest: extracted {len(picked)} branches')

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='splitgate.'))
    for nm, s in ((args.name, parent), (args.into, comp)):
        (tmp / f'{nm}.rkt').write_text(s, encoding='utf-8')
    shutil.copyfile(EN / f'{args.name}.orig.rkt', tmp / f'{args.name}.orig.rkt')
    g = gates.juice(['-cf', f'{args.name}.rkt'], tmp), gates.juice(['-cf', f'{args.into}.rkt'], tmp)
    ok = all((tmp / f'{n}.rkt.mes').exists() for n in (args.name, args.into))
    print(f'\ncompiling both: {"ok" if ok else "FAIL"}')
    print(f'{size0} -> parent {pn} b + companion {cn} b (threshold {LIMIT} per file)')
    # ⚠️ The structure gate doesn't apply here: it compares the skeleton against .orig.rkt,
    # and we're changing the structure ON PURPOSE. The one real check is entering the room
    # in the emulator.
    if args.apply and ok:
        (EN / f'{args.name}.rkt').write_text(parent, encoding='utf-8')
        (EN / f'{args.into}.rkt').write_text(comp, encoding='utf-8')
        shutil.copyfile(tmp / f'{args.name}.rkt.mes', EN / f'{args.name}.rkt.mes')
        shutil.copyfile(tmp / f'{args.into}.rkt.mes', EN / f'{args.into}.rkt.mes')
        print(f'\nwritten: en/{args.name}.rkt and en/{args.into}.rkt (+ .mes)')
        print('⚠️ verify in emulator: enter the room and invoke the extracted branches')
    else:
        print(f'\nNOT WRITTEN. Apply with: tools/split.py {args.name} --into {args.into} --apply')
    shutil.rmtree(tmp, ignore_errors=True)




def companions(name):
    """Companions the parent calls that WEREN'T IN THE GAME -- i.e. ours.

    Told apart by the absence of a Japanese original: floor08a.mes is native (it has
    FLOOR08A.MES.orig.rkt), floor08b.mes was created by a split.
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    out = []
    for m in re.finditer(r'\(mes-call "([a-z0-9_]+\.mes)"\)', src):
        nm = m.group(1).upper()[:-4] + '.MES'
        if not (EN / f'{nm}.orig.rkt').exists() and (EN / f'{nm}.rkt').exists():
            out.append(nm)
    return sorted(set(out))


# ⚠️ The engine keeps ONE file to return to. A branch that calls a battle from inside a
# companion takes that slot, so the branch leaves by jumping back into the parent instead of
# returning (tools/checksplit.py, "way home"). Those lines are ours, not the game's: each ends
# with this marker so unsplit() can drop them before comparing against the original.
WAY_HOME = '; way home'


def way_home(parent, cell):
    """Lines that end a battle branch inside a companion: set the cell, re-enter the parent.

    Entrance register 121 = 15 matches none of the parent's entrances (0..7), so its preamble
    keeps the cell written here into M22-24 instead of moving the hero to a staircase.
    """
    x, y, f = cell
    return [f'(set-arr~ M 22 {x}) {WAY_HOME}', f'(set-arr~ M 23 {y}) {WAY_HOME}',
            f'(set-arr~ M 24 {f}) {WAY_HOME}'], \
           [f'(set-reg: 121 15) {WAY_HOME}', f'(mes-jump "{parent.lower()}") {WAY_HOME}']


def strip_way_home(src):
    """The companion text as the split wrote it: our way-home lines and comments removed."""
    src = re.sub(r'\n[ \t]*\([^\n]*\) ' + re.escape(WAY_HOME) + r'(?=\n)', '', src)
    src = re.sub(r'\n[ \t]*;;[^\n]*(?=\n)', '', src)
    # the parens the insertion moved onto a line of their own go back where they were
    return re.sub(r'\n[ \t]*(\)+)(?=\n|$)', r'\1', src)


def unsplit(name):
    """The source as it would look without the split: branch bodies restored from companions.

    ⚠️ Why. The structure gate compares the skeleton against the Japanese original, and a
    split changes the structure ON PURPOSE -- after splitting nine rooms, recompile gave
    ok=84 bad=9, and all nine "failures" were split parents. Chalking them up to "that's
    intended" isn't good enough: that gate is the only thing proving the script isn't
    mangled, and disabling it exactly where we changed the most means going without a
    safety net. So we compare the reconstruction instead. Side benefit: a matching skeleton
    proves the split didn't lose or reorder a single branch.
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    bodies = {}
    for c in companions(name):
        for br in branches((EN / f'{c}.rkt').read_text(encoding='utf-8')):
            cs, ce = sexprs(br['src'], 1, len(br['src']) - 1)[0]
            key = re.sub(r'\s+', ' ', br['src'][cs:ce])
            bodies[key] = strip_way_home(br['src'][ce:-1])
    out, last, restored = [], 0, 0
    for br in branches(src):
        s, e = br['span']
        tail = src[br['cond_end']:e - 1]
        if 'mes-call' not in tail:
            continue
        cs, ce = sexprs(br['src'], 1, len(br['src']) - 1)[0]
        key = re.sub(r'\s+', ' ', br['src'][cs:ce])
        if key not in bodies:
            continue
        out.append(src[last:br['cond_end']])
        out.append(bodies[key])
        last = e - 1
        restored += 1
    out.append(src[last:])
    return ''.join(out), restored


if __name__ == '__main__':     # ⚠️ otherwise importing for unsplit() triggers argv parsing
    run()


def gate_split_parent(name):
    """Gate for a split parent. None -- all clear.

    A two-link chain that together equals the regular gate_compile_and_structure:
      1. the reconstruction (parent + companions) matches the Japanese original by skeleton
         -- meaning the split didn't lose or reorder a single branch;
      2. the parent compiles and decompiles back into itself -- meaning the compiler
         mangled nothing.
    The first link replaces the comparison against the original, which is impossible after
    a split; the second keeps the compilation check, which doesn't depend on the split.
    """
    rec, k = unsplit(name)
    if not k:
        return f'{name}: reconstruction returned no branches -- no companions found'
    orig = (EN / f'{name}.orig.rkt').read_text(encoding='utf-8')
    if gates.skeleton(orig) != gates.skeleton(rec):
        a, b = gates.skeleton(orig), gates.skeleton(rec)
        i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        return f'reconstruction diverges from the original at {i}: …{a[max(0,i-40):i+40]}…'

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='splitgate.'))
    try:
        shutil.copyfile(EN / f'{name}.rkt', tmp / f'{name}.rkt')
        r = gates.juice(['-cf', f'{name}.rkt'], tmp)
        mes = tmp / f'{name}.rkt.mes'
        if not mes.exists() or not mes.stat().st_size:
            return f'compile failed: {(r.stderr or r.stdout).strip()[:200]}'
        back = tmp / 'roundtrip'
        back.mkdir(exist_ok=True)
        (back / name).write_bytes(mes.read_bytes())
        gates.juice(['-df', '--protag', '0,1', '--charset', 'english', name], back)
        got = back / f'{name}.rkt'
        if not got.exists():
            return 'recompiled file will not decompile'
        if gates.skeleton(got.read_text(encoding='utf-8')) != gates.skeleton(
                (EN / f'{name}.rkt').read_text(encoding='utf-8')):
            return 'compilation not reversible: parsed file does not match source'
        return gates.gate_size(mes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
