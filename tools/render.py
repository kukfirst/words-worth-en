#!/usr/bin/env python3
"""Что игрок увидит в окне сообщения -- посчитанное, а не угаданное.

Разрывы строк ставят ДВА механизма, и беда живёт ровно на их стыке:

1. **juice при компиляции** (`engine/ai5/mes-compiler.rkt`, `text-wrap*`) режет
   КАЖДЫЙ строковый кусок ОТДЕЛЬНО и ОТ НУЛЕВОЙ КОЛОНКИ по `(wordwrap 46)`, вставляя
   жёсткий перевод строки. Подстановку имени (`0`) он пропускает мимо и колонку через
   неё не переносит.
2. **движок при выводе** заполняет окно шириной 56 и рвёт по краю где придётся --
   японскому перенос по словам не нужен.

Отсюда типовой брак: кусок из 43 знаков juice считает коротким, а на экране он
начинается с 16-й колонки, выходит 59 и рвётся по краю -- слово-сирота на своей строке,
а следом жёсткий перевод строки от juice её закрывает.

Здесь оба механизма воспроизведены по коду компилятора, так что раскладку можно
проверять без эмулятора. Сверено с кадром: `FLOOR05B` реплика Тэсио -- 4 строки знак
в знак (`tools/render.py --selftest`).
"""
import re

import re

_MARK = re.compile(r'\{(\d+)\}')

WRAP = 46          # (wordwrap 46) из meta -- порог juice
WIDTH = 56         # ширина окна сообщения, снята с кадра findings/0015.png
NAME = 6           # длина имени героя; в наших сохранениях Astral/Pollux -- по 6
# ⚠️ Рвём на ОДИН знак раньше края окна. Строка ровно в 56 знаков движок переносит САМ, и
# наш явный перевод строки ложится вторым -- на экране появляется пустая строка (замерено:
# 867 реплик после первой версии раскладки). Один знак плотности дешевле этой ямы, и заодно
# страхует, если полезная ширина окна на самом деле 55, а не 56.
LINE = WIDTH - 1
# ⚠️ Потолок добивки. Ряд в полсотни пробелов УБИВАЕТ игру -- на этом валилось сохранение
# в комнате героя (STATUS.md §12). Не влезли в потолок -- разрыв не ставим вовсе.
MAX_PAD = 16


def _chop(words, w):
    """Один проход juice: сколько слов влезает в порог. Цена слова -- len+1."""
    take, c = [], 0
    for s in words:
        n = len(s) + 1
        if w < c + n:
            break
        c += n
        take.append(s)
    return take, words[len(take):]


