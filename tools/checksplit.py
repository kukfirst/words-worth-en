#!/usr/bin/env python3
"""Verify that the split lost nothing: the parent calls exactly what the companion provides.

A split is the only edit that deliberately changes the STRUCTURE of a script, so
the structural gate (`gates`, skeleton check against `.orig.rkt`) does not apply to it. What remains is
one thing: recompute the pair in full.

What it catches:

| check | what it catches |
|---|---|
| conditions | a branch lifted out of the parent but not carried over to the companion |
| call in place | a parent that, where a branch used to be, calls the wrong companion |
| no orphans | a companion with a branch the parent no longer calls -- dead code |
| size | file over `gates.MES_MAX` |
| names | a companion that is called but does not exist in `en/` |

⚠️ This is the very check that would have caught the FLOOR05C overwrite on 2026-09-14: the companion would have
kept one branch instead of seven, while the parent called it in seven places -- "no orphans" in reverse.

    tools/checksplit.py            # all pairs
    tools/checksplit.py FLOOR08.MES
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gates                                                        # noqa: E402
import split                                                        # noqa: E402

EN = ROOT / 'en'


def norm(s):
    """Condition regardless of whitespace: we compare meaning, not formatting."""
    return re.sub(r'\s+', ' ', s).strip()


def conds(src):
    """Condition of each dispatcher branch -> its body. Without a dispatcher -- empty, not an error:
    most game scripts (battles, shops, menus) contain no (cond ..) at all."""
    out = {}
    if '(cond' not in src:
        return out
    for br in split.branches(src):
        s, _ = br['span']
        cs, ce = split.sexprs(src, s + 1, br['span'][1] - 1)[0]
        out.setdefault(norm(src[cs:ce]), []).append(br['src'])
    return out


def check(name):
    """List of issues with the parent/satellites pair. Empty means everything is consistent."""
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    bad = []
    parent = conds(src)

    # who the parent calls and under what condition
    wanted = {}
    for cond, bodies in parent.items():
        for body in bodies:
            for callee in re.findall(r'mes-call "([^"]+)"', body):
                wanted.setdefault(callee.upper().replace('.MES', '.MES'), set()).add(cond)

    for callee, need in sorted(wanted.items()):
        comp = EN / f'{callee}.rkt'
        if not comp.exists():
            bad.append(f'calls {callee}, but the file is not in en/')
            continue
        # native game companions (they have a Japanese original) not our concern:
        # their conditions were written by elf, and they don't need to match the parent's
        if (EN / f'{callee}.orig.rkt').exists():
            continue
        have = set(conds(comp.read_text(encoding='utf-8')))
        for c in sorted(need - have):
            bad.append(f'{callee}: parent calls under «{c[:56]}», but no such branch exists in the satellite')
        for c in sorted(have - need):
            bad.append(f'{callee}: branch «{c[:56]}» exists, but the parent no longer calls it (dead text)')

    for f in [name] + sorted(wanted):
        mes = EN / f'{f}.rkt.mes'
        if not mes.exists():
            bad.append(f'{f}: not compiled')
        elif mes.stat().st_size > gates.MES_MAX:
            bad.append(f'{f}: {mes.stat().st_size} b -- over threshold {gates.MES_MAX}')
    return bad, wanted


def main(names):
    total = 0
    for name in names:
        bad, wanted = check(name)
        ours = [c for c in wanted if not (EN / f'{c}.orig.rkt').exists()]
        if not ours and not bad:
            continue
        mark = '❌' if bad else '✅'
        print(f'{mark} {name:16} our satellites {len(ours)}: {", ".join(sorted(ours)) or "—"}')
        for b in bad:
            print(f'      {b}')
        total += len(bad)
    print(f'\n{"❌ issues:" + str(total) if total else "✅ all pairs matched"}')
    return 1 if total else 0


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        args = sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                      if not p.name.endswith('.orig.rkt'))
    sys.exit(main(args))
