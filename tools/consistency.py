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
        # ⚠️ Каждая форма несёт СВОЙ файл: разрез отменяется для ПОРЯДКА, а смещения
        # остаются настоящими, поэтому правку можно записать по месту. Склеенный текст
        # для записи не годится — это уже покорёжило перевод однажды.
        try:
            pairs_ = unsplit_forms(p.name[:-4])
        except Exception:
            pairs_ = [(f, p.name) for f in forms(p.read_text(encoding='utf-8'))]
        fe = [f for f, _ in pairs_]
        owner = [o for _, o in pairs_]
        # ⚠️⚠️ ПИСАТЬ по слотам из перестроенного текста НЕЛЬЗЯ. `unsplit` подставляет тело
        # ветки на место `(mes-call …)`, и всё, что ниже, съезжает: слоты перестают
        # указывать туда, где текст лежит НА ДИСКЕ. Проверено дорого -- 2026-09-15 `--apply`
        # ⚠️ История вопроса: первая версия писала по слотам из СКЛЕЕННОГО текста и
        # покорёжила разрезанные файлы (FLOOR02: 491 форма стала 483, реплики съехали).
        # Теперь каждая форма несёт файл-владельца, и запись идёт по месту.
        if len(fe) == len(fj):
            out[p.name] = [(j['ja'], e['ja'], e['slots'], e['ins'], o)
                           for j, e, o in zip(fj, fe, owner)]
            continue
        # ⚠️ Слоты берём ТОЛЬКО из выровненных блоков: писать по ним можно лишь туда,
        # где соответствие доказано, а не предположено.
        # ⚠️ Признак СТРУКТУРНЫЙ, а не «есть ли текст»: у боевых файлов текст есть почти
        # везде, и по булевой маске difflib выравнивал 44 формы из 290. Число слотов и
        # вставок у перевода то же, что у оригинала (перевод их не меняет), поэтому по
        # ним ряды сходятся: 285 из 290.
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
            print(f'  · {p.name}: {len(fe)}/{len(fj)} форм — выровнено {kept}')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min', type=int, default=2, help='ignore fragments seen fewer times')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    data = pairs_by_file()
    # ⚠️ Ведущий перенос -- это РАСКЛАДКА, а не перевод. Реплика, печатаемая с колонки 39,
    # обязана начинаться с новой строки, иначе имя говорящего не влезет в остаток; та же
    # реплика с колонки 0 переноса не требует. Считать это разнобоем -- значит краснеть на
    # собственном лечении, что и случилось 2026-09-15 сразу после починки пяти подписей.
    var = collections.defaultdict(collections.Counter)
    for rows in data.values():
        for ja, en, _, _, _ in rows:
            # ⚠️ Текст здесь СЫРОЙ, не разэкранированный: перенос записан двумя знаками
            # `\` и `n`, и `lstrip('\n')` его не видит (проверено -- отчёт не изменился).
            var[ja][re.sub(r'^(?:\\n)+', '', en)] += 1

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
    # ⚠️ Правки копим ПО ФАЙЛУ-ВЛАДЕЛЬЦУ: форма из семьи может лежать не в том файле, по
    # которому мы её нашли, и запись «куда попало» уже ломала перевод.
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
        print(f'  {fname:22s} выровнено форм: {counted[fname]}')
    total = sum(counted.values())
    print(f'итого переписано форм: {total} — теперь прогони tools/recompile.py')


if __name__ == '__main__':
    main()
