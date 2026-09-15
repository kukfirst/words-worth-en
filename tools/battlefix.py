#!/usr/bin/env python3
"""Unwind battle lines where the name of the one TAKING the hit is phrased as the one dealing it.

## What's broken

In battle, `(proc 41)` prints the name, followed by a form. The Japanese form starts with
`に` -- a particle that makes the named name the RECIPIENT:

    (proc 41) (text "に" (number D) "のダメージを与えた！！")     [NAME] took D damage

The English in the same spot says the opposite:

    (proc 41) (text " dealt " (number D) " damage!!")            [NAME] dealt D damage

The emulator frame that caught this: `A Sturdy Dwarf dealt 0 damage to!!` -- the Dwarf there
was TAKING the hit, not dealing it, and on top of that `to` was left dangling with no name,
because there's nothing left to insert after the form.

Measured: places where Japanese `に` makes the name the recipient -- **52**; translated
correctly in 7, **flipped in 45**, across 23 of 26 battle files. This is the single most
common line in the game: one per hit.

⚠️ Why no gate caught this. `gate_edges` looks at the character AFTER a marker, and there is
no marker here at all -- a procedure prints the name. `gate_width` measures width. The
structural gate checks the instruction skeleton, and that never changed. Nobody checked the
MEANING of the phrase, and the only way to check it was to read a frame.

## How it's fixed

A swap by form, not by meaning: "dealt" -> "took", the dangling `to` is dropped. Fixed ONLY
where Japanese `に` confirms the recipient role -- the list of spots is computed, not
hand-written.

    tools/battlefix.py            # preview
    tools/battlefix.py --apply    # write and rebuild
"""
import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from split import close                                             # noqa: E402
import gates                                                        # noqa: E402

EN = ROOT / 'en'

# Form -> fixed form. The left side is exactly what measurement found.
SWAP = [
    (' dealt ', ' took '),
    (' did no damage', ' took no damage'),
    (' dealt no damage', ' took no damage'),
    # ⚠️ A one-off form: someone squeezed it to fit the size budget and cut the verb entirely.
    ('- no damage', ' took no damage'),
]
# Dangling tail: nothing to insert after the form, the name is already printed BEFORE it.
TAIL = [(' damage to it!!', ' damage!!'), (' damage to!!', ' damage!!'),
        (' damage to"', ' damage"'), (' damage to', ' damage')]


def sites(src):
    """(start, end, text) of the nearest (text …) after each (proc 41).

    By bracket balance, not a regex: a number fix sits between them,
    `(set-arr~ @ 20 (// (&& (~ @ 20) 4095) 4096))`, three levels deep.
    """
    out = []
    for m in re.finditer(r'\(proc 41\)', src):
        i = src.find('(text', m.end())
        if i < 0 or i - m.end() > 300:
            out.append(None)
            continue
        e = close(src, i)
        out.append((i, e + 1, src[i:e + 1]))
    return out


def fix(form):
    """Fixed form, or None if there's nothing to change."""
    out = form
    for a, b in SWAP:
        if a in out:
            out = out.replace(a, b, 1)
            break
    else:
        return None
    for a, b in TAIL:
        out = out.replace(a, b)
    return out if out != form else None


def plan(name):
    """What will change in the file: a list of (start, end, was, now)."""
    orig = EN / f'{name}.orig.rkt'
    if not orig.exists():
        return []
    ja = sites(orig.read_text(encoding='utf-8'))
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    en = sites(src)
    if len(ja) != len(en):
        return []
    out = []
    for a, b in zip(ja, en):
        if not a or not b:
            continue
        # ⚠️ Fixed ONLY where Japanese `に` confirms: the name is the recipient.
        if '"に' not in a[2][:10]:
            continue
        new = fix(b[2])
        if new:
            out.append((b[0], b[1], b[2], new))
    return out


def main(names, apply):
    total, touched = 0, []
    for name in names:
        rows = plan(name)
        if not rows:
            continue
        total += len(rows)
        touched.append(name)
        if len(touched) <= 3:
            for _, _, was, now in rows:
                print(f'  {name}')
                print(f'      was: {was}')
                print(f'      now: {now}')
        if apply:
            src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
            for s, e, _, now in sorted(rows, reverse=True):
                src = src[:s] + now + src[e:]
            (EN / f'{name}.rkt').write_text(src, encoding='utf-8')
    print(f'\nspots: {total}, files: {len(touched)}')
    if not apply:
        print('NOT RECORDED. Apply: tools/battlefix.py --apply')
        return 0
    print('\nrebuild:')
    bad = 0
    for name in touched:
        gates.juice(['-cf', f'{name}.rkt'], EN)
        mes = EN / f'{name}.rkt.mes'
        n = mes.stat().st_size if mes.exists() else 0
        over = '⚠️ OVER THRESHOLD' if n > gates.MES_MAX else ''
        if not n or over:
            bad += 1
        print(f'  {name:16} {n} b{over}', flush=True)
    print(f'\n{"❌ failures:" + str(bad) if bad else "✅ all reassembled under threshold"}')
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ns = a.names or sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                           if not p.name.endswith('.orig.rkt'))
    sys.exit(main(ns, a.apply))
