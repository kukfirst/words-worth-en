#!/usr/bin/env python3
"""Проверка по КАДРАМ: что реально написано в окне сообщения, а не что мы думаем.

Зачем отдельно от `tools/relayout.py --check`. Та проверка судит по исходнику и по модели
раскладки. Модель бывает неполна -- ровно так проехал дефект «Front. The door has a sign:
'Prison, no entry for unauth / orized»: форма сама по себе укладывалась в окно, а печаталась
после чужого «Front. » (`tools/column.py`). Проверка по исходнику этого не видела, потому
что не знала про колонку; кадр видел сразу.

Здесь читается то, что на экране: `emu/textbox.py` снимает знакоместа растром, и ловятся

  * разорванные слова -- строка занимает всё окно и следующая начинается с буквы;
  * дыры -- два и более пробела посреди строки;
  * нечитаемые знакоместа -- растр, которого нет в эталонах.

Кадры берутся из `emu/replay/` (их пишет агент на каждое нажатие человека), `emu/rec/` и
`emu/shots/`. ⚠️ Это НЕ полное покрытие: проверяется только то, до чего дошли ногами. Гейт
по исходнику остаётся главным, а этот -- вторая, независимая сеть.

    tools/screenqa.py              # все кадры
    tools/screenqa.py --since 2h   # только свежие
"""
import argparse
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'emu'))
import textbox                                                       # noqa: E402

HOLE = re.compile(r'\S {2,}\S')
DIRS = ('replay', 'rec', 'shots', 'audit', 'findings')


def frames(since=None):
    for d in DIRS:
        for p in sorted((ROOT / 'emu' / d).glob('*.png')):
            if since and p.stat().st_mtime < since:
                continue
            yield p


def defects(lines, width):
    """Брак раскладки и, ОТДЕЛЬНО, нечитаемые знакоместа.

    ⚠️ Нечитаемое в самом КОНЦЕ текста -- не брак, а кадр, снятый посреди печати: последний
    знак дорисован наполовину. Такие кадры агент пишет на каждое нажатие, и без этой
    оговорки весь отчёт состоит из них.
    ⚠️ Нечитаемое в СЕРЕДИНЕ -- почти всегда цифра 6..9, которых нет в эталонах шрифта
    (`emu/textbox.py`). Это пробел в ЧТЕНИИ, а не в тексте, поэтому гейт на нём не валится.
    """
    bad = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if len(ln) >= width and nxt and ln[-1:].strip() and nxt[:1].strip():
            bad.append('слово разорвано')
        if HOLE.search(ln):
            bad.append('дыра в строке')
    text = '\n'.join(lines).rstrip()
    return sorted(set(bad)), textbox.UNKNOWN in text[:-1]


def scan(since=None):
    from PIL import Image
    import textsrc
    idx = textsrc.index()
    seen, rows = set(), []
    for p in frames(since):
        try:
            im = Image.open(p).convert('RGB')
        except Exception:
            continue
        if im.size != (640, 400):
            continue
        lines = textbox.lines(im)
        if not any(l.strip() for l in lines):
            continue
        key = tuple(lines)
        if key in seen:
            continue
        seen.add(key)
        bad, unread = defects(lines, textbox.COLS)
        if not bad and not unread:
            continue
        if unread:
            bad = bad + ['нечитаемое знакоместо']
        try:
            src = textsrc.locate(lines, idx=idx)
        except Exception:
            src = None
        rows.append((p, lines, bad, src))
    return rows, len(seen)


def broken(rows):
    """Только настоящий брак текста -- по нему и валится гейт."""
    return [r for r in rows if any(b != 'нечитаемое знакоместо' for b in r[2])]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='только кадры за последние N (h/m), например 2h')
    a = ap.parse_args()
    since = None
    if a.since:
        mul = {'h': 3600, 'm': 60, 'd': 86400}[a.since[-1]]
        since = time.time() - float(a.since[:-1]) * mul
    rows, total = scan(since)
    hard = broken(rows)
    print(f'разных экранов прочитано: {total}, с браком раскладки: {len(hard)}, '
          f'с нечитаемыми знакоместами: {len(rows) - len(hard)}')
    for p, lines, bad, src in rows:
        where = f"{src['file']}:{src['line']}" if src else 'в исходнике не найдено'
        print(f"\n--- {p.name}  [{', '.join(bad)}]  -> {where}")
        for l in lines:
            print(f'    |{l}|')
    sys.exit(1 if hard else 0)
