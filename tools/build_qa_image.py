#!/usr/bin/env python3
"""Build a QA image: everything the engine survives is translated; the rest stays Japanese.

Why separate from `patch_hdi.sh`. That script dumps ALL translated .mes files, including those
over the buffer threshold — and the game dies when entering such a location, and before that it also
silently overwrites the player stats block (measured in: `emu/size_ladder.py`, `emu/HANDOFF.md`).
For the task of "making the game as passable as possible for testing" this is the worst option:
you lose not one room but the entire run.

Here the decision is made by ONE source of truth — `gates.gate_size`. A file under the threshold
goes into the image translated, a file over the threshold goes in not at all, and the image keeps the
original from the clean base image (`gates.BASE`). No name lists in code: if the threshold changes
or a file shrinks — the build picks it up on its own.

⚠️ A Japanese room is not "text verified". `state.identify()` shows such scenes
with the `:ja` suffix, so the agent report distinguishes them.
"""
import pathlib, shutil, subprocess, sys, os, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gates                                                        # noqa: E402
import hdimage                                                      # noqa: E402

# ⚠️ Hero names live in SAVE, not in scripts. The rule and encoding -- in
# `tools/savenames.py`: single source of truth, because `build_play.py` overwrites the same ones
# Two fields in the player's OWN slot, so a rebuild doesn't kill progress.
from savenames import latinise, NAME_SLOTS                                   # noqa: E402


def latinise_names(dst, offset, mtoolsrc_env):
    """Rewrite the names in the five save slots in Latin script."""
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


SRC = gates.BASE                            # pristine original (gates.BASE)
# Where to build. By default — the image the emulator loads; as an argument you can
# build alongside, without touching the running agent (overwriting .hdi under an open
# cannot be done with an emulator).
DST = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'game/WordsWorth_qa.hdi'

if not SRC.is_file():
    sys.exit(f'missing {SRC}')
shutil.copyfile(SRC, DST)

rc = os.environ.copy()
cfg = pathlib.Path(tempfile.mkstemp(suffix='.mtoolsrc')[1])
# ⚠️ Section offset IS SEARCHED, not stored -- as in apply_patch.py: 71680 is an address
# of our image — in someone else's it's different, and mtools silently finds nothing there.
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
# Images with Japanese text: redrawn every time FROM SCRATCH from the untouched original
# of the original (tools/titlemenu.py), not taken ready-made -- the single source of truth is the tool.
import titlemenu                                                    # noqa: E402
art = [titlemenu.build()]
for f in art:
    subprocess.run(['mcopy', '-o', str(f), f'z:/WW/{f.name}'], env=rc, check=True,
                   capture_output=True)
renamed = latinise_names(DST, OFFSET, rc)
cfg.unlink(missing_ok=True)

for name, size in sorted(held, key=lambda x: -x[1]):
    print(f'  🇯🇵 left Japanese: {name:16s} {size:6d} b  '
          f'(over threshold by {size - gates.MES_MAX} b)')
print(f'\n{DST}')
print(f'images redrawn: {len(art)} ({", ".join(f.name for f in art)})')
print(f'translated copied in: {len(sent)} · left Japanese: {len(held)} · '
      f'threshold {gates.MES_MAX} b')
print(f'names romanized in saves: {", ".join(renamed) or "already existed"} '
      f'({", ".join(NAME_SLOTS.values())})')
