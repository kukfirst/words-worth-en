#!/usr/bin/env python3
"""Re-emit already-translated files with a rebuilt dictionary, then re-gate them.

Uses no model time: this is compilation only.
"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from strings import rebuild_dict, number_fix
import gates, split

EN = pathlib.Path(__file__).resolve().parent.parent / 'en'
names = sorted({p.name[:-4] for p in EN.glob('*.MES.rkt') if not p.name.endswith('.orig.rkt')})
# never touch the file the running pipeline is writing right now -- but live.json
# outlives the run that wrote it, and a stale name used to drop a finished file
# from the sweep silently (ok=59 of 60, with nothing to say which one).
import json, subprocess
busy = ''
running = subprocess.run(['pgrep', '-f', 'tools/translate.py'],
                         capture_output=True).returncode == 0
if running:
    try:
        busy = json.load(open(EN.parent / 'live.json')).get('file', '')
    except Exception:
        busy = ''
skipped = [n for n in names if busy and busy.startswith(n)]
names = [n for n in names if n not in skipped]
if skipped:
    print(f'  ⏭️  skipped (the pipeline is writing it right now): {", ".join(skipped)}')
ok = bad = 0
for n in names:
    src = (EN / f'{n}.rkt').read_text(encoding='utf-8')
    new, nfix = number_fix(src)          # digits in the message window -- half-glyphs without this
    new = rebuild_dict(new)
    if new != src:
        (EN / f'{n}.rkt').write_text(new, encoding='utf-8')
    # ⚠️ A split satellite (tools/split.py) has no Japanese original and can't have one --
    # no such file ever existed in the game. There's nothing to check the skeleton against,
    # the structure gate is inapplicable to it by definition; what's left is compilation and size.
    if (EN / f'{n}.orig.rkt').exists():
        # ⚠️ A split parent's skeleton diverges from the original BY CONSTRUCTION: the branch
        # body is replaced with (mes-call ..). The usual gate declared such a file broken
        # (ok=84 bad=9 right after splitting nine rooms). We check the reconstruction instead --
        # split.gate_split_parent() pulls bodies back from the satellites and compares them
        # against the Japanese original, while compile reversibility is checked as a separate link.
        if split.companions(n):
            # ⚠️ gate_split_parent() compiles into a TEMPORARY directory, not into en/. Without
            # this line en/<name>.rkt.mes for split parents was left over from the moment of the
            # split, image assembly picked it up as-is, and text without its later edits shipped
            # into the game. Caught by a screenshot: a letter ran past the window edge even though
            # the padding was there in the source. 10 out of 11 were stale.
            gates.juice(['-cf', f'{n}.rkt'], EN)
            g = split.gate_split_parent(n)
        else:
            g = gates.gate_compile_and_structure(EN, n)
        orig = (EN.parent / 'work' / n).stat().st_size
    else:
        r = gates.juice(['-cf', f'{n}.rkt'], EN)
        mes = EN / f'{n}.rkt.mes'
        g = (gates.gate_size(mes) if mes.exists() and mes.stat().st_size
             else f'compile failed: {(r.stderr or r.stdout).strip()[:200]}')
        orig = 0
    size = (EN / f'{n}.rkt.mes').stat().st_size if (EN / f'{n}.rkt.mes').exists() else 0
    where = (f'(original {orig:6d}, {100*size/orig:5.0f}%)' if orig else '(cut satellite)')
    print(f"  {'✅' if not g else '❌'} {n:16s} {size:6d} b {where}"
          + (f'  digits wrapped {nfix}' if nfix else '')
          + ('' if not g else f'  {str(g)[:70]}'), flush=True)
    ok += not g
    bad += bool(g)
print(f'total ok={ok} bad={bad}' + (f' skipped={len(skipped)}' if skipped else ''))
