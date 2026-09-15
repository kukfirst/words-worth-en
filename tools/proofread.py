#!/usr/bin/env python3
"""Вычитка перевода: пройтись по английскому тексту ещё раз, уже без японского перед глазами.

Первый проход переводил японское в английское и судился по верности оригиналу. Вычитка --
другая задача: японского здесь нет вовсе, и спрашивается только одно -- «так говорят
по-английски?». Буквальный порядок слов, сбитый артикль, мёртвая идиома, разнобой в
обращениях -- всё то, на что справедливо указал корректор gbatemp.

## Куда пишется результат

НЕ в `en/*.rkt`. Правки ложатся обратно в `text/*.json`, то есть в тот же плоский вид, что
уходит человеку, и дальше идут общим путём: `tools/import_text.py` со всеми его гейтами и
правилом «не прошла одна строка -- не пишется ничего». Двух дорог в скрипты быть не должно.

Значит вычитку можно посмотреть глазами до того, как она куда-то поедет:

    tools/proofread.py                  # прогнать и записать предложения в text/
    tools/proofread.py FLOOR02.MES      # один файл
    git diff --stat                     # (text/ под gitignore -- смотреть через --review)
    tools/proofread.py --review         # показать все принятые правки построчно
    tools/import_text.py                # что из этого пройдёт гейты
    tools/import_text.py --apply        # записать в скрипты

## Что отвергается на месте, не доходя до import_text

| проверка | почему здесь, а не потом |
|---|---|
| маркеры `{0}` | дешевле не предлагать, чем потом разбирать отказ на 200 строк |
| кодировка | то же |
| раскладка в окне | вычитка норовит удлинить фразу; окно 56 знаков этого не прощает |
| длина | реплика не должна занять БОЛЬШЕ экранных строк, чем занимала: файл растёт |
| «правка ради правки» | замена, не меняющая смысла (регистр, точка), только тратит размер |

⚠️ Модель здесь ничего не решает. Она предлагает; принимает код.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from render import screen, parts_of, flaws                          # noqa: E402
import gates                                                        # noqa: E402
import llm                                                          # noqa: E402

TEXT = ROOT / 'text'
MARK = re.compile(r'\{(\d+)\}')
# Подпись говорящего в начале реплики: `[Kaiser]: `, `[{0}]: `. Меняться не имеет права.
SPEAKER = re.compile(r'^\s*\[[^\]]*\]:\s*')
BATCH = 25

PROMPT = """You are proofreading the English script of a 1993 Japanese dungeon RPG.
The Japanese is gone; judge only whether each line reads as natural English.

Fix: literal word order, wrong or missing articles, dead-literal idioms, wrong register,
clumsy phrasing, inconsistent address between characters.

Do NOT fix: spelling of proper nouns, American vs British spelling, punctuation style,
"..." ellipses, or anything that is merely a matter of taste. Do not censor. Do not soften.
The game is adult and blunt; keep it blunt.

A very common defect in this script: a noun is missing after a group name, because Japanese
does not need one. "took out some Light Clan" must become "took out some Light Clan members".
Adding the missing word is REQUIRED even though it makes the line longer.

HARD RULES:
- {0} and {1} are runtime name insertions. Keep them exactly, same count, same order.
- A line may start with a speaker tag like "[Kaiser]: " or "[{0}]: ". KEEP IT VERBATIM,
  including the brackets, the colon and the space. Never drop it, never add one.
- Do not change leading or trailing spaces.
- Prefer the shortest correct fix, but DO lengthen a line when the fix needs a word. Never
  add so much that the line would need an extra display line.
- Keep the line breaks (\\n) where they are unless the line genuinely needs rewrapping.
- ASCII only.

Input is a numbered list, one reply per numbered item. A reply's own line break inside the
message window is written as the two characters \\n, never as a real newline; keep it that way.

