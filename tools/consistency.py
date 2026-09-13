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
import argparse, collections, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from strings import forms, patch, split_translation

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'


def pairs_by_file():
    """(ja, en, slots, ins) per translated form, keyed by file name.

    The Japanese comes from the `.orig.rkt` sibling the pipeline keeps next to every
    translation; forms are positional, so a mismatch in count means the file was
    restructured and it is skipped rather than guessed at.
    """
    out = {}
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        orig = EN / (p.name[:-4] + '.orig.rkt')
        if not orig.exists():
            continue
        fe = forms(p.read_text(encoding='utf-8'))
        fj = forms(orig.read_text(encoding='utf-8'))
        if len(fe) != len(fj):
            print(f'  ⚠️ {p.name}: {len(fe)} форм против {len(fj)} в оригинале — пропущен')
            continue
        out[p.name] = [(j['ja'], e['ja'], e['slots'], e['ins']) for j, e in zip(fj, fe)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min', type=int, default=2, help='ignore fragments seen fewer times')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    data = pairs_by_file()
    var = collections.defaultdict(collections.Counter)
    for rows in data.values():
        for ja, en, _, _ in rows:
            var[ja][en] += 1

    occ = sum(sum(c.values()) for c in var.values())
    multi = {k: c for k, c in var.items() if len(c) > 1 and sum(c.values()) >= args.min}
    hit = sum(sum(c.values()) for c in multi.values())
    print(f'файлов: {len(data)} · форм: {occ} · уникальных японских: {len(var)}')
    print(f'переведены по-разному в разных местах: {len(multi)} строк, {hit} вхождений '
          f'({100*hit/max(occ,1):.0f}% всего текста)')
    print()
    for ja, c in sorted(multi.items(), key=lambda kv: -sum(kv[1].values()))[:args.top]:
        print(f'{sum(c.values()):5d}x  {ja[:44]}')
        for en, n in c.most_common(4):
            print(f'          {n:4d}  {en[:64]}')

    if not args.apply:
        print('\n(отчёт; чтобы переписать — --apply, затем tools/recompile.py)')
        return

    canon = {ja: min(c.most_common(), key=lambda kv: (-kv[1], len(kv[0])))[0] for ja, c in multi.items()}
    total = 0
    for name, rows in data.items():
        p = EN / name
        src = p.read_text(encoding='utf-8')
        edits, n = [], 0
        for ja, en, slots, ins in rows:
            want = canon.get(ja)
            if want is None or want == en:
                continue
            pieces = split_translation(want, len(slots), len(ins))
            if pieces is None:            # markers do not fit this form -- leave it alone
                continue
            edits += [(s, e, v) for (s, e), v in zip(slots, pieces)]
            n += 1
        if edits:
            p.write_text(patch(src, edits), encoding='utf-8')
            print(f'  {name:22s} выровнено форм: {n}')
            total += n
    print(f'итого переписано форм: {total} — теперь прогони tools/recompile.py')


if __name__ == '__main__':
    main()
