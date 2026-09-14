#!/usr/bin/env python3
"""Вернуть вычитанный текст из `text/*.json` обратно в `en/*.rkt` — через гейты, а не на веру.

Корректор правит плоский JSON (`tools/export_text.py`). Здесь правки возвращаются на место,
и КАЖДАЯ проходит проверку. Без этого вычитка — это непроверенный текст прямо в игру, а мы
уже знаем, чем такое кончается: строка длиннее окна рвёт слово, потерянный маркер `{0}`
превращается в «Astralwas», лишний байт уводит файл за буфер и игра выходит в DOS.

Что проверяется у каждой изменённой реплики:

| проверка | что ловит |
|---|---|
| отпечаток `was` | правили не ту строку: файл сдвинулся с момента выгрузки |
| маркеры | `{0}` пропал, размножился или переехал |
| кодировка | всё, что вне знакогенератора игры |
| раскладка | реплика не влезает в окно, рвёт слово, оставляет сироту |
| размер | файл уехал за `gates.MES_MAX` — это смерть при заходе в комнату |

⚠️ Ничего не пишет без `--apply`, и при первой же непройденной проверке НЕ пишет вовсе:
половина принятой вычитки хуже, чем ни одной, — потом не найти, что применилось.

    tools/import_text.py            # показать, что изменится
    tools/import_text.py --apply    # записать и пересобрать затронутые .mes
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

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
    """Что не так с новой репликой. Пусто — значит можно брать."""
    bad = []
    if MARK.findall(old) != MARK.findall(new):
        bad.append(f'маркеры {MARK.findall(old)} стали {MARK.findall(new)}')
    if gates.gate_charset([new]):
        bad.append('знаки вне кодировки игры')
    why = flaws(screen(parts_of(new), w=None, col0=c0), col0=c0)
    if why:
        bad.append('раскладка: ' + ', '.join(sorted(set(why))))
    return bad


def main(apply):
    if not TEXT.is_dir():
        sys.exit(f'нет {TEXT} — сперва tools/export_text.py')
    changes, problems = {}, []
    for jf in sorted(TEXT.glob('*.MES.json')):
        name = jf.name[:-5]
        src_p = EN / f'{name}.rkt'
        if not src_p.exists():
            problems.append(f'{name}: нет такого файла в en/')
            continue
        src = src_p.read_text(encoding='utf-8')
        fs = forms(src)
        cols = column.columns(src)
        for row in json.loads(jf.read_text(encoding='utf-8')):
            i = int(row['id'].split('#')[1])
            if i >= len(fs):
                problems.append(f'{row["id"]}: формы с таким номером больше нет')
                continue
            f = fs[i]
            old = unescape(f['ja'])
            if hashlib.blake2b(old.encode(), digest_size=4).hexdigest() != row['was']:
                problems.append(f'{row["id"]}: исходная строка изменилась после выгрузки')
                continue
            new = row['en']
            if new == old:
                continue
            bad = check(old, new, cols.get(f['start'], 0))
            if bad:
                problems.append(f'{row["id"]}: ' + '; '.join(bad))
                continue
            pieces = split_translation(new, len(f['slots']), len(f['ins']),
                                       f.get('lead', 0), f.get('trail', 0))
            if pieces is None:
                problems.append(f'{row["id"]}: не раскладывается по слотам формы')
                continue
            changes.setdefault(name, []).append((f, pieces, old, new))

    for name, rows in sorted(changes.items()):
        print(f'  {name:16} правок {len(rows)}')
        for _, _, old, new in rows[:2]:
            print(f'      было : {old[:66]}')
            print(f'      стало: {new[:66]}')
    if problems:
        print(f'\n❌ не прошли проверку: {len(problems)}')
        for p in problems[:12]:
            print(f'   {p}')
        sys.exit('\nНИЧЕГО НЕ ЗАПИСАНО: сначала исправить перечисленное')
    if not changes:
        print('правок нет')
        return
    print(f'\nфайлов к правке: {len(changes)}, реплик: {sum(len(v) for v in changes.values())}')
    if not apply:
        print('\nНЕ ЗАПИСАНО. Применить: tools/import_text.py --apply')
        return
    for name, rows in changes.items():
        src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
        edits = []
        for f, pieces, _, _ in rows:
            for (x, y), piece in zip(f['slots'], pieces):
                edits.append((x, y, escape(piece)))
        (EN / f'{name}.rkt').write_text(patch(src, edits), encoding='utf-8')
        subprocess.run(['racket', str(ROOT / 'tools/juice/mes/juice.rkt'), '-cf',
                        f'{name}.rkt'], cwd=EN, capture_output=True, timeout=900)
        mes = EN / f'{name}.rkt.mes'
        size = mes.stat().st_size if mes.exists() else 0
        over = ' ⚠️ ЗА ПОРОГОМ' if size > gates.MES_MAX else ''
        print(f'  {name:16} записан, {size} б{over}')
    print('\n⚠️ дальше обязательно: tools/verify.py и приёмка запуском')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    main(ap.parse_args().apply)
