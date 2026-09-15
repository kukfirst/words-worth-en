#!/usr/bin/env python3
"""Lay the translation out in a form fit for PROOFREADING, not for the compiler.

The proofreader can't be shown `.rkt`: it's s-expressions, slots, substitutions, and
escaping, and any hand edit breaks the build. Here it's the same content, but flat: one
record — one line, plus HOW it lands on screen.

Record format:

    {"id": "FLOOR02.MES#123",          stable address: file + form's sequence number
     "en": "[Kaiser]: Hey... ",        text with {0} markers — must not be touched
     "was": "9f3c1a2b",                fingerprint of the source text: import will verify
                                       that this exact line was edited, not a shifted one
     "screen": ["[Kaiser]: Hey...",    how the line lands in the 56-character window
                "Clan."]}              (tools/render.py, checked against frames) — this is
                                       what shows a bad wrap

⚠️ The Japanese original is deliberately NOT here: the full game script is protected text,
and that's exactly why the scene distributes patches, not text. The `text/` directory is
not published.

    tools/export_text.py              # everything into text/
    tools/export_text.py FLOOR02.MES  # a single file
"""
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from strings import forms, unescape                                 # noqa: E402
from render import screen, parts_of                                 # noqa: E402
import column                                                       # noqa: E402

EN = ROOT / 'en'
OUT = ROOT / 'text'
MARK = re.compile(r'\{(\d+)\}')


def export(name):
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    cols = column.columns(src)
    rows = []
    for i, f in enumerate(forms(src)):
        en = unescape(f['ja'])
        if not en.strip() or any(ord(c) > 126 for c in en):
            continue                       # Japanese leftovers and stubs are not needed by the corrector
        c0 = cols.get(f['start'], 0)
        rows.append({
            'id': f'{name}#{i}',
            'en': en,
            'was': hashlib.blake2b(en.encode(), digest_size=4).hexdigest(),
            # ⚠️ Source line markers are passed as a SEPARATE field: without them the checker's validation
            # can't say that `{0}` was lost -- there's nothing to compare against, except the fingerprint,
            # but it only says "changed", not "what exactly changed".
            'marks': MARK.findall(en),
            'col': c0,
            'screen': screen(parts_of(en), w=None, col0=c0),
        })
    return rows


def main(names):
    OUT.mkdir(exist_ok=True)
    total = 0
    for name in names:
        rows = export(name)
        if not rows:
            continue
        (OUT / f'{name}.json').write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
        total += len(rows)
        print(f'  {name:16} lines {len(rows):5d}')
    print(f'\ntotal lines: {total}, directory: {OUT}')
    print('⚠️ text/ is not pushed to git — it contains the full game text')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        main(sys.argv[1:])
    else:
        main(sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                    if not p.name.endswith('.orig.rkt')))
