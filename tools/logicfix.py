#!/usr/bin/env python3
"""Исправления ЛОГИКИ игры -- объявленные, а не тихие.

    tools/logicfix.py            # что применено / что нет
    tools/logicfix.py --apply    # применить к en/*.rkt (идемпотентно)

Перевод меняет только текст, и гейт структуры (gates.py) это стережёт: любое изменение
инструкций -- провал. Но в оригинале есть ошибки, которые игрок видит как «не работает», и
они живут в логике скрипта. Такие правки перечислены здесь -- ЕДИНСТВЕННЫЙ источник: этот
инструмент применяет их к переводу, а гейт -- к эталону перед сравнением. Разрешено ровно
объявленное изменение и ничего сверх.

Каждая запись: файл, было, стало, почему -- с замером.
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'

FIXES = {
    'START1.MES': [(
        '(&& (== (~ @ 10) 639) (== (~ @ 11) 399))',
        '(&& (== (~ @ 10) 639) (> (~ @ 11) 390))',
        'Титульное меню: стрелки паркуют курсор `(mouse 3 639 399)`, а движок не пускает '
        'его ниже y=391 -- замер: после «вверх» курсор в (639, 391). Подтверждение с '
        'клавиатуры требовало y == 399 и не срабатывало НИКОГДА после стрелок, поэтому '
        'New Game был недоступен и на японском оригинале. Порог > 390 проходит и при 391, '
        'и при 399.',
    )],
}


def fixed(name, src):
    """Текст с применёнными исправлениями этого файла."""
    for old, new, _ in FIXES.get(name, []):
        src = src.replace(old, new)
    return src


def status():
    rows = []
    for name, fixes in FIXES.items():
        src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
        for old, new, why in fixes:
            rows.append((name, 'применено' if new in src and old not in src else 'НЕТ', why[:70]))
    return rows


if __name__ == '__main__':
    if '--apply' in sys.argv:
        for name in FIXES:
            p = EN / f'{name}.rkt'
            p.write_text(fixed(name, p.read_text(encoding='utf-8')), encoding='utf-8')
    for name, st, why in status():
        print(f'  {name:12} {st:10} {why}')
