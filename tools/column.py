#!/usr/bin/env python3
"""С какой КОЛОНКИ реплика начнёт печататься -- то, чего раскладка не знала.

## Что было не так

`relayout.py` раскладывал каждую форму `(text …)` от нулевой колонки. Но движок печатает
в одно окно подряд, и форме нередко предшествует другая -- без `(wait)` между ними:

    (cond ((== D 2) (<> (text "Front. ")))
          ((== D 1) (<> (text "Left. ")))
          ((== D 3) (<> (text "Right. ")))
          (else (<>)))
    (text "The door has a sign: 'Prison, no entry for unauthorized\\npersons.'")

Строка сама по себе 55 знаков -- влезает. Но «Front. » сдвигает её на 7, выходит 62, и
движок рвёт по краю окна ПОСЕРЕДИНЕ СЛОВА:

    Front. The door has a sign: 'Prison, no entry for unauth
    orized
    persons.'

⚠️ И окно при этом раздувается по самой длинной строке, вылезая за рамку -- «чёрный
квадрат» на экране это он и есть. Один корень, два симптома.

## Что делаем

Считаем колонку по самому скрипту. Ветки `cond`/`if` -- АЛЬТЕРНАТИВЫ, поэтому берём по
ним максимум, а не сумму: иначе три направления из примера дали бы 20 колонок вместо 7 и
текст сузился бы на ровном месте.

⚠️ Ширина `(number …)` неизвестна до выполнения -- считаем по потолку в 5 знаков.
⚠️ Подстановка имени -- `NAME` знаков (имя вводит игрок; у Astral и Pollux их 6).
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from split import close, sexprs                                      # noqa: E402
from strings import forms, unescape                                  # noqa: E402

NAME = 6          # столько занимает подстановка имени
NUMBER = 5        # потолок ширины (number …)
# ⚠️ `(str …)` тоже печатает в окно сообщения: боевое «<имя> equipped 'STICK'!!» собирает
# название оружия именно им. Сперва я его выкинул, решив, что это только ярлыки панели, --
# и колонка у хвоста «'!!» ушла на пять знаков вниз. Настоящая причина колонок за сорок была
# другая: цепочка `(if …)` с одинаковым условием складывалась вместо выбора (см. _chain).
PRINTS = ('text', 'str')
RESETS = ('wait', 'clear', 'menu-show', 'mes-call', 'mes-jump')

# ⚠️ Окно сообщения очищает не только `(wait)`. Целые сцены построены на `(text …) (proc 28)
# (text …) (proc 28)`, где `proc 28` -- «дождись нажатия и очисти». Без этого знания колонка
# копилась через всю сцену: у реплики «[Torturer 1]: Hmph…» вышло 45, раскладка решила, что
# места нет, и разорвала САМО ИМЯ -- «[Torturer» / «1]: Hmph…».
# Список не вбит руками: он выведен из `en/START.MES.rkt` -- процедура очищает окно, если в
# её теле есть `(clear)`. Замер: это 10, 25, 26, 28.
_RESET_PROCS = None


def reset_procs():
    """Номера процедур, которые очищают окно сообщения -- по телу из START.MES."""
    global _RESET_PROCS
    if _RESET_PROCS is None:
        out = set()
        try:
            src = (pathlib.Path(__file__).resolve().parent.parent
                   / 'en/START.MES.rkt').read_text(encoding='utf-8')
            for m in re.finditer(r'\(define-proc (\d+)', src):
                a = m.start()
                if '(clear)' in src[a:close(src, a)]:
                    out.add(int(m.group(1)))
        except OSError:
            pass
        _RESET_PROCS = out
    return _RESET_PROCS
BRANCH = ('cond', 'if', 'if-else')
SEQ = ('<>', 'else', 'while', '<.>', 'mes', 'slot')
# ⚠️ Тело процедуры печатает с той колонки, какая была В МЕСТЕ ВЫЗОВА, а не там,
# где процедура объявлена. Считать её в общую цепочку -- чушь: колонка приезжает
# из соседнего кода, и `(text " were injured!!")` получал разрыв на ровном месте.
# Тело считаем от нуля, а наружу колонку не отдаём.
PROC = 'define-proc'

_HEAD = re.compile(r'\(\s*([^\s()]+)')
_MARK = re.compile(r'\{(\d+)\}')


def head(src, a):
    m = _HEAD.match(src, a)
    return m.group(1) if m else ''


def printed(src, a, e):
    """Что напечатает форма (text …) / (str …): текст с маркерами подстановок."""
    fs = forms(src[a:e])
    if not fs:
        return '?' * NUMBER if '(number' in src[a:e] else ''
    out = _MARK.sub('x' * NAME, unescape(fs[0]['ja']))
    return out.replace('(number', '?' * NUMBER)


def walk(src, on_print):
    """Пройти файл, зовя `on_print(a, e, col)` на каждой печатающей форме.

    Возврат колбэка -- ТЕКСТ, которым форма печатает на самом деле. Это и позволяет
    раскладке идти ОДНИМ проходом слева направо: она возвращает уже переложенный текст, и
    колонка следующей формы считается по нему, а не по старому.

    ⚠️ Без этого раскладка не сходится. Считать колонки один раз, потом переложить всё и
    посчитать заново -- значит гонять систему по кругу: замер дал 233 брака после первого
    прохода, 636 после второго, падение на третьем.
    """
    for a, e in sexprs(src, 0, len(src)):
        _walk(src, a, e, 0, None, on_print)
    return


def _walk(src, a, e, col, out, on_print=None):
    """Колонка после выполнения [a, e). Попутно записывает колонку каждой печати в `out`.

    ⚠️ Один проход на файл, а не поиск на каждую форму: по форме на проход это 22 000
    обходов всего текста игры, минуты вместо секунды.
    """
    h = head(src, a)
    if h in RESETS:
        return 0
    if h == 'proc':
        m = re.match(r'\(\s*proc\s+(\d+)', src[a:e])
        return 0 if m and int(m.group(1)) in reset_procs() else col
    if h in PRINTS:
        if out is not None:
            out[a] = col
        t = on_print(a, e, col) if on_print else printed(src, a, e)
        return len(t.rsplit('\n', 1)[1]) if '\n' in t else col + len(t)
    if h in BRANCH:
        # ⚠️ Ветки -- АЛЬТЕРНАТИВЫ: максимум, а не сумма.
        # ⚠️ Ветка `cond` выглядит как `((условие) (тело))` -- у неё НЕТ головы-опкода, и
        # обход по голове проваливался мимо неё молча. Внутрь ветки идём как в цепочку.
        best = col
        for s, x in sexprs(src, a + 1 + len(h), e - 1):
            best = max(best, _chain(src, s + 1, x - 1, col, out, on_print))
        return best
    if h == PROC:
        # ⚠️ Текст процедуры, начинающийся с ПРОБЕЛА, продолжает уже напечатанное имя:
        # `(define-proc 43 (<> (text " were injured!!")))` печатается сразу после имени
        # героя или противника. Считаем такую от ширины имени -- иначе три реплики
        # («… attacked, trying to engulf …», «… swung their swords …») вылезают за окно и
        # рвутся посередине слова.
        #
        # ⚠️⚠️ Ширина имени -- НЕ константа `NAME = 6`. В бою имя печатает `proc 41` из той же
        # группы объявлений, и это имя ПРОТИВНИКА: от «Delta» (5) до «A Suspicious Woman»
        # (18). Замер трёх моделей (`procwidth2.py`): `proc` = 0 находил 19 случаев брака,
        # «имя своей ветки» -- 85, «максимум по файлу» -- 131. Первая слепа, третья сужает
        # текст зря; верна вторая, потому что `(define-proc 41 (text "Light Knight"))` стоит
        # рядом с `(define-proc 42 (text " raised their sword!!"))` -- ширина известна точно.
        _chain(src, a + 1 + len(h), e - 1,
               _name_width(src, a) if _continues(src, a, e) else 0,
               out, on_print)
        return col
    if h in SEQ:
        return _chain(src, a + 1 + len(h), e - 1, col, out, on_print)
    return col                      # прочие опкоды ничего не печатают


def _continues(src, a, e):
    """Тело процедуры начинается с пробела -- значит продолжает напечатанное имя."""
    fs = forms(src[a:e])
    return bool(fs) and unescape(fs[0]['ja']).startswith(' ')


_NAMEPROC = re.compile(r'\(define-proc 41\b')


def _name_width(src, a):
    """Ширина имени, которое напечатают ПЕРЕД этим телом процедуры.

    Имя объявляет `(define-proc 41 (text "…"))` в той же группе, поэтому берётся ближайшее
    ТАКОЕ объявление ВЫШЕ по файлу. Нет его (обычная сцена, а не бой) -- значит имя подставит
    движок из сохранения, и это `NAME` знаков.
    """
    best = None
    for m in _NAMEPROC.finditer(src, 0, a):
        best = m.start()
    if best is None:
        return NAME
    fs = forms(src[best:close(src, best) + 1])
    if not fs:
        return NAME
    return max(NAME, len(unescape(fs[0]['ja'])))


_NUM = re.compile(r'\d+')


def _shape(src, a, e):
    """«Форма» условия: то же выражение с обнулёнными числами.

    Идиома игры -- разбор случая ЦЕПОЧКОЙ независимых `(if …)`:

        (if (== (- V 3) 2) (<> (text "Gold Bar")))
        (if (== (- V 3) 3) (<> (text "Ascension Stone")))

    Структурно это последовательность, по смыслу -- взаимоисключающие ветки. Складывать их
    ширины нельзя: так у боевых файлов набегала колонка 45-53, раскладка считала, что места
    нет, и рвала реплику раньше времени -- вплоть до разрыва ИМЕНИ говорящего.
    """
    inner = sexprs(src, a + 1 + len(head(src, a)), e - 1)
    if not inner:
        return None
    c0, c1 = inner[0]
    return _NUM.sub('#', re.sub(r'\s+', ' ', src[c0:c1]))


def _chain(src, a, e, col, out, on_print=None):
    """Подряд идущие выражения в [a, e): колонка накапливается.

    ⚠️ Кроме цепочки `(if …)` с одинаковой формой условия -- это разбор случая, и по ней
    берётся максимум, как по веткам `cond`.
    """
    cur = col
    spans = sexprs(src, a, e)
    i = 0
    while i < len(spans):
        s, x = spans[i]
        if head(src, s) == 'if':
            shape = _shape(src, s, x)
            j = i
            best = cur
            while (j < len(spans) and head(src, spans[j][0]) == 'if'
                   and shape is not None and _shape(src, *spans[j]) == shape):
                best = max(best, _walk(src, spans[j][0], spans[j][1], cur, out, on_print))
                j += 1
            if j > i:
                cur = best
                i = j
                continue
        cur = _walk(src, s, x, cur, out, on_print)
        i += 1
    return cur


def columns(src):
    """{смещение формы -> колонка, с которой она начнёт печатать}."""
    out = {}
    for a, e in sexprs(src, 0, len(src)):
        _walk(src, a, e, 0, out)
    return out


def scan(en_dir):
    """[(файл, смещение, колонка)] для форм, которые НЕ начинаются с нуля."""
    rows = []
    for p in sorted(pathlib.Path(en_dir).glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        src = p.read_text(encoding='utf-8')
        cols = columns(src)
        for f in forms(src):
            c = cols.get(f['start'], 0)
            if c:
                rows.append((p.name, f['start'], c))
    return rows


if __name__ == '__main__':
    from collections import Counter
    root = pathlib.Path(__file__).resolve().parent.parent
    rows = scan(root / 'en')
    print(f'форм, начинающихся не с нулевой колонки: {len(rows)}')
    print('по колонке:', Counter(c for *_, c in rows).most_common(10))
