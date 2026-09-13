#!/usr/bin/env python3
"""Вся цепочка от текста до играбельного образа -- ОДНОЙ командой и в правильном порядке.

Зачем. Шагов семь, порядок между ними жёсткий, и пропуск любого выглядит не как ошибка, а
как «починили одно -- отвалилось другое». Так уже было:

⚠️ `make_patch.py` сравнивает оригинал с **QA-образом**, а не с `en/`. Собрать патч, не
пересобрав перед этим QA-образ, -- значит выпустить вчерашний текст, и проверка «патч и
en/ сходятся» краснеет уже ПОСЛЕ сборки.
⚠️ `terms.py` переименовывает по строкам файла, а `relayout.py` двигает переносы внутри
строк. Запустить их в обратном порядке -- значит не найти фразу, разорванную переносом.
⚠️ `recompile.py` должен идти ПОСЛЕ всех правок текста: он и пересобирает `.mes`, и гоняет
структурный гейт.

    tools/release.py            # вся цепочка + быстрая проверка
    tools/release.py --probe    # плюс запуск в эмуляторе
    tools/release.py --dry      # только показать, что будет сделано
"""
import argparse
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable

# (заголовок, команда, «сколько примерно ждать»)
CHAIN = [
    ('имена: один предмет -- одно имя', [PY, 'tools/terms.py', '--fix'], 'секунды'),
    ('раскладка: переносы с учётом колонки', [PY, 'tools/relayout.py', '--apply'], 'минута'),
    ('пересборка скриптов и структурный гейт', [PY, 'tools/recompile.py'], '~20 минут'),
    ('QA-образ из en/', [PY, 'tools/build_qa_image.py'], 'минута'),
    ('патч из QA-образа', [PY, 'tools/make_patch.py'], 'минута'),
    ('играбельный образ (сохранения переносятся)', [PY, 'tools/build_play.py'], 'минута'),
]


def run(title, cmd, dry):
    print(f'\n=== {title}\n    {" ".join(cmd)}', flush=True)
    if dry:
        return 0
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    print(f'    {"✅" if not r.returncode else "❌"} за {time.time() - t0:.0f} с', flush=True)
    return r.returncode


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='проверка с запуском в эмуляторе')
    ap.add_argument('--dry', action='store_true', help='только показать порядок')
    a = ap.parse_args()

    # ⚠️ Играбельный образ нельзя пересобирать под работающим эмулятором: build_play.py
    # откажется сам, но узнать об этом через двадцать минут пересборки -- обидно.
    sys.path.insert(0, str(ROOT / 'tools'))
    import build_play
    busy = build_play.holders(ROOT / 'game/WordsWorth_play.hdi')
    if busy and not a.dry:
        sys.exit(f'образ занят процессами {busy} -- остановите агента (кнопка «⏹ процесс»)')

    for title, cmd, eta in CHAIN:
        if run(f'{title}  ({eta})', cmd, a.dry):
            sys.exit(f'\n❌ оборвалось на шаге «{title}» -- дальше идти нельзя')

    check = [PY, 'tools/verify.py', '--screens'] + (['--probe'] if a.probe else [])
    sys.exit(run('проверка', check, a.dry))
