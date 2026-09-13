#!/usr/bin/env python3
"""Где внутри HDD-образа начинается раздел с файлами.

⚠️ Раньше во всех инструментах стояло `offset=71680` — смещение раздела ИМЕННО НАШЕГО
образа. Для нас это работало, а на чужом HDI раздел начинается в другом месте: mtools не
находит там файловой системы, патч не применяет НИ ОДНОГО файла и делает это молча —
«применено 0, предупреждений 0» выглядит как успех. Патч едет к людям с их собственными
образами, поэтому смещение нужно искать, а не помнить.

Ищем загрузочный сектор FAT по BPB: строка типа ФС на +0x36 плюс осмысленные поля.
Шаг 512 б: раздел всегда выровнен по сектору.

⚠️ Подпись 0x55AA НЕ проверяем. У нашего же образа её на +510 нет вовсе (там нули), потому
что сектор на PC-98 — 1024 байта, и признак «конец сектора» стоит в другом месте. Первая
детектора требовала подпись и не нашла раздел ни в одном из трёх наших образов — то есть
проверка была строже действительности.
"""
import pathlib

STEP = 512
LIMIT = 16 << 20          # раздел с игрой лежит в начале диска; дальше не ищем
FSTYPE = (b'FAT12   ', b'FAT16   ')


def find_offset(img, limit=LIMIT):
    """Смещение первого раздела FAT в байтах, или None."""
    p = pathlib.Path(img)
    with p.open('rb') as f:
        blob = f.read(min(limit, p.stat().st_size))
    for off in range(0, len(blob) - STEP, STEP):
        sec = blob[off:off + STEP]
        if sec[0x36:0x3e] not in FSTYPE:
            continue
        bps = int.from_bytes(sec[11:13], 'little')
        spc, nfat = sec[13], sec[16]
        root = int.from_bytes(sec[17:19], 'little')
        if bps in (512, 1024, 2048) and spc and not (spc & (spc - 1)) \
                and nfat in (1, 2) and 0 < root <= 4096:
            return off
    return None


def mtoolsrc(img, path, drive='z'):
    """Написать конфиг mtools для образа. Возвращает найденное смещение."""
    off = find_offset(img)
    if off is None:
        raise SystemExit(f'в образе {img} не найден раздел FAT — это точно образ игры?')
    pathlib.Path(path).write_text(f'drive {drive}: file="{img}" offset={off}\n')
    return off


if __name__ == '__main__':
    import sys
    for a in sys.argv[1:]:
        print(f'{a}: {find_offset(a)}')
