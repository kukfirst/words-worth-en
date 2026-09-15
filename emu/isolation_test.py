#!/usr/bin/env python3
"""The agent must not share a system or save directory with any other emulator script.

The np2kai core WRITES np2kai.cfg into its system directory. Two cores on one directory is how
the human's unsaved game died on 2026-09-15: a probe started next to the live agent, rewrote
the shared config, and the agent segfaulted inside np2kai_libretro.so 15 s later.

The fix is structural -- the agent defaults to its own copy -- and this test keeps it that way:
it reads the DEFAULT directory of every script in emu/ from the source (nothing is started)
and fails if the agent's default is the same as anyone else's.

    emu/.venv/bin/python emu/isolation_test.py              # the real agent.py
    emu/.venv/bin/python emu/isolation_test.py some.py      # check another file as the agent
"""
import pathlib
import re
import sys

EMU = pathlib.Path(__file__).resolve().parent

# `system=HERE / "system"`, `system=EMU/'system'`, `... or (EMU / 'system'))`, `_own_copy(..., "system-agent", ...)`
DEFAULT = {
    'system': re.compile(r'''(?:WW_SYSTEM['"]\)\s*or\s*\(?|system\s*=\s*|_own_copy\(\s*["']WW_SYSTEM["'],\s*)'''
                         r'''(?:\w+\s*/\s*)?["']([\w-]+)["']'''),
    'save': re.compile(r'''(?:WW_SAVE['"]\)\s*or\s*\(?|save\s*=\s*|_own_copy\(\s*["']WW_SAVE["'],\s*)'''
                       r'''(?:\w+\s*/\s*)?["']([\w-]+)["']'''),
}


def defaults(path):
    src = pathlib.Path(path).read_text(encoding='utf-8')
    return {kind: set(rx.findall(src)) for kind, rx in DEFAULT.items()}


def main(agent_path):
    agent = defaults(agent_path)
    bad = []
    for kind in DEFAULT:
        if not agent[kind]:
            bad.append(f'{kind}: cannot find the agent default in {agent_path}')
            continue
        for p in sorted(EMU.glob('*.py')):
            if p.name in ('agent.py', 'isolation_test.py') or p.resolve() == pathlib.Path(agent_path).resolve():
                continue
            shared = agent[kind] & defaults(p)[kind]
            if shared:
                bad.append(f'{kind}: agent and {p.name} both default to {sorted(shared)}')
    for b in bad:
        print('  ❌', b)
    print('❌ the live agent shares a directory with another emulator' if bad else
          f'✅ the agent has its own directories: system={sorted(agent["system"])} '
          f'save={sorted(agent["save"])}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else EMU / 'agent.py'))
