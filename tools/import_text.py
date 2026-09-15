#!/usr/bin/env python3
"""Bring proofread text from `text/*.json` back into `en/*.rkt` -- through the gates, not on trust.

The proofreader edits a flat JSON (`tools/export_text.py`). Here the edits go back into place,
and EVERY one passes a check. Without this, proofreading is unchecked text going straight into
the game, and we already know how that ends: a line longer than the window breaks a word, a
lost `{0}` marker turns into "Astralwas", a stray byte pushes the file past its buffer and the
game drops to DOS.

What gets checked on every changed line:

| check | what it catches |
|---|---|
| `was` fingerprint | the wrong line got edited: the file has shifted since export |
| markers | `{0}` vanished, multiplied, or moved |
| encoding | anything outside the game's character generator |
| layout | the line doesn't fit the window, breaks a word, leaves an orphan |
| size | the file crossed `gates.MES_MAX` -- that's death on entering the room |

⚠️ Writes nothing without `--apply`, and on the very first failed check it writes NOTHING AT
ALL: half of an accepted proofreading pass is worse than none -- afterwards there's no way to
tell what got applied.

    tools/import_text.py            # show what will change
    tools/import_text.py --apply    # write and rebuild the affected .mes files
"""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from strings import escape, forms, patch, split_translation, unescape   # noqa: E402
from render import screen, parts_of, flaws                              # noqa: E402
import column                                                           # noqa: E402
import gates                                                            # noqa: E402

EN = ROOT / 'en'
TEXT = ROOT / 'text'
MARK = re.compile(r'\{(\d+)\}')


def check(old, new, c0):
    """What's wrong with the new replica. Empty — means safe to take."""
    bad = []
    if MARK.findall(old) != MARK.findall(new):
        bad.append(f'markers {MARK.findall(old)} became {MARK.findall(new)}')
    if gates.gate_charset([new]):
        bad.append('characters outside game encoding')
    why = flaws(screen(parts_of(new), w=None, col0=c0), col0=c0)
    if why:
        bad.append('layout:' + ', '.join(sorted(set(why))))
    return bad


