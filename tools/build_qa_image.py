#!/usr/bin/env python3
"""Собрать QA-образ: переведено всё, что движок переживает; остальное остаётся японским.

Зачем отдельно от `patch_hdi.sh`. Тот заливает ВСЕ переведённые .mes, включая те, что
за порогом буфера, — и игра умирает при заходе в такую локацию, а до того ещё и молча
затирает блок характеристик игрока (обмер: `emu/size_ladder.py`, `emu/HANDOFF.md`).
Для задачи «сделать игру максимально проходимой для тестирования» это худший из вариантов:
теряется не одна комната, а весь прогон.

Здесь решение принимает ОДИН источник истины — `gates.gate_size`. Файл под порогом едет в
образ переведённым, файл за порогом не едет вовсе, и на образе остаётся оригинал из
чистого `game/WordsWorth.hdi`. Никаких списков имён в коде: поменяется порог или ужмётся
файл — сборка сама это подхватит.

⚠️ Японская комната — это не «текст проверен». `state.identify()` показывает такие сцены
с суффиксом `:ja`, так что отчёт агента их отличает.
"""
import pathlib, shutil, subprocess, sys, os, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gates                                                        # noqa: E402
import hdimage                                                      # noqa: E402

# ⚠️ Имена героев живут в СОХРАНЕНИИ, а не в скриптах. Правило и кодировка -- в
# `tools/savenames.py`: один источник, потому что `build_play.py` переписывает те же
# два поля в СВОЁМ слоте игрока, чтобы пересборка не убивала прогресс.
from savenames import latinise, NAME_SLOTS                                   # noqa: E402


def latinise_names(dst, offset, mtoolsrc_env):
    """Переписать имена в пяти слотах сохранения латиницей."""
    done = []
    for slot in range(5):
        name = f"FLAG{slot}"
        tmp = pathlib.Path(tempfile.mkstemp(prefix=name)[1])
        r = subprocess.run(['mcopy', '-o', f'z:/WW/{name}', str(tmp)],
                           env=mtoolsrc_env, capture_output=True)
        if r.returncode:
            tmp.unlink(missing_ok=True)
            continue
        blob, changed = latinise(tmp.read_bytes())
        if changed:
            tmp.write_bytes(blob)
            subprocess.run(['mcopy', '-o', str(tmp), f'z:/WW/{name}'],
                           env=mtoolsrc_env, check=True, capture_output=True)
            done.append(name)
        tmp.unlink(missing_ok=True)
    return done


SRC = ROOT / 'game/WordsWorth.hdi'          # нетронутый оригинал
# Куда собирать. По умолчанию -- образ, который грузит эмулятор; аргументом можно
# собрать рядом, не трогая работающий агент (переписывать .hdi под открытым
# эмулятором нельзя).
DST = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'game/WordsWorth_qa.hdi'

if not SRC.is_file():
    sys.exit(f'нет {SRC}')
shutil.copyfile(SRC, DST)

rc = os.environ.copy()
cfg = pathlib.Path(tempfile.mkstemp(suffix='.mtoolsrc')[1])
# ⚠️ Смещение раздела ИЩЕТСЯ, а не помнится -- как в apply_patch.py: 71680 это адрес
# раздела нашего образа, у чужого он другой, и mtools там молча не находит ничего.
OFFSET = hdimage.mtoolsrc(DST, cfg)
rc['MTOOLSRC'] = str(cfg); rc['MTOOLS_SKIP_CHECK'] = '1'

sent, held = [], []
for f in sorted((ROOT / 'en').glob('*.MES.rkt.mes')):
    name = f.name[:-len('.rkt.mes')]
    over = gates.gate_size(f)
    if over:
        held.append((name, f.stat().st_size))
        continue
    subprocess.run(['mcopy', '-o', str(f), f'z:/WW/{name}'], env=rc, check=True,
                   capture_output=True)
    sent.append(name)
# Картинки с японскими надписями: перерисовываются каждый раз ЗАНОВО из нетронутого
# оригинала (tools/titlemenu.py), а не берутся готовыми -- источник правды один, инструмент.
import titlemenu                                                    # noqa: E402
art = [titlemenu.build()]
for f in art:
    subprocess.run(['mcopy', '-o', str(f), f'z:/WW/{f.name}'], env=rc, check=True,
                   capture_output=True)
renamed = latinise_names(DST, OFFSET, rc)
cfg.unlink(missing_ok=True)

for name, size in sorted(held, key=lambda x: -x[1]):
    print(f'  🇯🇵 оставлен японским: {name:16s} {size:6d} б  '
          f'(за порогом на {size - gates.MES_MAX} б)')
print(f'\n{DST}')
print(f'картинок перерисовано: {len(art)} ({", ".join(f.name for f in art)})')
print(f'переведённых залито: {len(sent)} · оставлено японскими: {len(held)} · '
      f'порог {gates.MES_MAX} б')
print(f'имена латиницей в сохранениях: {", ".join(renamed) or "уже были"} '
      f'({", ".join(NAME_SLOTS.values())})')
