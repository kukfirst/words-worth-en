#!/usr/bin/env python3
"""Build a playable image: original HDI + translation patch -> new file.

    tools/build_play.py [source.hdi] [output.hdi] [--fresh]

By default `game/WordsWorth.hdi` -> `game/WordsWorth_play.hdi` -- the very image
a human plays on (`emu/agent.py` in "self" mode, `WW_DISK`).

The source is NEVER touched: we work on a copy and rename it to the output file only after
verification. If it aborts mid-way -- the previous playable image stays intact.

## Save data survives a rebuild

The patch rewrites `FLAG0..FLAG4` -- five save slots. Not by accident: they hold hero names
that the engine substitutes into dialogue (`tools/savenames.py`). But taking a slot from the patch
as-is means killing player progress on every rebuild, and that's the whole point:
found a defect -- I fix it -- you continue from the same place.

So slots take a special path: they are EXTRACTED from the previous playable image and
placed into the new one as-is, with only the two Latin name fields inside them rewritten.
`--fresh` -- start with clean slots (progress will be lost).

⚠️ A clean slot is taken from the ORIGINAL (`FLAG1..FLAG4` there are byte-identical -- this
is an untouched template), NOT from the patch. In the patch, slot 0 carries someone else's save: it
came with the image itself from the collection (68 b difference from empty) -- someone played before us. No point
handing that to the player as "ロード1".

⚠️ Cannot rebuild an image that is open in a running emulator -- the script will refuse.
"""
import functools, hashlib, json, pathlib, shutil, subprocess, sys, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hdimage
import gates
from savenames import latinise, is_latin, NAME_SLOTS

# unbuffered print: otherwise the parent's lines leak after apply_patch output
print = functools.partial(print, flush=True)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
SLOTS = [f'FLAG{i}' for i in range(5)]


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def mtenv(img):
    d = pathlib.Path(tempfile.mkdtemp(prefix='wwmt.'))
    cfg = d / 'mtoolsrc'
    hdimage.mtoolsrc(img, cfg)
    return os.environ | {'MTOOLSRC': str(cfg), 'MTOOLS_SKIP_CHECK': '1'}, d


def read_slots(img):
    """Extract five save slots from the image: name -> bytes."""
    env, d = mtenv(img)
    got = {}
    try:
        for name in SLOTS:
            f = d / name
            subprocess.run(['mcopy', '-o', f'z:/WW/{name}', str(f)], env=env, capture_output=True)
            if f.is_file():
                got[name] = f.read_bytes()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return got


def empty_slot(img):
    """Untouched slot template from the image.

    In the original, FLAG1..FLAG4 are byte-identical -- that is what "no saves" means.
    If they ever diverge, the image is not what it claims to be, and staying silent about it is not an option.
    """
    got = read_slots(img)
    blanks = {got[n] for n in SLOTS[1:] if n in got}
    if len(blanks) != 1:
        sys.exit(f'in {img.name} slots FLAG1..FLAG4 are not identical -- cannot tell which is empty')
    return blanks.pop()