def main(apply):
    if not TEXT.is_dir():
        sys.exit(f'no {TEXT} -- run tools/export_text.py first')
    changes, problems = {}, []
    for jf in sorted(TEXT.glob('*.MES.json')):
        name = jf.name[:-5]
        src_p = EN / f'{name}.rkt'
        if not src_p.exists():
            problems.append(f'{name}: no such file in en/')
            continue
        src = src_p.read_text(encoding='utf-8')
        fs = forms(src)
        cols = column.columns(src)
        for row in json.loads(jf.read_text(encoding='utf-8')):
            i = int(row['id'].split('#')[1])
            if i >= len(fs):
                problems.append(f'{row["id"]}: no form with that number anymore')
                continue
            f = fs[i]
            old = unescape(f['ja'])
            new = row['en']
            # ⚠️ First «did the proofreader edit this line», and only then the fingerprint. Inverted
            # the order made the import fragile to the point of uselessness: `battlefix.py` fixed 45
            # battle replicas AFTER the dump, and each of them -- untouched by the corrector --
            # it gave "source string changed", and the rule "one failed -- not written
            # » nothing was breaking the entire import because of other people's files.
            #
            # ⚠️⚠️ The 'no edits' indicator -- the `was` fingerprint matches `en` ITSELF, rather than
            # the current source. The first attempt compared `new` with `old` (that is, with
            # the current `en/`), but this is exactly the same comparison as the one below, just with different
            # in other words: the stale entry differs from the original precisely because
            # source is gone. `was` -- snapshot at the time of export, and it's the only one
            # knows what the corrector had in front of it.
            if hashlib.blake2b(new.encode(), digest_size=4).hexdigest() == row['was']:
                continue                      # record is unmodified -- nothing to compare against
            if hashlib.blake2b(old.encode(), digest_size=4).hexdigest() != row['was']:
                problems.append(f'{row["id"]}: source line changed since export')
                continue
            bad = check(old, new, cols.get(f['start'], 0))
            if bad:
                problems.append(f'{row["id"]}: ' + '; '.join(bad))
                continue
            pieces = split_translation(new, len(f['slots']), len(f['ins']),
                                       f.get('lead', 0), f.get('trail', 0))
            if pieces is None:
                problems.append(f'{row["id"]}: does not fit into the form slots')
                continue
            changes.setdefault(name, []).append((f, pieces, old, new))

    for name, rows in sorted(changes.items()):
        print(f'  {name:16} edits {len(rows)}')
        for _, _, old, new in rows[:2]:
            print(f'      was: {old[:66]}')
            print(f'      now: {new[:66]}')
    if problems:
        # ⚠️ Showing 12 out of 46 is not acceptable: you can't fix it from such a report -- 2026-09-15 for
        # Truncation hid 34 issues; had to parse them out with a separate script.
        # Group by REASON, with examples: few distinct reasons, many lines.
        kinds = {}
        for p in problems:
            kind = p.split(': ', 1)[1] if ': ' in p else p
            kind = re.sub(r'\[.*?\]', '[…]', kind)
            kinds.setdefault(kind, []).append(p.split(':')[0])
        print(f'\n❌ failed check: {len(problems)}')
        for kind, ids in sorted(kinds.items(), key=lambda x: -len(x[1])):
            print(f'   {len(ids):4d}  {kind}')
            print(f'         {", ".join(ids[:8])}{" …" if len(ids) > 8 else ""}')
        sys.exit('\nNOTHING WRITTEN: fix the listed items first')
    if not changes:
        print('no edits')
        return
    print(f'\nfiles to edit: {len(changes)}, lines: {sum(len(v) for v in changes.values())}')
    if not apply:
        print('\nNOT WRITTEN. Apply: tools/import_text.py --apply')
        return
    # ⚠️ The build DIVERGES, and only what converges moves to en/. The previous version wrote
    # immediately, compiled in place and THEN printed «⚠️ OVER THRESHOLD» — that is, it reported
    # the mess it has already caused. A file past the threshold crashes the game on room entry (§30),
    # and there's no way to find out about it after the fact.
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='import.'))
    built, over = {}, []
    for name, rows in changes.items():
        src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
        edits = []
        for f, pieces, _, _ in rows:
            for (x, y), piece in zip(f['slots'], pieces):
                edits.append((x, y, escape(piece)))
        (tmp / f'{name}.rkt').write_text(patch(src, edits), encoding='utf-8')
        subprocess.run(['racket', str(ROOT / 'tools/juice/mes/juice.rkt'), '-cf',
                        f'{name}.rkt'], cwd=tmp, capture_output=True, timeout=900)
        mes = tmp / f'{name}.rkt.mes'
        size = mes.stat().st_size if mes.exists() else 0
        was = (EN / f'{name}.rkt.mes').stat().st_size
        if not size:
            over.append(f'{name}: did not compile')
        elif size > gates.MES_MAX:
            over.append(f'{name}: {size} b, past threshold {gates.MES_MAX} (was {was})')
        built[name] = (size, was)
        print(f'  {name:16} {was} -> {size} b')
    if over:
        print(f'\n❌ failed on size: {len(over)}')
        for o in over:
            print(f'   {o}')
        shutil.rmtree(tmp, ignore_errors=True)
        sys.exit('\nNOTHING WRITTEN: compress or split first (tools/split.py)')
    for name in built:
        shutil.copyfile(tmp / f'{name}.rkt', EN / f'{name}.rkt')
        shutil.copyfile(tmp / f'{name}.rkt.mes', EN / f'{name}.rkt.mes')
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'\nfiles written: {len(built)}')
    print('⚠️ required next: tools/verify.py and acceptance by running')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    main(ap.parse_args().apply)
