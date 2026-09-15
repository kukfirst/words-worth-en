#!/usr/bin/env python3
"""Finish off the forms still left in Japanese, through the same pipeline -- for the remainder ACROSS THE ENTIRE GAME.

Why a separate tool. `gates.gate_charset` judges a BATCH: if the model returned Japanese,
the batch is rejected and after three attempts the form stays as it was. This is correct (a Japanese
string is better than a fabrication), but nobody ever asked the whole game: "what's left?". Measurement
2026-09-13 -- three forms remain, all three in H-scenes, and this is the only Japanese text
the player will ever see.

⚠️ Kana in `NAME.MES` are NOT translated: these are on-screen keyboard rows for name input, not text.
⚠️ Full-width spaces (\\u3000) -- layout, not text.

    tools/finish_ja.py            # show the remainder
    tools/finish_ja.py --apply    # translate and write
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from strings import escape, forms, patch, split_translation, unescape       # noqa: E402

# ⚠️ `translate.py` parses arguments AT MODULE LEVEL, not under `if __name__`. Import
# From here argparse diverts IT into our flags, and `--apply` fails as "unrecognized argument"
# From external parsing. We hide our arguments during import.
_argv, sys.argv = sys.argv, sys.argv[:1]
import translate                                                           # noqa: E402
sys.argv = _argv

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'
SKIP_FILES = {'NAME.MES'}            # on-screen keyboard -- on point


def real_japanese(t):
    return any(ord(c) > 126 for c in t) and not all(c in '　 \n' for c in t)


def remaining():
    out = []
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt') or p.name[:-4] in SKIP_FILES:
            continue
        src = p.read_text(encoding='utf-8')
        for f in forms(src):
            if real_japanese(unescape(f['ja'])):
                out.append((p, f))
    return out


def main(apply):
    rows = remaining()
    print(f'forms with Japanese text: {len(rows)}')
    for p, f in rows:
        print(f'  {p.name[:-8]:12} {unescape(f["ja"])[:60]}')
    if not rows or not apply:
        return
    glossary, locked = translate.load_glossary()
    by_file = {}
    for p, f in rows:
        by_file.setdefault(p, []).append(f)
    for p, fs in by_file.items():
        src = p.read_text(encoding='utf-8')
        ja = [unescape(f['ja']) for f in fs]
        en, why, stats = translate.translate_batch(ja, glossary, locked,
                                                   story='', tail=[], width=46)
        if en is None:
            print(f'  ❌ {p.name[:-8]}: gates rejected the translation -- {str(why)[:160]}')
            continue
        edits = []
        for f, e in zip(fs, en):
            pieces = split_translation(e, len(f['slots']), len(f['ins']),
                                       f.get('lead', 0), f.get('trail', 0))
            if pieces is None:
                print(f'  ❌ {p.name[:-8]}: translation does not fit the slots of the form')
                continue
            for (x, y), piece in zip(f['slots'], pieces):
                edits.append((x, y, escape(piece)))
            print(f'  ✅ {p.name[:-8]}: {e[:70]}')
        if edits:
            p.write_text(patch(src, edits), encoding='utf-8')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    main(ap.parse_args().apply)
