#!/usr/bin/env python3
"""Where the file partition starts inside an HDD image.

⚠️ Every tool used to hard-code `offset=71680` -- the partition offset of OUR image specifically.
It worked for us, but on someone else's HDI the partition starts elsewhere: mtools finds no
filesystem there, and the patch applies NOT A SINGLE file and does it silently --
"applied 0, warnings 0" looks like success. The patch goes out to people with their own
images, so the offset has to be searched for, not remembered.

We look for the FAT boot sector by its BPB: the filesystem type string at +0x36 plus sane fields.
Step 512 b: a partition is always sector-aligned.

⚠️ We do NOT check the 0x55AA signature. Our own image has none at +510 (zeros there), because
a PC-98 sector is 1024 bytes and the "end of sector" mark sits elsewhere. The first version of
the detector required the signature and found the partition in none of our three images -- the
check was stricter than reality.
"""
import pathlib

STEP = 512
LIMIT = 16 << 20          # the game partition is at the beginning of the disk; we don't search further
FSTYPE = (b'FAT12   ', b'FAT16   ')


def find_offset(img, limit=LIMIT):
    """Offset of the first FAT region in bytes, or None."""
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
    """Write an mtools config for the image. Returns the found offset."""
    off = find_offset(img)
    if off is None:
        raise SystemExit(f'no FAT partition found in image {img} — is this really a game image?')
    pathlib.Path(path).write_text(f'drive {drive}: file="{img}" offset={off}\n')
    return off


if __name__ == '__main__':
    import sys
    for a in sys.argv[1:]:
        print(f'{a}: {find_offset(a)}')
