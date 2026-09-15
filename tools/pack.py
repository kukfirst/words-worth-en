#!/usr/bin/env python3
"""Собрать раздаваемый зип: патч + readme, с проверкой ПУТЁМ ИГРОКА.

⚠️ Зип v1.0 собирался руками, и это было видно: в readme стояла база, с которой рабочий
образ к тому времени разошёлся на 21 байт (§33). Здесь все числа берутся из самих файлов,
а не переписываются.

Что делает:

1. кладёт `xdelta` и `readme.txt` в `dist/release/words-worth-en-v<версия>.zip`;
2. ⚠️ РАСПАКОВЫВАЕТ зип в пустой каталог и применяет патч xdelta к СВЕЖЕЙ копии базы --
   ровно так, как это сделает человек. Компилируется -- не значит раздаётся: проверять надо
   то, что лежит в зипе, а не то, что лежит в `dist/`;
3. сверяет CRC32 получившегося образа с тем, что обещано в readme.

    tools/pack.py 1.1            # собрать и проверить
    tools/pack.py 1.1 --dry      # только показать
"""
import argparse
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gates                                                        # noqa: E402

REL = ROOT / 'dist/release'


def crc(p):
    return format(zlib.crc32(pathlib.Path(p).read_bytes()) & 0xffffffff, '08X')


def sha1(p):
    return hashlib.sha1(pathlib.Path(p).read_bytes()).hexdigest()


def main(ver, dry):
    base = gates.BASE
    img = ROOT / 'game/WordsWorth_qa.hdi'
    for p in (base, img):
        if not p.exists():
            sys.exit(f'нет {p}')

    delta = REL / f'words-worth-en-v{ver.replace(".", "-")}.xdelta'
    zip_p = REL / f'words-worth-en-v{ver.replace(".", "-")}.zip'
    readme = REL / 'readme.txt'

    print(f'база   : {base.name}  CRC32 {crc(base)}')
    print(f'образ  : {img.name}  CRC32 {crc(img)}')

    # ⚠️ readme обязан описывать ТО ЖЕ, что в зипе. Сверяем, а не надеемся.
    txt = readme.read_text(encoding='utf-8') if readme.exists() else ''
    for want, what in ((crc(base), 'базы'), (crc(img), 'результата')):
        if want not in txt:
            print(f'   ⚠️ CRC32 {what} {want} НЕ упомянут в readme.txt')

    if dry:
        print('\nНЕ ЗАПИСАНО. Применить: без --dry')
        return 0

    REL.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['xdelta3', '-e', '-9', '-f', '-s', str(base), str(img), str(delta)],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'xdelta3 не собрал дельту: {r.stderr[:200]}')
    with zipfile.ZipFile(zip_p, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(delta, delta.name)
        if readme.exists():
            z.write(readme, 'readme.txt')
    print(f'\nзип: {zip_p}  {zip_p.stat().st_size / 1024:.0f} КБ')

    # --- приёмка путём игрока -------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        with zipfile.ZipFile(zip_p) as z:
            z.extractall(d)
        copy = d / 'WordsWorth.hdi'
        shutil.copyfile(base, copy)
        out = d / 'out.hdi'
        r = subprocess.run(['xdelta3', '-d', '-f', '-s', str(copy),
                            str(d / delta.name), str(out)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'❌ патч из зипа НЕ применился: {r.stderr[:200]}')
        got = crc(out)
        ok = got == crc(img)
        print(f'путь игрока: распаковал, применил -> CRC32 {got}  '
              f'{"✅ совпало" if ok else "❌ РАЗОШЛОСЬ с " + crc(img)}')
        if not ok:
            return 1
        print(f'             SHA-1 {sha1(out)}')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('version')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    sys.exit(main(a.version, a.dry))
