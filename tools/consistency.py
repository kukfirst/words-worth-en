#!/usr/bin/env python3
"""Same Japanese line, same English line — a check the per-batch gates cannot make.

Every gate in `gates.py` judges ONE batch. Nothing ever compared two batches, so a
fragment that occurs in twenty places was translated twenty times, independently,
and drifted. That is invisible per batch and very visible on screen: the battle
tables are built from fragments (`は『` + item + `』を見つけた！！`), so a drifting
half leaves the player looking at `found 'Gold Nugget!` with no closing quote.

Uses no model time. Read-only by default; `--apply` rewrites each divergent slot to
the majority rendering (ties -> the most frequent, then the shortest).

    tools/consistency.py                  # report
    tools/consistency.py --min 2          # only fragments seen at least twice
    tools/consistency.py --apply          # rewrite, then re-run tools/recompile.py
"""
import argparse, collections, json, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from strings import forms, patch, split_translation

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'


def pairs_by_file():
    """(ja, en, slots, ins) per translated form, keyed by file name.

    The Japanese comes from the `.orig.rkt` sibling the pipeline keeps next to every
    translation; forms are positional.

    ⚠️ A count mismatch used to skip the whole file, and that silence was expensive: it
    skipped ALL 26 battle files (`itembox.py` re-cut the item menu) and every split floor —
    which is precisely where text repeats most. Measured 2026-09-15: the line
    `{0}ゴールドと、{1}の経験値を得た。` is rendered `gold and … experience gained.` in 21
    battle files and `gained gold and … EXP.` in five, and this check was blind to it
    because it never looked at a single one of them.

    Now a mismatched file is ALIGNED instead of skipped: a split is undone (`audit.unsplit`
    puts the companion's branch back where the `(mes-call …)` stands), and whatever still
    does not line up is matched by difflib on the "has text / is blank" shape. Only blocks
    difflib calls EQUAL are used — a stretch it is unsure about is dropped, not guessed.
    """
    import difflib
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from audit import unsplit_forms
    out = {}
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        orig = EN / (p.name[:-4] + '.orig.rkt')
        if not orig.exists():
            continue
        fj = forms(orig.read_text(encoding='utf-8'))
        # ⚠️ Each form carries ITS OWN file: the slice is cancelled for ORDER, and offsets
        # remain authentic, so the edit can be written in place. Concatenated text
        # not suitable for writing — this has already mangled the translation once.
        try:
            pairs_ = unsplit_forms(p.name[:-4])
        except Exception:
            pairs_ = [(f, p.name) for f in forms(p.read_text(encoding='utf-8'))]
        fe = [f for f, _ in pairs_]
        owner = [o for _, o in pairs_]
        # ⚠️⚠️ DO NOT WRITE to slots from the restructured text. `unsplit` substitutes the body
        # branches to the position of `(mes-call …)`, and everything below shifts: slots stop
        # point to where the text is ON DISK. Expensive to verify -- 2026-09-15 `--apply`
        # ⚠️ Background: the first version wrote to slots from GLUED text and
        # corrupted the split files (FLOOR02: 491 form became 483, dialogue lines shifted).
        # Now each form carries its owner file, and writes are directed in-place.
        if len(fe) == len(fj):
            out[p.name] = [(j['ja'], e['ja'], e['slots'], e['ins'], o)
                           for j, e, o in zip(fj, fe, owner)]
            continue
        # ⚠️ Slots are taken ONLY from aligned blocks: writing to them is only possible into
        # where the correspondence is proven, not assumed.
        # ⚠️ The indicator is STRUCTURAL, not 'does text exist': production files almost always have text
        # everywhere, and by a boolean mask difflib aligned 44 forms out of 290. The number of slots and
        # the number of insertions in the translation is the same as in the original (the translation does not change them), therefore by
        # which the series converge: 285 out of 290.
        shape = lambda fs: [(len(f['slots']), len(f['ins']), bool(f['ja'].strip()))
                            for f in fs]
        sm = difflib.SequenceMatcher(None, shape(fj), shape(fe))
        rows, kept = [], 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != 'equal':
                continue
            for j, e, o in zip(fj[i1:i2], fe[j1:j2], owner[j1:j2]):
                rows.append((j['ja'], e['ja'], e['slots'], e['ins'], o))
                kept += 1
        if kept:
            out[p.name] = rows
            print(f'  · {p.name}: {len(fe)}/{len(fj)} forms — aligned {kept}')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min', type=int, default=2, help='ignore fragments seen fewer times')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    data = pairs_by_file()
    # ⚠️ Leading newline -- this is LAYOUT, not a translation. The line printed starting at column 39,
    # must start on a new line, otherwise the speaker's name won't fit in the remainder; same
    # A replica from column 0 does not require a line break. Treating this as a mismatch -- that's what makes it go red on
    # in its own self-repair, which is what happened 2026-09-15 right after fixing five signatures.
    var = collections.defaultdict(collections.Counter)
    for rows in data.values():
        for ja, en, _, _, _ in rows:
            # ⚠️ The text here is RAW, not unescaped: the line break is written with two characters
            # `\` and `n`, and `lstrip('\n')` doesn't pick it up (confirmed -- report unchanged).
            var[ja][re.sub(r'^(?:\\n)+', '', en)] += 1

    occ = sum(sum(c.values()) for c in var.values())
    multi = {k: c for k, c in var.items() if len(c) > 1 and sum(c.values()) >= args.min}
    hit = sum(sum(c.values()) for c in multi.values())
    print(f'files: {len(data)} · forms: {occ} · unique Japanese: {len(var)}')
    print(f'translated differently in different places: {len(multi)} lines, {hit} occurrences '
          f'({100*hit/max(occ,1):.0f}% of all text)')
    print()
    for ja, c in sorted(multi.items(), key=lambda kv: -sum(kv[1].values()))[:args.top]:
        print(f'{sum(c.values()):5d}x  {ja[:44]}')
        for en, n in c.most_common(4):
            print(f'          {n:4d}  {en[:64]}')

    if not args.apply:
        print('\n(report; to rewrite — --apply, then tools/recompile.py)')
        return

    canon = {ja: min(c.most_common(), key=lambda kv: (-kv[1], len(kv[0])))[0] for ja, c in multi.items()}
    total = 0
    # ⚠️ Changes are accumulated BY OWNER FILE: a form from the family may live in the wrong file, b
    # where we found it, and a 'random location' record was already breaking the translation.
    by_file, counted = {}, {}
    for name, rows in data.items():
        for ja, en, slots, ins, owner in rows:
            want = canon.get(ja)
            if want is None or want == en or slots is None:
                continue
            pieces = split_translation(want, len(slots), len(ins))
            if pieces is None:            # markers do not fit this form -- leave it alone
                continue
            by_file.setdefault(owner, []).extend(
                (s, e, v) for (s, e), v in zip(slots, pieces))
            counted[owner] = counted.get(owner, 0) + 1
    for fname, edits in sorted(by_file.items()):
        f = EN / fname
        f.write_text(patch(f.read_text(encoding='utf-8'), edits), encoding='utf-8')
        print(f'  {fname:22s} forms aligned: {counted[fname]}')
    total = sum(counted.values())
    print(f'total forms rewritten: {total} — now run tools/recompile.py')


if __name__ == '__main__':
    main()
