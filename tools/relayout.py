#!/usr/bin/env python3
"""Разложить реплики по строкам самим -- вместо спора двух механизмов переноса.

## Что было не так

Разрывы ставили ДВОЕ. juice при компиляции резал каждый строковый кусок отдельно и от
нулевой колонки по `(wordwrap 46)`, подстановку имени пропуская мимо; движок при выводе
заполнял окно шириной 56 и рвал по краю. `wrapfix.py` чинил второе добивкой пробелами, но
про первое не знал -- и получались слова-сироты на своей строке и дыры посреди строки:

    [Teshio]: Astral, is it because of that ring that you
    and
    Sharon aren't getting along? ...They say
    Sharon     prefers a manly man.

Замер симулятором (`tools/render.py`, сверен с кадром знак в знак): **605 реплик**
с браком -- 451 дыра, 172 сироты, 32 разорванных слова.

## Что делаем

Снимаем `(wordwrap …)` из meta -- компилятор перестаёт резать сам, -- и ставим переводы
строк явно, считая колонку сквозь всю реплику вместе с подстановкой имени. Добивка
пробелами больше не нужна и снимается.

⚠️ Длина имени известна только в игре, его вводит игрок. Считаем по NAME=6 (столько у
Astral и Pollux). Для имени другой длины раскладка сместится -- но не хуже прежнего.

    tools/relayout.py            # померить
    tools/relayout.py --apply    # записать
"""
import argparse, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from strings import forms, patch, split_translation, unescape, escape
from render import layout_text, pad_breaks, parts_of, screen, flaws, NAME, WIDTH, _MARK
import column

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'
META_WRAP = re.compile(r'\s*\(wordwrap\s+\d+\)')


def relaid(en, padded=False, col0=0):
    """Текст с маркерами -> он же с расставленными переносами.

    `padded` -- ставить добивку вместо явных переводов строк: так делаем там, где реплика
    несёт `(number …)`, иначе форма режется надвое и гейт видит лишнюю `(TEXT )`.
    `col0` -- колонка, с которой форма начнёт печатать (`tools/column.py`).
    """
    flat = re.sub(r'  +', ' ', en.replace('\n', ' '))
    out = layout_text(flat, col0=col0)
    return pad_breaks(out, col0=col0) if padded else out