Output ONLY a JSON object mapping the number of each line YOU ACTUALLY CHANGED to its
corrected text. A line that is already fine must NOT appear. Most lines are fine, so most
answers are small; {} is a perfectly good answer. No commentary."""


def wrong(old, new, c0):
    """Причина отказать предложению. Пусто -- значит берём.

    ⚠️ Запрета «не длиннее ни на знак» здесь НЕТ, и это исправление. Он стоял в первой
    версии и отверг бы настоящую починку: `took out some Light Clan` -> `took out a few
    Light Clan guys` -- фраза, которой не хватало существительного, лечится только
    прибавлением слова. Ограничение на самом деле не на реплику, а на ФАЙЛ: он не должен
    уехать за `gates.MES_MAX`. Поэтому здесь -- число экранных строк (окно есть окно), а
    рост считается бюджетом на файл (`budget`), и последнее слово всё равно за размерным
    гейтом `import_text.py`, который теперь отказывает ДО записи.
    """
    if new == old:
        return 'без изменений'
    if MARK.findall(old) != MARK.findall(new):
        return f'маркеры {MARK.findall(old)} -> {MARK.findall(new)}'
    # ⚠️ Маркер В НАЧАЛЕ (или в конце) формы -- не украшение, а место склейки: движок
    # печатает имя ДО текста формы, и перенести `{0}` внутрь фразы физически нельзя.
    # Замер: вычитка предложила `{0} to. I'll write.` -> `I'll write in {0}.`, и это
    # единственная правка из 368, которую отверг импорт (`не раскладывается по слотам`).
    # Дешевле не предлагать, чем ронять весь импорт: он всё-или-ничего.
    for end in (True, False):
        o, n = (old.rstrip(), new.rstrip()) if end else (old.lstrip(), new.lstrip())
        om, nm = (MARK.search(o[-4:]), MARK.search(n[-4:])) if end else \
                 (MARK.match(o), MARK.match(n))
        if bool(om) != bool(nm):
            return f'маркер на {"конце" if end else "краю"} формы съехал внутрь фразы'
    # ⚠️ Подпись говорящего `[Kaiser]: ` -- обычный текст, не маркер, и первая версия её не
    # стерегла. Замер 2026-09-15 на FLOOR05: из 16 принятых правок ПЯТЬ срезали подпись
    # («[Kaiser]: Hey...» -> «Hey...»), а ещё одна дописала пробел в начало. Такое доехало
    # бы до игры и молча убрало бы имена из реплик.
    o, n = SPEAKER.match(old), SPEAKER.match(new)
    if (o.group(0) if o else None) != (n.group(0) if n else None):
        return f'подпись говорящего {o.group(0) if o else "нет"!r} -> {n.group(0) if n else "нет"!r}'
    if (len(old) - len(old.lstrip())) != (len(new) - len(new.lstrip())) or \
            old.rstrip() != old and new.rstrip() == new:
        return 'изменился отступ или хвостовой пробел'
    if gates.gate_charset([new]):
        return 'знаки вне кодировки игры'
    sc_old = screen(parts_of(old), w=None, col0=c0)
    sc_new = screen(parts_of(new), w=None, col0=c0)
    if len(sc_new) > len(sc_old):
        return f'занимает {len(sc_new)} строк вместо {len(sc_old)}'
    why = flaws(sc_new, col0=c0)
    if why:
        return 'раскладка: ' + ', '.join(sorted(set(why)))
    if new.strip().lower() == old.strip().lower():
        return 'правка ради правки'
    return ''


def budget(name):
    """Сколько знаков файлу можно прибавить, не подойдя к порогу.

    Считать точно нельзя: `(dict-build)` строит словарь сжатия ИЗ текста, и связь «знак ->
    байт» не линейна (правка букв в 40 репликах меняла 27,8 % байтов файла). Поэтому берём
    заведомо скупо: треть запаса в байтах. Настоящая проверка -- пересборка в `import_text`.
    """
    mes = ROOT / 'en' / f'{name}.rkt.mes'
    if not mes.exists():
        return 0
    return max(0, (gates.MES_MAX - mes.stat().st_size) // 3)


def pass_file(jf, limit):
    rows = json.loads(jf.read_text(encoding='utf-8'))
    left = budget(jf.name[:-5])
    took, refused = 0, {}
    for i in range(0, len(rows), BATCH):
        if limit and took >= limit:
            break
        batch = rows[i:i + BATCH]
        # ⚠️ Перенос ВНУТРИ реплики кодируется двумя знаками: иначе нумерованный список
        # разъезжается на её же переносе, и модель отвечает не про ту строку.
        ask = '\n'.join(f'{n}. {r["en"]}'.replace('\n', '\\n') for n, r in enumerate(batch))
        try:
            out = json.loads(llm.chat(PROMPT, ask))
        except Exception as e:
            refused.setdefault(f'модель: {type(e).__name__}', 0)
            refused[f'модель: {type(e).__name__}'] += 1
            continue
        if not isinstance(out, dict):
            refused['модель вернула не объект'] = refused.get('модель вернула не объект', 0) + 1
            continue
        for k, new in out.items():
            if not isinstance(new, str) or not str(k).strip('.').isdigit():
                continue
            n = int(str(k).strip('.'))
            if not 0 <= n < len(batch):
                refused['номер вне батча'] = refused.get('номер вне батча', 0) + 1
                continue
            r = batch[n]
            new = new.replace('\\n', '\n')      # обратно из двухзначной записи переноса
            # колонка старта восстанавливается из выгрузки: первая экранная строка
            # короче текста ровно на отступ, который движок уже напечатал
            c0 = max(0, len(r['screen'][0]) - len(r['en'].split('\n')[0])) if r['screen'] else 0
            why = wrong(r['en'], new, c0)
            if why:
                refused[why.split(':')[0]] = refused.get(why.split(':')[0], 0) + 1
                continue
            grow = len(new) - len(r['en'])
            if grow > left:
                refused['не хватает бюджета файла'] = refused.get('не хватает бюджета файла', 0) + 1
                continue
            left -= max(0, grow)
            r['en'] = new
            r['screen'] = screen(parts_of(new), w=None, col0=c0)
            took += 1
    if took:
        jf.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    return took, refused


def main(names, limit):
    if not TEXT.is_dir():
        sys.exit(f'нет {TEXT} -- сперва tools/export_text.py')
    files = [TEXT / f'{n}.json' for n in names] if names else sorted(TEXT.glob('*.MES.json'))
    total, all_ref = 0, {}
    for jf in files:
        if not jf.exists():
            print(f'  {jf.name}: нет такого')
            continue
        took, ref = pass_file(jf, limit)
        total += took
        for k, v in ref.items():
            all_ref[k] = all_ref.get(k, 0) + v
        print(f'  {jf.name[:-5]:16} принято {took:4d}', flush=True)
    print(f'\nпринято правок: {total}')
    if all_ref:
        print('отказано:')
        for k, v in sorted(all_ref.items(), key=lambda x: -x[1]):
            print(f'   {v:5d}  {k}')
    print('\nдальше: tools/import_text.py (посмотреть), потом --apply')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--limit', type=int, default=0, help='правок на файл, 0 -- без предела')
    a = ap.parse_args()
    main(a.names, a.limit)
