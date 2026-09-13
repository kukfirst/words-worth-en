#!/usr/bin/env python3
"""Добить формы, оставшиеся японскими, тем же конвейером -- по остатку ПО ВСЕЙ ИГРЕ.

Зачем отдельный инструмент. `gates.gate_charset` судит БАТЧ: если модель вернула японский,
батч отклоняется и после трёх попыток форма остаётся как была. Это правильно (лучше японская
строка, чем выдумка), но никто никогда не спрашивал игру целиком: «а что осталось?». Замер
2026-09-13 -- осталось три формы, все три в H-сценах, и это единственный японский текст,
который увидит игрок.

⚠️ Кана в `NAME.MES` НЕ переводится: это ряды экранной клавиатуры ввода имени, а не текст.
⚠️ Полноширинные пробелы (\\u3000) -- раскладка, а не текст.

    tools/finish_ja.py            # показать остаток
    tools/finish_ja.py --apply    # перевести и записать
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from strings import escape, forms, patch, split_translation, unescape       # noqa: E402

# ⚠️ `translate.py` разбирает аргументы НА УРОВНЕ МОДУЛЯ, а не под `if __name__`. Импорт
# отсюда уводит ЕГО argparse в наши ключи, и `--apply` падает как «unrecognized argument»
# от чужого разбора. Прячем свои аргументы на время импорта.
_argv, sys.argv = sys.argv, sys.argv[:1]
import translate                                                           # noqa: E402
sys.argv = _argv

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'
SKIP_FILES = {'NAME.MES'}            # экранная клавиатура -- кана по делу


def real_japanese(t):
    return any(ord(c) > 126 for c in t) and not all(c in '　 \n' for c in t)


def remaining():
    out = []
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt') or p.name[:-4] in SKIP_FILES:
            continue
        src = p.read_text(encoding='utf-8')
        for f in forms(src):
            if real_japanese(unescape(f['ja'])):
                out.append((p, f))
    return out


def main(apply):
    rows = remaining()
    print(f'форм с японским текстом: {len(rows)}')
    for p, f in rows:
        print(f'  {p.name[:-8]:12} {unescape(f["ja"])[:60]}')
    if not rows or not apply:
        return
    glossary, locked = translate.load_glossary()
    by_file = {}
    for p, f in rows:
        by_file.setdefault(p, []).append(f)
    for p, fs in by_file.items():
        src = p.read_text(encoding='utf-8')
        ja = [unescape(f['ja']) for f in fs]
        en, why, stats = translate.translate_batch(ja, glossary, locked,
                                                   story='', tail=[], width=46)
        if en is None:
            print(f'  ❌ {p.name[:-8]}: гейты отклонили перевод -- {str(why)[:160]}')
            continue
        edits = []
        for f, e in zip(fs, en):
            pieces = split_translation(e, len(f['slots']), len(f['ins']),
                                       f.get('lead', 0), f.get('trail', 0))
            if pieces is None:
                print(f'  ❌ {p.name[:-8]}: перевод не раскладывается по слотам формы')
                continue
            for (x, y), piece in zip(f['slots'], pieces):
                edits.append((x, y, escape(piece)))
            print(f'  ✅ {p.name[:-8]}: {e[:70]}')
        if edits:
            p.write_text(patch(src, edits), encoding='utf-8')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    main(ap.parse_args().apply)