def fix_file(name, apply=False):
    """Переложить файл ОДНИМ проходом слева направо.

    ⚠️ Колонка каждой формы считается по УЖЕ переложенному тексту предыдущих -- иначе
    система не сходится: раскладка меняет ширину, ширина меняет колонки, колонки меняют
    раскладку. Замер на двух проходах «посчитать все колонки, потом переложить всё»:
    233 брака после первого, 636 после второго, падение на третьем.
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    out = META_WRAP.sub('', src, count=1)
    bystart = {f['start']: f for f in forms(out)}
    edits, touched = [], 0

    def on_print(a, e, col):
        f = bystart.get(a)
        if not f:
            return column.printed(out, a, e)
        en = unescape(f['ja'])
        if any(ord(c) > 126 for c in en):
            return column.printed(out, a, e)     # японский: перенос по словам не нужен
        # подстановка имени -- это `0`/`1`; всё прочее (`(number …)`) требует добивки
        new = relaid(en, padded=any(not i.isdigit() for i in f['ins']), col0=col)
        if new != en:
            pieces = split_translation(new, len(f['slots']), len(f['ins']),
                                       f['lead'], f['trail'])
            if pieces is not None:
                for (x, y), piece in zip(f['slots'], pieces):
                    edits.append((x, y, escape(piece)))
                nonlocal touched
                touched += 1
            else:
                new = en
        return column._MARK.sub('x' * column.NAME, new)

    column.walk(out, on_print)
    out = patch(out, edits) if edits else out
    if apply:
        (EN / f'{name}.rkt').write_text(out, encoding='utf-8')
    return touched, len(out) - len(src)


def check():
    """Инвариант раскладки: ни дыр, ни сирот, ни разорванных слов, ни рядов пробелов.

    Заменяет прежний `wrapfix.py --check`. Ряд из 2+ пробелов допустим ТОЛЬКО как добивка,
    то есть если он упирается ровно в край окна: добивка осталась в одной узкой роли -- в
    репликах с `(number …)`, где явный перевод строки резал бы форму надвое (`pad_breaks`).
    Плюс сверяется сама раскладка симулятором `tools/render.py`, проверенным кадром.

    ⚠️ Глазами этот класс дефектов не судится: в растровом шрифте 640x400 один пробел от
    двух не отличить, и QA-агент трижды докладывал о двойных пробелах, которых на кадре нет
    (findings 0016, 0017, 0020). Поэтому проверка -- по исходнику и счётом.
    """
    bad, residue, lost = [], [], []
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        src = p.read_text(encoding='utf-8')
        cols = column.columns(src)
        for f in forms(src):
            en = unescape(f['ja'])
            if any(ord(c) > 126 for c in en):
                continue
            c0 = cols.get(f['start'], 0)
            # ⚠️ КОЛОНКА БОЛЬШЕ ШИРИНЫ ОКНА -- модель потеряла курсор, судить нечем.
            # Так выходит там, где курсор ставится КООРДИНАТАМИ, а не печатью: экранная
            # клавиатура в NAME.MES рисует ряды букв через (set-arr~ @ 17 x y), и колонка
            # набегает до 74 при окне в 56. Считать `@ 17` переносом строки было бы честнее,
            # но это меняет колонку 169 формам в 29 файлах -- цена несопоставима с одной
            # ложной жалобой. Замер 2026-09-13: такая форма в игре РОВНО ОДНА.
            if c0 >= WIDTH:
                lost.append((p.name[:-4], en[:40]))
                continue
            why = flaws(screen(parts_of(en), w=None, col0=c0), col0=c0)
            # ⚠️ ОСТАТОК, который переложить нечем: форма начинается с подстановки
            # (`(text (number …) "!!")`), перед ней нет строкового куска, и положить туда
            # перевод строки некуда. Разрыв приходится на стык подстановки и хвоста -- это
            # некрасиво, но не разорванное слово. Пять таких на всю игру (замер 2026-09-11).
            if why and f['lead'] and en.lstrip().startswith('{'):
                residue.append((p.name[:-4], en[:40]))
                continue
            flat = en.replace('\n', ' ')
            for m in re.finditer(r'\S( {2,})\S', flat):
                col = len(_MARK.sub('X' * NAME, flat[:m.end() - 1])) % WIDTH
                if col:                   # ряд не упёрся в край окна -- значит не добивка
                    why.append('ряд пробелов не на краю окна')
            if why:
                bad.append((p.name[:-4], sorted(set(why)), en[:60]))
    print(f'реплик с браком раскладки: {len(bad)}')
    for n, why, t in bad[:8]:
        print(f'  {n:16} {", ".join(why)}: {t}')
    if residue:
        print(f'  (плюс {len(residue)} форм, начинающихся с подстановки -- разрыв положить '
              f'некуда: {", ".join(n for n, _ in residue)})')
    if lost:
        print(f'  (плюс {len(lost)} форм, где курсор ставится координатами и колонка модели '
              f'больше окна: {", ".join(n for n, _ in lost)})')
    return len(bad)


def names():
    return sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                  if not p.name.endswith('.orig.rkt'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('name', nargs='?')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true', help='проверить инвариант раскладки')
    a = ap.parse_args()
    if a.check:
        sys.exit(1 if check() else 0)
    tot = grew = 0
    for n in ([a.name] if a.name else names()):
        t, g = fix_file(n, a.apply)
        if t:
            print(f'  {n:16} переложено {t:4d}, {g:+d} знаков', flush=True)
        tot += t; grew += g
    print(f'\nитого: переложено {tot} реплик, {grew:+d} знаков')
    if not a.apply:
        print('НЕ ЗАПИСАНО. Применить: tools/relayout.py --apply')
