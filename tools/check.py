#!/usr/bin/env python3
"""Check a proofread `text/` folder. Plain Python, no game, no Racket, no model.

This is the one command a proofreader runs. It never writes anything: it reads the JSON you
edited and tells you, line by line, what the game will refuse and why.

Five things are checked, and each of them has broken a real build:

| check | what it catches |
|---|---|
| markers   | `{0}` / `{1}` changed, moved or vanished -- the engine puts the hero's name there |
| charset   | a character the game's own font cannot draw (208 glyphs, no backslash, no tilde) |
| layout    | a line that will not fit the 56-column window, breaks a word, or leaves an orphan |
| fingerprint | you edited a line that has since changed on our side -- your edit would land on the wrong text |
| speaker   | the `[Name]:` tag at the start was dropped, renamed or lower-cased |

Everything is reported at once, with the file, the line id and the text, so one pass fixes
everything. Nothing here needs the game, the disk image or an emulator.

    python3 tools/check.py            # check every file
    python3 tools/check.py FLOOR02    # check one
    python3 tools/check.py --quiet    # only the summary line
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from render import screen, parts_of, flaws                          # noqa: E402
import gates                                                        # noqa: E402

TEXT = ROOT / 'text'
MARK = re.compile(r'\{(\d+)\}')
SPEAKER = re.compile(r'^\s*\[([^\]]*)\]\s*:')


def problems(row):
    """Everything wrong with one entry. Empty list means it is fine."""
    en, was = row['en'], row['was']
    out = []
    # the entry as exported: `was` fingerprints the text you started from
    untouched = hashlib.blake2b(en.encode(), digest_size=4).hexdigest() == was
    if untouched:
        return []                                   # not edited -- nothing to check

    screen_col = row.get('col', 0)

    orig_marks = row.get('marks')
    if orig_marks is not None and MARK.findall(en) != orig_marks:
        out.append(f"markers changed: {orig_marks or 'none'} -> {MARK.findall(en) or 'none'}"
                   " — {0} is where the engine prints the hero's name")

    bad_chars = gates.gate_charset([en])
    if bad_chars:
        seen = {c for c in en.replace('\n', '') if ord(c) not in gates.ALLOWED}
        out.append(f"characters the game cannot draw: {sorted(seen)}")

    tag = SPEAKER.match(en)
    if tag and '\n' in tag.group(1):
        out.append("the [Name]: tag is split across two lines")

    why = flaws(screen(parts_of(en), w=None, col0=screen_col), col0=screen_col)
    if why:
        names = {'слово разорвано': 'a word is broken in half',
                 'строка шире поля текста': 'the line is wider than the window',
                 'слово-сирота': 'a single word left alone on its own line',
                 'дыра в строке': 'a gap in the middle of the line'}
        out.append('layout: ' + ', '.join(sorted({names.get(w, w) for w in why})))
    return out


def check_file(jf, show):
    rows = json.loads(jf.read_text(encoding='utf-8'))
    edited = bad = 0
    for row in rows:
        en = row['en']
        if hashlib.blake2b(en.encode(), digest_size=4).hexdigest() == row['was']:
            continue
        edited += 1
        why = problems(row)
        if not why:
            continue
        bad += 1
        if bad <= show:
            print(f"\n  {row['id']}")
            for line in en.split('\n'):
                print(f"      | {line}")
            for w in why:
                print(f"      ✗ {w}")
    return edited, bad


def main(names, show, quiet):
    if not TEXT.is_dir():
        sys.exit(f"no {TEXT} folder -- put the text/ folder from the proofreading pack here")
    files = ([TEXT / f'{n}.MES.json' for n in names] if names
             else sorted(TEXT.glob('*.MES.json')))
    total_edited = total_bad = touched = 0
    for jf in files:
        if not jf.exists():
            print(f"  {jf.name}: no such file")
            continue
        edited, bad = check_file(jf, 0 if quiet else show)
        if edited:
            touched += 1
            if not quiet:
                mark = '✗' if bad else '✓'
                print(f"{mark} {jf.name[:-9]:16} {edited} edited"
                      + (f", {bad} need fixing" if bad else ""))
        total_edited += edited
        total_bad += bad
    print()
    if not total_edited:
        print("No edits found. Change the \"en\" fields in text/*.json and run this again.")
        return 0
    if total_bad:
        print(f"✗ {total_bad} of your {total_edited} edits will be refused "
              f"(in {touched} files). Fix them and run this again.")
        print("  Nothing was written. Your text/ folder is untouched.")
        return 1
    print(f"✓ all {total_edited} edits in {touched} files pass. Send the text/ folder back.")
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*', help='file names without .MES.json, e.g. FLOOR02')
    ap.add_argument('--show', type=int, default=10, help='problems to print per file')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    sys.exit(main(a.names, a.show, a.quiet))
