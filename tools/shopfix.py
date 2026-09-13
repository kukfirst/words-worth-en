#!/usr/bin/env python3
"""Реплики магазинов: убрать добивку по числу и собрать вопрос о покупке заново.

## Два дефекта, оба видны игроку в лавке

**1. Добивка по числу неизвестной ширины.** `render.pad_breaks` дотягивает строку пробелами
до края окна, считая `(number …)` шириной шесть знаков. На экране число бывает любым: при
`260` строка выходит на три знака короче, движок дотягивает её следующим словом и рвёт по
краю окна. Кадр `emu/replay/3850_return_key.png`:

    [Item Shop Owner]: All together that's 260 gold, ya  kno
    w...

Дыра посреди строки -- это и есть добивка. Починить добивку нельзя: ширина числа известна
только в игре. Поэтому реплики укорочены так, чтобы при ПЯТИЗНАЧНОМ числе перенос не
требовался вовсе -- тогда добивки нет и рвать нечего. Порог -- `render.LINE` (55).

**2. Вопрос о покупке собран из независимо переведённых половин.** Открывающая кавычка в
одной форме, закрывающая -- в восьми ветках `(if (== V n) …)`, хвост -- в девятой. Половины
переводились порознь, поэтому кавычка открывалась `'`, а закрывалась `"`, порядок слов
рассыпался, а в SHP_5I в слот предмета заехал кусок вопроса:

    [Item Shop Owner]: ' all Healing Herbs'  will you buy?
    [Item Shop Owner]: ' Gold Bar", will you buy max\n?

Собрано заново по одной схеме на все шесть лавок: `<Кто>: Buy 'Предмет'?` и
`<Кто>: Buy all 'Предметы'?`.

⚠️ Меняется ТОЛЬКО содержимое строк -- ни одной инструкции не добавлено и не убрано,
поэтому структурный гейт (`gates.gate_compile_and_structure`) видит прежний скелет.

    tools/shopfix.py --check    # показать, что будет заменено
    tools/shopfix.py --apply    # записать
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'

# ---------------------------------------------------------------- денежные реплики
# (файл, старое, новое, сколько раз ожидается)
MONEY = [
    ('KANKIN', '" gold total... Come back  when you find more."',
     '" gold in all. Come back."', 2),
    ('SHP_0A', '" gold... good price if you ask  me."',
     '" gold... a good price, eh?"', 1),
    ('SHP_0A', '" gold... a bit steep if you ask me."',
     '" gold... a bit steep, eh?"', 1),
    ('SHP_0B', '" gold. A good     deal, right?"', '" gold. Good deal?"', 1),
    ('SHP_2A', '" gold... a good    deal, right?"', '" gold. Fair deal?"', 1),
    ('SHP_2A', '" gold... a good deal,  right?"', '" gold... good deal?"', 1),
    ('SHP_2A', '" gold... Pretty cheap, huh."', '" gold... Cheap, huh?"', 1),
    ('SHP_3I', '" gold total,        though..."', '" gold, though..."', 4),
    ('SHP_4I', '"[Item Shop Owner]: All together that\'s "',
     '"[Item Shop Owner]: That\'s "', 4),
    ('SHP_4I', '" gold, ya  know..."', '" gold, ya know..."', 4),
    ('SHP_4S', '" gold... cheap,       right?"', '" gold... cheap, eh?"', 1),
    ('SHP_5B', '" gold for that one."', '" gold for it."', 1),
]

# ---------------------------------------------------------------- вопрос о покупке
# Одна схема на все лавки: <Кто>: Buy 'Предмет'?  /  <Кто>: Buy all 'Предметы'?
ITEMS = {
    0: "'Healing Herb'",         1: "all 'Healing Herbs'",
    2: "'Stamina Herb'",         3: "all 'Stamina Herbs'",
    4: "'Gold Bar'",             5: "all 'Gold Bars'",
    6: "'Ascension Stone'",      7: "all 'Ascension Stones'",
}
BUY = {
    'SHP_0I': ('"[Epo]: \'"', '"[Epo]: Buy "', '" Buy it?"', ITEMS),
    'SHP_2I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '" will you\\nbuy?"', ITEMS),
    'SHP_3I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '" for me?"', ITEMS),
    'SHP_4I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '" will you buy?"', ITEMS),
    'SHP_4S': ('"[Dark Merchant]: \'"', '"[Dark Merchant]: Buy "',
               '" you\'re buying, I\\nsee."',
               ITEMS | {4: "'Magic Boots'", 5: "all 'Magic Boots'"}),
    'SHP_5I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '"\\n?"', ITEMS),
}
# Старые тексты веток -- как они лежат сейчас, по файлам. Ключ -- номер ветки.
OLD_BRANCH = {
    'SHP_0I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '"Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" all Gold Bars\\""',
               6: '" Ascension Stone\\""', 7: '" all Ascension Stones\\""'},
    'SHP_2I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" all Gold Bars\\""',
               6: '" Ascension Stone\\""', 7: '" all Ascension Stones\\""'},
    'SHP_3I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" as many Gold Bars\'"',
               6: '" Ascension Stone\\""', 7: '" as many Ascension Stones\'"'},
    'SHP_4I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" as many Gold Bars as you can\\ncarry\'"',
               6: '" Ascension Stone\\""',
               7: '" as many Ascension Stones as you\\ncan carry\'"'},
    'SHP_4S': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Magic Boots\'"', 5: '"All Magic Boots\'"',
               6: '" Ascension Stone\\""', 7: '"All Ascension Stone\'"'},
    'SHP_5I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" Gold Bar\\", will you buy max"',
               6: '" Ascension Stone\\""',
               7: '" Ascension Stone\\", will you buy max"'},
}

# ---------------------------------------------------------------- вопрос о продаже
# Мелочь того же класса: ведущий пробел внутри кавычек и пробел перед закрывающей.
SELL = [
    ('SHP_0I', '(text " \' - will you sell it?")', '(text "\' - will you sell it?")', 1),
    ('SHP_2I', '(if (== V 3) (<> (text " Ascension Stone")))',
     '(if (== V 3) (<> (text "Ascension Stone")))', 1),
]

# ---------------------------------------------------------------- «применил предмет» в бою
# Тот же класс: <имя> + связка + <предмет> + хвост. Связка `は、` осталась ЯПОНСКОЙ -- она
# одна из немногих форм, где японский знак не виден гейту charset, потому что форма короткая
# и состоит из служебных знаков. На экране выходило `Astralは、Gold Bar tried using it!!`.
# Переставлено в порядок, который даёт связка: `Astral tried using 'Gold Bar'!!`.
USED = [
    ('SENTO04', '(text "は、")', '(text " tried using \'")', 1),
    ('SENTO04', '(text " tried using it!!")', '(text "\'!!")', 1),
    ('SENTO0G', '(text "は、")', '(text " tried using \'")', 1),
    ('SENTO0G', '(text " tried using it!!")', '(text "\'!!")', 1),
    ('SENTO0J', '(text "は、")', '(text " tried using \'")', 1),
    ('SENTO0J', '(text " tried using!")', '(text "\'!")', 1),
]


def edits():
    """[(файл, старое, новое, сколько)] -- все замены одним списком.

    ⚠️ Открывающая форма `(text "[Item Shop Owner]: '")` есть в файле ДВАЖДЫ -- в вопросе о
    покупке и в вопросе о продаже. Поэтому она заменяется ВМЕСТЕ с первой веткой: такая пара
    в файле одна.
    """
    out = list(MONEY)
    for fn, (lead_old, lead_new, tail_old, items) in BUY.items():
        pairs = OLD_BRANCH[fn]
        for v, old in pairs.items():
            branch_old = f'(if (== V {v}) (<> (text {old})))'
            branch_new = f'(if (== V {v}) (<> (text "{items[v]}")))'
            if v == 0:
                out.append((fn, f'(text {lead_old})\n         {branch_old}',
                            f'(text {lead_new})\n         {branch_new}', 1))
            else:
                out.append((fn, branch_old, branch_new, 1))
        out.append((fn, f'(text {tail_old})', '(text "?")', 1))
    return out + SELL + USED


def main(apply):
    bad = 0
    by_file = {}
    for fn, old, new, want in edits():
        by_file.setdefault(fn, []).append((old, new, want))
    for fn, rows in sorted(by_file.items()):
        p = EN / f'{fn}.MES.rkt'
        src = p.read_text(encoding='utf-8')
        done = 0
        for old, new, want in rows:
            got = src.count(old)
            if got != want:
                # ⚠️ ИДЕМПОТЕНТНОСТЬ. Старого текста нет, а новый на месте -- правка уже
                # применена, и это не провал: иначе повторный прогон (а он неизбежен, правки
                # идут волнами) объявляет сломанным то, что сам же и починил.
                if got == 0 and src.count(new) >= want:
                    done += 1
                    continue
                print(f'  ❌ {fn}: {old[:56]} -- найдено {got}, ждали {want}')
                bad += 1
                continue
            src = src.replace(old, new)
        if apply and not bad:
            p.write_text(src, encoding='utf-8')
        note = f'замен {len(rows) - done}' + (f', уже было {done}' if done else '')
        print(f'  {"✅" if not bad else "⏭️"} {fn}: {note}')
    if bad:
        sys.exit(f'\n❌ несовпадений: {bad} -- ничего не записано')
    print(f'\n{"записано" if apply else "НЕ ЗАПИСАНО (--apply)"}: файлов {len(by_file)}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    main(ap.parse_args().apply)
