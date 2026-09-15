#!/usr/bin/env python3
"""Assemble the translation patch: per-file xdelta + config, plus a full image as a fallback.

⚠️ Why PER-FILE rather than a delta of the whole image. This is what the PC-98 community does:
the Pachy98 patcher (46 OkuMen) patches individual files inside the disk via NDC + xdelta
driven by a JSON config, and that's the whole point -- "target patch necessary files
correctly while ignoring differences on the rest of the disk, making it possible for
end-users to have different dumps or even make their own dump of a disk". A whole-image delta
only applies to a byte-identical dump of the same one we have; a per-file patch applies to any.
Verified on our case: 84 files change and 11 are added (split satellites), which is
1.7 MB against a 20 MB image.

⚠️ IPS is fundamentally unsuitable: 16 MB ceiling. BPS is meant for cartridges. For disk
images the standard is xdelta3 (VCDIFF).

    tools/make_patch.py            # assemble into dist/
"""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hdimage
import gates

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = gates.BASE                            # untouched original (gates.BASE)
# Converted image. You can specify a different one as an argument -- e.g. one built next to it,
# while the worker is busy with a running agent.
DST = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'game/WordsWorth_qa.hdi'
OUT = ROOT / 'dist'


def mount(img):
    d = pathlib.Path(tempfile.mkdtemp(prefix='wwpatch.'))
    cfg = d / 'mtoolsrc'
    hdimage.mtoolsrc(img, cfg)
    env = os.environ | {'MTOOLSRC': str(cfg), 'MTOOLS_SKIP_CHECK': '1'}
    files = d / 'files'
    files.mkdir()
    subprocess.run(['mcopy', '-o', '-s', 'z:/WW/*', str(files)], env=env, capture_output=True)
    return d, files


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def main():
    if not shutil.which('xdelta3'):
        sys.exit('xdelta3 not found: sudo pacman -S --needed xdelta3')
    for p in (SRC, DST):
        if not p.is_file():
            sys.exit(f'missing {p}')
    _, a = mount(SRC)
    _, b = mount(DST)
    OUT.mkdir(exist_ok=True)
    patches = OUT / 'files'
    if patches.exists():
        shutil.rmtree(patches)
    patches.mkdir(parents=True)

    entries, changed, added = [], 0, 0
    for f in sorted(b.iterdir()):
        if not f.is_file():
            continue
        old = a / f.name
        if old.is_file():
            if md5(old) == md5(f):
                continue
            out = patches / (f.name + '.xdelta')
            subprocess.run(['xdelta3', '-e', '-f', '-s', str(old), str(f), str(out)],
                           check=True, capture_output=True)
            entries.append({'name': f.name, 'type': 'patch', 'patch': f'files/{out.name}',
                            'md5_before': md5(old), 'md5_after': md5(f)})
            changed += 1
        else:
            # ⚠️ Slice satellites -- NEW files that don't exist in the original. Delta from void
            # no need to do it, we keep it as is: together they are less than a hundred kilobytes.
            shutil.copyfile(f, patches / f.name)
            entries.append({'name': f.name, 'type': 'add', 'file': f'files/{f.name}',
                            'md5_after': md5(f)})
            added += 1

    (OUT / 'patch.json').write_text(json.dumps({
        'game': 'Words Worth (elf, PC-98, 1993-07-22)',
        'target': 'HDI, catalog WW',
        'base_md5': md5(SRC),
        'result_md5': md5(DST),
        'entries': entries}, ensure_ascii=False, indent=1))

    # Fallback: full image delta — only works on our dump
    full = OUT / 'WordsWorth_en_full.xdelta'
    subprocess.run(['xdelta3', '-e', '-f', '-s', str(SRC), str(DST), str(full)],
                   check=True, capture_output=True)

    size = sum(p.stat().st_size for p in patches.rglob('*'))
    print(f'files changed: {changed}, added: {added}')
    print(f'per-file patch:  {size/1024:.0f} KB  ({patches})')
    print(f'full image:      {full.stat().st_size/1024:.0f} KB  ({full.name})')
    print(f'config:          {OUT / "patch.json"}')


main()
