#!/usr/bin/env python3
"""Перевести русские комментарии и docstring'и на английский — не трогая код.

Публичный репозиторий раздаётся людям, которые по-русски не читают, а весь смысл наших
инструментов записан именно в комментариях: почему порог такой, почему список считается, а
не пишется, на чём погорели прошлый раз. Без перевода публикуется код без объяснений.

## Почему это не «прогнать файл через модель»

Модель, переписывающая .py целиком, однажды переставит аргумент или съест отрицание, и это
не заметят: диффы тут на сотни строк. Поэтому меняются **только значения токенов COMMENT и
строковых литералов-документаций**, а доказательство — пересличение:

| проверка | что ловит |
|---|---|
| поток токенов | изменившийся тип или число токенов — то есть тронутый код |
| позиции | правку токена, которого не выбирали |
| компиляция | синтаксис, сломанный кавычкой или скобкой внутри комментария |
| кириллица | недопереведённый кусок |
| маркеры | съеденные `⚠️`, ссылки, имена файлов и чисел — их модель любит терять |

⚠️ Ничего не пишет без `--apply`. Файл, у которого не сошлась хоть одна проверка, не пишется
ЦЕЛИКОМ — половина переведённого файла хуже непереведённого.

    tools/comments_en.py                 # что найдено и что изменится
    tools/comments_en.py --apply         # записать
    tools/comments_en.py tools/gates.py  # один файл
"""
import argparse
import io
import pathlib
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import llm                                                          # noqa: E402

CYR = re.compile(r'[А-Яа-яЁё]')
# Куски, которые обязаны пережить перевод дословно: без них комментарий теряет адрес.
KEEP = re.compile(r'⚠️|§\d+|`[^`]+`|\b\d[\d_ ]*\b|[A-Za-z_][A-Za-z0-9_]*\.(?:py|rkt|mes|json|md)\b')

PROMPT = """You translate Russian source-code comments into English.

Rules:
- Translate the MEANING, in the voice of an engineer writing for other engineers.
- Keep every backtick span, every number, every filename, every § reference and every ⚠️
  EXACTLY as-is. They are addresses, not prose.
- Keep the leading '#' and the original indentation of each line.
- Keep the line structure: N lines in, N lines out.
- Do not add anything. Do not explain. Do not soften warnings.
- Output ONLY the translated block."""


def chunks(src):
    """Спаны токенов, которые нам можно менять: комментарии и строки-документации."""
    out = []
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            out.append(t)
        elif t.type == tokenize.STRING:
            # docstring -- строка, стоящая одна, как целая инструкция
            prev = next((p for p in reversed(toks[:i])
                         if p.type not in (tokenize.NL, tokenize.NEWLINE,
                                           tokenize.INDENT, tokenize.DEDENT)), None)
            if prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT,
                                             tokenize.DEDENT) or prev.string == ':':
                out.append(t)
    return [t for t in out if CYR.search(t.string)], toks


def skeleton(toks):
    """Скелет файла: типы всех токенов плюс значения тех, которые менять нельзя.

    ⚠️ Позиции сюда НЕ входят намеренно. Первая версия сличала `start`/`end` и отвергала
    любой перевод: английский текст другой длины, и колонки съезжают у всего, что справа.
    Это ловило не порчу кода, а собственную арифметику.
    """
    return [(t.type, None) if t.type in (tokenize.COMMENT, tokenize.STRING)
            else (t.type, t.string) for t in toks]


def replace(src, edits):
    """Подставить новые значения по спанам. Идём с конца, чтобы не съехали координаты."""
    lines = src.splitlines(keepends=True)
    for tok, new in sorted(edits, key=lambda e: e[0].start, reverse=True):
        (r1, c1), (r2, c2) = tok.start, tok.end
        head = lines[r1 - 1][:c1]
        tail = lines[r2 - 1][c2:]
        lines[r1 - 1:r2] = [head + new + tail]
    return ''.join(lines)


def kept(before, after):
    """Что из обязательного к сохранению потерялось."""
    was = sorted(KEEP.findall(before))
    now = sorted(KEEP.findall(after))
    return [x for x in was if was.count(x) > now.count(x)]


def translate(tok):
    """Один токен -> английский. Возвращает None, если модель не справилась."""
    body = tok.string
    out = llm.chat(PROMPT, body)
    if not out:
        return None
    out = out.strip('\n')
    if body.startswith('#') and not out.lstrip().startswith('#'):
        return None
    if body.lstrip()[:3] in ('"""', "'''"):
        q = body.lstrip()[:3]
        if not (out.lstrip().startswith(q) and out.rstrip().endswith(q)):
            return None
    if len(body.splitlines()) != len(out.splitlines()):
        return None
    if CYR.search(out):
        return None
    return out


def do(path, apply):
    src = path.read_text(encoding='utf-8')
    try:
        todo, toks = chunks(src)
    except tokenize.TokenError as e:
        return f'{path}: не разбирается: {e}', 0
    if not todo:
        return None, 0

    edits, bad = [], []
    for tok in todo:
        new = translate(tok)
        if new is None:
            bad.append(f'строка {tok.start[0]}: модель не справилась')
            continue
        lost = kept(tok.string, new)
        if lost:
            bad.append(f'строка {tok.start[0]}: потеряно дословное {lost[:4]}')
            continue
        edits.append((tok, new))

    if bad:
        return f'{path.name}: ' + '; '.join(bad[:3]), len(todo)

    out = replace(src, edits)
    # ⚠️ Главное доказательство: скелет обязан совпасть. Если модель тронула код --
    # изменится тип или число токенов, и это видно здесь, а не в ревью на 300 строк.
    try:
        new_toks = list(tokenize.generate_tokens(io.StringIO(out).readline))
    except tokenize.TokenError as e:
        return f'{path.name}: после правки не разбирается: {e}', len(todo)
    if skeleton(toks) != skeleton(new_toks):
        return f'{path.name}: СКЕЛЕТ РАЗОШЁЛСЯ — тронут код, файл не пишется', len(todo)
    try:
        compile(out, str(path), 'exec')
    except SyntaxError as e:
        return f'{path.name}: не компилируется: {e}', len(todo)

    if apply:
        path.write_text(out, encoding='utf-8')
    return None, len(todo)


def main(paths, apply):
    total, failed = 0, []
    for p in paths:
        err, n = do(p, apply)
        if n:
            mark = '❌' if err else ('записан' if apply else 'готов')
            print(f'  {p.relative_to(ROOT)!s:32} блоков {n:4d}  {mark}', flush=True)
        if err:
            print(f'      {err}', flush=True)
            failed.append(p)
        total += n
    print(f'\nблоков всего: {total}, файлов с отказом: {len(failed)}')
    if not apply:
        print('НЕ ЗАПИСАНО. Применить: tools/comments_en.py --apply')
    return 1 if failed else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ps = [pathlib.Path(x).resolve() for x in a.paths] or sorted(
        p for d in ('tools', 'emu') for p in (ROOT / d).glob('*.py'))
    sys.exit(main(ps, a.apply))
