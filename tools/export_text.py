#!/usr/bin/env python3
"""Выложить перевод в вид, пригодный для ВЫЧИТКИ, а не для компилятора.

Корректору `.rkt` показывать нельзя: там s-выражения, слоты, подстановки и экранирование, и
любая правка руками ломает сборку. Здесь то же самое, но плоско: одна запись — одна реплика,
плюс то, КАК она ляжет на экран.

Формат записи:

    {"id": "FLOOR02.MES#123",          стабильный адрес: файл + порядковый номер формы
     "en": "[Kaiser]: Hey... ",        текст с маркерами {0} — их трогать нельзя
     "was": "9f3c1a2b",                отпечаток исходного текста: импорт проверит, что
                                       правили именно эту строку, а не съехавшую
     "screen": ["[Kaiser]: Hey...",    как реплика ляжет в окно 56 знаков (tools/render.py,
                "Clan."]}              сверено с кадрами) — по нему и видно кривой перенос

⚠️ Японского оригинала здесь НЕТ намеренно: полный скрипт игры — это охраняемый текст, и
именно поэтому сцена раздаёт патчи, а не тексты. Каталог `text/` не выкладывается.

    tools/export_text.py              # всё в text/
    tools/export_text.py FLOOR02.MES  # один файл
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
            continue                       # японские остатки и пустышки корректору не нужны
        c0 = cols.get(f['start'], 0)
        rows.append({
            'id': f'{name}#{i}',
            'en': en,
            'was': hashlib.blake2b(en.encode(), digest_size=4).hexdigest(),
            # ⚠️ Маркеры исходной строки едут ОТДЕЛЬНЫМ полем: без них проверка у корректора
            # не может сказать, что `{0}` потерялся -- сравнивать не с чем, кроме отпечатка,
            # а он говорит только «изменено», но не «чем именно».
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
        print(f'  {name:16} реплик {len(rows):5d}')
    print(f'\nвсего реплик: {total}, каталог: {OUT}')
    print('⚠️ text/ не выкладывается в git — это полный текст игры')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        main(sys.argv[1:])
    else:
        main(sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                    if not p.name.endswith('.orig.rkt')))
