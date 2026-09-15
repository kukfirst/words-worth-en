#!/usr/bin/env python3
"""Game LOGIC fixes -- declared, not silent.

    tools/logicfix.py            # what was applied / what wasn't
    tools/logicfix.py --apply    # apply to en/*.rkt (idempotent)

Translation changes text only, and the structure gate (gates.py) enforces that: any change
to instructions is a failure. But the original has bugs that players see as "doesn't work", and
they live in the script logic. Such fixes are listed here -- the SINGLE source of truth: this
tool applies them to the translation, and the gate -- to the reference before comparison. Only
the declared change is permitted, and nothing beyond it.

Each entry: file, before, after, why -- with measurement.
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'

FIXES = {
    'START1.MES': [(
        '(&& (== (~ @ 10) 639) (== (~ @ 11) 399))',
        '(&& (== (~ @ 10) 639) (> (~ @ 11) 390))',
        'Title menu: the arrow keys park the cursor at `(mouse 3 639 399)`, but the engine '
        'will not let it go below y=391 -- measured: after "up" the cursor sits at (639, 391). '
        'Keyboard confirm required y == 399 and NEVER fired after using the arrows, so '
        'New Game was unreachable on the Japanese original too. The > 390 threshold passes '
        'at both 391 and 399.',
    )],
}


def fixed(name, src):
    """Text with this file's corrections applied."""
    for old, new, _ in FIXES.get(name, []):
        src = src.replace(old, new)
    return src


def status():
    rows = []
    for name, fixes in FIXES.items():
        src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
        for old, new, why in fixes:
            rows.append((name, 'applied' if new in src and old not in src else 'NO', why[:70]))
    return rows


if __name__ == '__main__':
    if '--apply' in sys.argv:
        for name in FIXES:
            p = EN / f'{name}.rkt'
            p.write_text(fixed(name, p.read_text(encoding='utf-8')), encoding='utf-8')
    for name, st, why in status():
        print(f'  {name:12} {st:10} {why}')
