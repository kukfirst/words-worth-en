#!/usr/bin/env python3
"""Assemble the release zip: patch + readme, verified THE PLAYER'S WAY.

⚠️ The v1.0 zip was assembled by hand, and it showed: the readme named a base that the
working image had by then drifted from by 21 bytes (§33). Here every number is read off
the files themselves, never retyped.

What it does:

1. puts `xdelta` and `readme.txt` into `dist/release/words-worth-en-v<version>.zip`;
2. ⚠️ UNPACKS the zip into an empty directory and applies the xdelta patch to a FRESH copy
   of the base -- exactly as a human would. Compiling isn't the same as shipping: what needs
   checking is what's in the zip, not what's in `dist/`;
3. verifies the CRC32 of the resulting image against what the readme promises.

    tools/pack.py 1.1            # build and verify
    tools/pack.py 1.1 --dry      # show only
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


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def stale_sums(txt, base, img):
    """Sums the readme must state but does not. Empty means it describes these exact files."""
    want = {'base CRC32': crc(base), 'base MD5': md5(base),
            'result CRC32': crc(img), 'result MD5': md5(img)}
    return [f'{k} {v}' for k, v in want.items() if v.lower() not in txt.lower()]


def secondary(delta):
    """Secondary compressor of a VCDIFF file, or None. Read from the bytes, not from printhdr:
    printhdr names a compressor ("lzma") even when the header has no VCD_SECONDARY bit."""
    head = pathlib.Path(delta).read_bytes()[:6]
    if head[:4] != b'\xd6\xc3\xc4\x00':
        return 'not a VCDIFF file'
    if not head[4] & 0x01:                      # Hdr_Indicator bit 0: VCD_DECOMPRESS
        return None
    return {1: 'djw', 2: 'lzma', 16: 'fgk'}.get(head[5], f'id {head[5]}')


def main(ver, dry):
    base = gates.BASE
    img = ROOT / 'game/WordsWorth_qa.hdi'
    for p in (base, img):
        if not p.exists():
            sys.exit(f'missing {p}')

    delta = REL / f'words-worth-en-v{ver.replace(".", "-")}.xdelta'
    zip_p = REL / f'words-worth-en-v{ver.replace(".", "-")}.zip'
    readme = REL / 'readme.txt'

    print(f'base   : {base.name}  CRC32 {crc(base)}')
    print(f'image  : {img.name}  CRC32 {crc(img)}')

    # ⚠️ readme must describe THE SAME thing as in the zip. Verify, don't hope.
    # This used to be a printed warning, and v1.1 shipped with a readme promising CRC32 DF4478CF
    # for an image that came out 646EA627: the readme was fixed 12 s after the zip was built
    # and never re-packed. A player who checks the sum sees a mismatch and calls it an error.
    # So a missing sum is a refusal, and the readme is checked again INSIDE the finished zip.
    txt = readme.read_text(encoding='utf-8') if readme.exists() else ''
    missing = stale_sums(txt, base, img)
    if missing:
        sys.exit('❌ readme.txt does not carry: ' + ', '.join(missing))

    if dry:
        print('\nNOT SAVED. Apply: without --dry')
        return 0

    REL.mkdir(parents=True, exist_ok=True)
    # ⚠️ `-S none`: NO secondary compression. Our xdelta3 is built with liblzma and picks lzma by
    # default; any xdelta built without liblzma stops on it ("unavailable secondary compressor").
    # The official Windows xdelta3.exe 3.0.11/3.1.0 DO carry lzma (measured in a real Windows 11
    # guest), so Windows itself is not the problem. The problem is Rom Patcher JS -- the web
    # patcher people use on macOS and phones -- which refuses ANY secondary compressor, djw
    # included ("not implemented: secondary decompressor"); measured 2026-09-15 on our own v1.0
    # (djw) and v1.1 (lzma) patches. Plain VCDIFF costs 75 KB and decodes everywhere we tried.
    r = subprocess.run(['xdelta3', '-e', '-9', '-S', 'none', '-f', '-s', str(base), str(img),
                        str(delta)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'xdelta3 failed to build the delta: {r.stderr[:200]}')
    with zipfile.ZipFile(zip_p, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(delta, delta.name)
        if readme.exists():
            z.write(readme, 'readme.txt')
    print(f'\nzip: {zip_p}  {zip_p.stat().st_size / 1024:.0f} KB')

    # --- acceptance via player -------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        with zipfile.ZipFile(zip_p) as z:
            z.extractall(d)
        # what the player reads, not what sits next to the zip
        missing = stale_sums((d / 'readme.txt').read_text(encoding='utf-8'), base, img)
        if missing:
            print('❌ readme INSIDE the zip does not carry: ' + ', '.join(missing))
            return 1
        comp = secondary(d / delta.name)
        if comp:
            print(f'❌ patch uses secondary compression ({comp}): '
                  'Rom Patcher JS and old xdelta builds cannot decode it')
            return 1
        print('readme in zip: base and result sums are in place; no secondary compression')
        copy = d / 'WordsWorth.hdi'
        shutil.copyfile(base, copy)
        out = d / 'out.hdi'
        r = subprocess.run(['xdelta3', '-d', '-f', '-s', str(copy),
                            str(d / delta.name), str(out)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'❌ patch from the zip did NOT apply: {r.stderr[:200]}')
        got = crc(out)
        ok = got == crc(img)
        print(f'player path: unpacked, applied -> CRC32 {got}  '
              f'{"✅ matched" if ok else "❌ FAILED with" + crc(img)}')
        if not ok:
            return 1
        print(f'             SHA-1 {sha1(out)}')
        # The ready image goes next to the zip, and it is THIS one -- rebuilt from the zip on a
        # fresh base -- not game/WordsWorth_play.hdi, which carries somebody's save slots, nor a
        # copy renamed by hand: dist/ kept a stale `Words Worth (English).hdi` that no step
        # refreshed and that looked newer than it was.
        ready = REL / image_name(ver)
        shutil.copyfile(out, ready)
        if crc(ready) != got:
            print(f'❌ copy in dist/ does not match: {crc(ready)}')
            return 1
        print(f'image  : {ready.name}  CRC32 {got}')
    return 0


def image_name(ver):
    """TOSEC naming: the original dump's fields stay, the translation is the [tr] dump flag,
    the release version goes in the free-form [more info] field at the end.
    https://www.tosecdev.org/tosec-naming-convention"""
    return f'Words Worth (1993)(Elf)(JP)[tr en kukfirst][v{ver}].hdi'


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('version')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    sys.exit(main(a.version, a.dry))
