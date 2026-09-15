#!/usr/bin/env python3
"""The entire chain from text to a playable image -- in a SINGLE command and in the correct order.

Why. Seven steps, the order is strict, and skipping any one doesn't look like an error but
like "fixed one thing -- broke another". This has already happened:

⚠️ `make_patch.py` compares the original against the **QA image**, not against `en/`. Building a patch without
rebuilding the QA image beforehand -- means shipping yesterday's text, and the "patch and
en/ match" check goes red only AFTER the build.
⚠️ `terms.py` renames by file lines, while `relayout.py` moves line-breaks inside
lines. Running them in reverse order -- means not finding a phrase split across a line-break.
⚠️ `recompile.py` must run AFTER all text edits: it both rebuilds `.mes` and runs the
structural gate.

    tools/release.py            # full chain + quick check
    tools/release.py --probe    # plus emulator run
    tools/release.py --dry      # only show what will be done
    tools/release.py --pack 1.2 # plus zip and ready image in dist/release/ (tools/pack.py)
"""
import argparse
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable

# (title, command, "how long to wait approximately")
CHAIN = [
    ('names: one item -- one name', [PY, 'tools/terms.py', '--fix'], 'seconds'),
    ('layout: column-aware line wrapping', [PY, 'tools/relayout.py', '--apply'], 'minute'),
    ('Rebuild scripts and structural gate', [PY, 'tools/recompile.py'], '~20 minutes'),
    ('QA image from en/', [PY, 'tools/build_qa_image.py'], 'minute'),
    ('patch from QA image', [PY, 'tools/make_patch.py'], 'minute'),
    ('playable image (saves carry over)', [PY, 'tools/build_play.py'], 'minute'),
]


def run(title, cmd, dry):
    print(f'\n=== {title}\n    {" ".join(cmd)}', flush=True)
    if dry:
        return 0
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    print(f'    {"✅" if not r.returncode else "❌"} in {time.time() - t0:.0f} s', flush=True)
    return r.returncode


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='check with launch in emulator')
    ap.add_argument('--dry', action='store_true', help='only show the order')
    ap.add_argument('--pack', metavar='VER', help='build release VER after green check')
    a = ap.parse_args()

    # ⚠️ A playable image must not be rebuilt while the emulator is running: build_play.py
    # it'll reject it on its own, but finding out after twenty minutes of rebuilding -- that's frustrating.
    sys.path.insert(0, str(ROOT / 'tools'))
    import build_play
    busy = build_play.holders(ROOT / 'game/WordsWorth_play.hdi')
    if busy and not a.dry:
        sys.exit(f'image busy with processes {busy} -- stop the agent (the «⏹ process» button)')

    for title, cmd, eta in CHAIN:
        if run(f'{title}  ({eta})', cmd, a.dry):
            sys.exit(f'\n❌ broke off at step «{title}» -- cannot continue')

    check = [PY, 'tools/verify.py', '--screens'] + (['--probe'] if a.probe else [])
    if run('Check', check, a.dry):
        sys.exit('\n❌ check failed -- nothing to pack')
    # Packing used to be a separate command nobody called in sequence, and dist/ drifted:
    # a zip with a stale readme and a hand-renamed image that no step refreshed. Only after
    # a green check, so dist/ never holds something the checks rejected.
    if a.pack:
        sys.exit(run(f'packing v{a.pack}: zip, player path, image in dist/',
                     [PY, 'tools/pack.py', a.pack], a.dry))