def wrap_chunk(s, w=WRAP):
    """`text-wrap*`: строковый кусок -> куски со вставленными переводами строки."""
    if not w:
        return [s]
    words = s.split(' ')
    if not words or w <= max(len(x) for x in words) + 1:
        return [s]                      # длинное слово -- juice не трогает кусок вовсе
    rest, out = words, []
    while rest:
        take, rest = _chop(rest, (w // 2) * 2)
        if not take:                    # защита от зацикливания
            out.append(' '.join(rest)); break
        out.append(' '.join(take))
    return [x + '\n' for x in out[:-1]] + [out[-1]]


def compile_parts(parts, w=WRAP):
    """Что окажется в .mes: строки уже с жёсткими переводами строки от juice.

    `parts` -- элементы формы (text …): строки и не-строки (подстановка имени).
    juice сперва склеивает соседние строки, не оканчивающиеся переводом строки.
    """
    merged, out = [], []
    for p in parts:
        if isinstance(p, str) and merged and isinstance(merged[-1], str) \
                and not merged[-1].endswith('\n'):
            merged[-1] += p
        else:
            merged.append(p)
    for p in merged:
        out.extend(wrap_chunk(p, w) if isinstance(p, str) else [p])
    return out


def screen(parts, name=NAME, w=WRAP, width=WIDTH, col0=0):
    """Строки на экране: сначала компиляция juice, потом заполнение окна движком.

    `col0` -- колонка, на которой форма начинает печатать (см. `tools/column.py`).
    Первая строка при этом короче на `col0` -- ровно так её и заполняет движок.
    """
    text = ''.join('x' * name if not isinstance(p, str) else p
                   for p in compile_parts(parts, w))
    lines, col, cur = [], col0, ''
    for ch in text:
        if ch == '\n':
            lines.append(cur); cur, col = '', 0
            continue
        cur += ch; col += 1
        if col == width:
            lines.append(cur); cur, col = '', 0
    if cur:
        lines.append(cur)
    return lines


def layout_text(en, name=NAME, width=LINE, col0=0):
    """Плоский текст с маркерами {N} -> он же с явными переводами строк.

    Считаем колонку сквозь всю реплику: подстановка имени -- такое же слово шириной
    `name`, и переносится целиком. Раньше она считалась в колонку, но переносом НЕ
    проверялась, и уезжала за край: `...weaker than xxxxx` / `x` / `.` -- имя разорвано
    пополам, точка отдельной строкой (55 реплик, замер 2026-09-11).

    Работаем на плоском тексте, а не на элементах формы, потому что разрыв нередко нужен
    ПЕРЕД подстановкой -- то есть в конце предыдущего элемента. На плоской строке это
    просто позиция, а по элементам приходилось бы тянуться назад.
    """
    def wide(tok):
        return len(_MARK.sub('X' * name, tok))

    # ⚠️ col0 -- КОЛОНКА, НА КОТОРОЙ ФОРМА НАЧНЁТ ПЕЧАТАТЬ. Не всегда ноль: движок пишет в
    # одно окно подряд, и форме нередко предшествует другая без (wait) между ними -- имя
    # говорящего собирается из трёх форм, направление печатается отдельным «Front. ».
    # Раскладка этого не знала, строка уезжала за край и движок рвал её ПОСЕРЕДИНЕ СЛОВА
    # (`tools/column.py`).
    out, col, fresh = [], col0, False     # fresh -- строку только что перенесли
    for word, sep in _words(en):
        n = wide(word)
        if col + n > width and col:
            while out and out[-1].isspace():
                out.pop()
            out.append('\n')
            col, fresh = 0, True
        if word:
            out.append(word); col += n; fresh = False
            if sep:
                out.append(sep); col += len(sep)
        elif not fresh:
            # ⚠️ ВЕДУЩИЙ ПРОБЕЛ КУСКА -- ЗНАЧИМЫЙ. Кусок нередко продолжает то, что уже
            # выведено соседней формой: `(text " as many Healing Herbs…")`. Снятый пробел
            # склеивает слова -- это дефект класса «Manfound», который агент и ловит
            # глазами. Снимаем пробел ТОЛЬКО сразу после переноса, где он стал бы отступом.
            out.append(sep); col += len(sep)
    return ''.join(out)



def pad_breaks(s, name=NAME, width=WIDTH, max_pad=MAX_PAD, col0=0):
    """Заменить явные переводы строк добивкой пробелами до края окна.

    ⚠️ Нужно там, где реплика несёт НЕ подстановку имени, а `(number …)`. Явный перевод
    строки режет форму надвое, и в скелете появляется лишняя `(TEXT )`; гейт схлопывает
    соседние текстовые формы, но только пока внутри них нет вложенной формы -- а `(number …)`
    как раз вложенная. Добивка же меняет ТОЛЬКО содержимое строки, которое `skeleton()`
    обнуляет, поэтому структура остаётся прежней.

    ⚠️ Ширина числа неизвестна: `(number …)` печатает от одной цифры до пяти, а считаем мы
    его как имя (`NAME`). Добивка тут приблизительна -- но не хуже прежней, которая тоже
    считала по имени. Таких реплик 18 на всю игру (замер 2026-09-11).
    """
    segs = s.split('\n')
    out, col = [], col0
    for i, seg in enumerate(segs):
        out.append(seg)
        col += len(_MARK.sub('X' * name, seg))
        if i < len(segs) - 1:
            need = width - col
            if 0 < need <= max_pad:
                out.append(' ' * need)
                col = 0
            else:
                col %= width          # добивка не влезла -- движок порвёт сам
    return ''.join(out)

def _words(s):
    """Слова и разделители после них: («Привет», « ») …"""
    out, i = [], 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] != ' ':
            j += 1
        k = j
        while k < len(s) and s[k] == ' ':
            k += 1
        out.append((s[i:j], s[j:k]))
        i = k
    return out


# ---------------------------------------------------------------- ревизия раскладки
import pathlib, re, sys                                              # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from strings import forms                                            # noqa: E402
from strings import unescape                                         # noqa: E402



def parts_of(en):
    """Текст с маркерами {N} -> элементы формы (text …): строки и подстановки."""
    out, i = [], 0
    for m in _MARK.finditer(en):
        if m.start() > i:
            out.append(en[i:m.start()])
        out.append(int(m.group(1)))
        i = m.end()
    if i < len(en):
        out.append(en[i:])
    return out


def flaws(lines, width=WIDTH, col0=0):
    """Что здесь плохо для читателя.

    ⚠️ `col0` обязателен для ПЕРВОЙ строки: она короче на столько, на сколько форма
    начинает печатать не с нуля. Без него разорванное слово не опознавалось -- строка
    «...for unauth» длиной 48 не равна ширине окна, а на экране она ровно 56 вместе с
    напечатанным до неё «Front. ». Так дефект и проехал мимо проверки.
    """
    bad = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        full = len(ln) + (col0 if i == 0 else 0)
        # ⚠️ Сеть на «вылезли на один квадрат справа» (игрок, 2026-09-13, магазин).
        # Замер по кадрам (`emu/textbox.py`, сверено здесь же): знакоместа идут с x=96
        # шагом 8, колонок 56, последняя кончается на 543, чёрное окно -- на 545. То есть
        # 56 колонок ровно влезают, а 57-я (544…551) ложится уже на рамку и после очистки
        # окна там и остаётся -- пробел движок рисует чёрным глифом, а не пустотой.
        # `layout_text` рвёт по LINE=55 и до края не доходит НИКОГДА. Единственные строки
        # ровно в 56 -- те, что `pad_breaks` добил пробелами до WIDTH; их 19, все денежные
        # реплики магазинов, и брак игрок видит именно в магазинах.
        # ⚠️ ЧЕГО НЕ ДОКАЗАНО: почему движок ставит 57-е знакоместо, если рвёт по 56-му.
        # Пока правило -- подозрение по совпадению места, а не измеренная причина.
        if full > LINE:
            bad.append('строка шире поля текста')
        if full == width and nxt and ln[-1] != ' ' and nxt[:1] not in ('', ' '):
            bad.append('слово разорвано')
        if re.search(r'\S {2,}\S', ln):
            bad.append('дыра в строке')
        # сирота: короткая строка после полной, и это не конец реплики
        prev = len(lines[i - 1]) + (col0 if i == 1 else 0) if i else 0
        if nxt is not None and i and prev >= width - 1 and len(ln.strip()) <= 12:
            bad.append('слово-сирота')
    return bad


def scan(en_dir):
    rows = []
    for f in sorted(pathlib.Path(en_dir).glob('*.MES.rkt')):
        if f.name.endswith('.orig.rkt'):
            continue
        src = f.read_text(encoding='utf-8')
        import column
        cols = column.columns(src)
        for form in forms(src):
            en = unescape(form['ja'])
            if any(ord(c) > 126 for c in en):
                continue                       # японский: перенос по словам не нужен
            # ⚠️ w=0: переноса от juice БОЛЬШЕ НЕТ -- `relayout.py` снял `(wordwrap 46)` из
            # meta всех 62 файлов (проверяется `grep -l wordwrap en/*.MES.rkt` -> пусто).
            # Со старым порогом ревизия считала лишний механизм и выдавала 26 «сирот» на
            # ровном месте; на самих кадрах их нет.
            c0 = cols.get(form['start'], 0)
            ls = screen(parts_of(en), w=0, col0=c0)
            bad = flaws(ls, col0=c0)
            if bad:
                rows.append((f.name[:-len('.rkt')], en, ls, sorted(set(bad))))
    return rows


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        p = ["[Teshio]: ", 0, ", is it because of that ring that you   and Sharon "
             "aren't getting along? ...They say Sharon     prefers a manly man."]
        want = ['[Teshio]: xxxxxx, is it because of that ring that you   ',
                'and',
                "Sharon aren't getting along? ...They say",
                'Sharon     prefers a manly man.']
        got = screen(p)
        assert got == want, f'симулятор разошёлся с кадром:\n{got}\n{want}'
        print('✅ самопроверка: кадр Тэсио воспроизведён знак в знак')
    else:
        rows = scan(pathlib.Path(__file__).resolve().parent.parent / 'en')
        from collections import Counter
        c = Counter(k for *_, bad in rows for k in bad)
        print(f'реплик с браком раскладки: {len(rows)}')
        for k, n in c.most_common():
            print(f'  {k}: {n}')
        for name, en, ls, bad in rows[:6]:
            print(f'\n--- {name}  [{", ".join(bad)}]')
            for l in ls:
                print(f'    |{l}|')