def write_slots(img, slots):
    env, d = mtenv(img)
    try:
        for name, blob in slots.items():
            f = d / name
            f.write_bytes(blob)
            subprocess.run(['mcopy', '-o', str(f), f'z:/WW/{name}'], env=env,
                           check=True, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def extract_ww(img, dest):
    """WW directory from the image -- for acceptance of what was produced."""
    dest.mkdir(parents=True, exist_ok=True)
    env, d = mtenv(img)
    try:
        subprocess.run(['mcopy', '-s', '-n', '-o', 'z:/WW/*', str(dest)],
                       env=env, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return dest


def holders(path):
    """Who holds the file open -- (pid, process name)."""
    out = []
    for pd in pathlib.Path('/proc').iterdir():
        if not pd.name.isdigit():
            continue
        try:
            for fd in (pd / 'fd').iterdir():
                if fd.resolve() == path:
                    out.append((pd.name, (pd / 'comm').read_text().strip()))
                    break
        except (PermissionError, OSError):
            continue
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    fresh = '--fresh' in sys.argv[1:]
    src = pathlib.Path(args[0] if len(args) > 0 else gates.BASE).resolve()
    out = pathlib.Path(args[1] if len(args) > 1 else ROOT / 'game/WordsWorth_play.hdi').resolve()
    cfgf = DIST / 'patch.json'

    if not src.is_file():
        sys.exit(f'no source image {src}')
    if not cfgf.is_file():
        sys.exit(f'no {cfgf} -- run tools/make_patch.py first')
    if src == out:
        sys.exit('source and output are the same file; the original must stay untouched')
    busy = holders(out)
    if busy:
        sys.exit('image is open: ' + ', '.join(f'{c} (pid {p})' for p, c in busy) +
                 ' -- close the emulator, .hdi cannot be rewritten under it')
    cfg = json.loads(cfgf.read_text())

    print(f'source {src.name} ({src.stat().st_size} b)')
    have = md5(src)
    if have == cfg['base_md5']:
        print('  same image the patch was built against')
    else:
        print(f'  ⚠️ md5 does not match the reference ({have[:8]} vs {cfg["base_md5"][:8]}) --'
              ' going by files instead, they will settle it')

    # --- save slots: we decide ONCE whose progress goes into the image --------------
    # Either yours from the previous image or an empty template. There is no third option (someone else's save from a patch).
    if out.is_file() and not fresh:
        slots = read_slots(out)
        empty = empty_slot(src)
        played = [n for n, b in slots.items() if latinise(b)[0] != latinise(empty)[0]]
        print(f'save from {out.name}: slots {len(slots)}, with progress '
              f'{", ".join(played) if played else "none"}')
    else:
        slots = {n: empty_slot(src) for n in SLOTS}
        print('clean slots' + (' (--fresh, progress not carried over)' if fresh else
                                ', this is the first build'))

    tmp = out.with_suffix(out.suffix + '.tmp')
    shutil.copyfile(src, tmp)
    try:
        subprocess.run([sys.executable, str(ROOT / 'tools/apply_patch.py'),
                        str(tmp), str(DIST)])

        # Slots go ON TOP OF the patch and we edit ONLY the names in them -- progress is not ours.
        renamed = []
        fixed = {}
        for name, blob in slots.items():
            fixed[name], changed = latinise(blob)
            if changed:
                renamed.append(name)
        write_slots(tmp, fixed)
        print(f'slots written; names fixed to Latin in: '
              f'{", ".join(renamed) or "already were"} ({", ".join(NAME_SLOTS.values())})')

        # --- acceptance: verify EVERY file ----------------------------------------------
        d = pathlib.Path(tempfile.mkdtemp(prefix='wwplay.'))
        got = extract_ww(tmp, d / 'ww')
        bad = []
        for e in cfg['entries']:
            f = got / e['name']
            if not f.is_file():
                bad.append((e['name'], 'not in image'))
            elif e['name'] in SLOTS:
                pass                      # slots are checked below, they have their own metric
            elif md5(f) != e['md5_after']:
                bad.append((e['name'], 'wrong content'))
        # slot -- this is progress, it is OBLIGATED to differ from the reference patch. The measure is different:
        # arrived byte-for-byte as deposited, and the names in it are Latin.
        for name, blob in fixed.items():
            cur = (got / name).read_bytes()
            if cur != blob:
                bad.append((name, 'slot arrived wrong'))
            elif not is_latin(cur):
                bad.append((name, 'names still in katakana'))
        shutil.rmtree(d, ignore_errors=True)
        if bad:
            print(f'\n❌ check failed: {len(bad)} of {len(cfg["entries"])}')
            for n, why in bad[:15]:
                print(f'   {n}: {why}')
            tmp.unlink(missing_ok=True)
            return 1
        tmp.replace(out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    print(f'\n✅ all {len(cfg["entries"])} files present and matching'
          + ', slots in place')
    print(f'play: {out}')
    return 0


# ⚠️ Build -- ONLY when run as a file. Previously `sys.exit(main())` was at module level,
# and a plain `import build_play` (even for a single `holders()`) silently rebuilt the image, in
# which person is currently playing. Caught in the act.
if __name__ == '__main__':
    sys.exit(main())
