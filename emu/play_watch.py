#!/usr/bin/env python3
"""Watch a running play cockpit and say only what is wrong.

    emu/.venv/bin/python emu/play_watch.py [port] [--every 5] [--summary 300]

Polls /state.json and the two processes. Prints a line when something is off:
  * the emulator is not `running` (booting, crashed, stopped) or was restarted;
  * at x1, unpaused, the core is not at its own 56.4 Hz;
  * the audio queue drops blocks;
  * the cockpit or the emulator grows in memory;
  * the cockpit stops answering.
and a one-line summary every `--summary` seconds, so silence means "fine", not "dead".
"""
import argparse
import json
import pathlib
import time
import urllib.request


def rss_kb(pid):
    try:
        for line in pathlib.Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('port', nargs='?', type=int, default=8778)
    ap.add_argument('--every', type=float, default=5.0)
    ap.add_argument('--summary', type=float, default=300.0)
    a = ap.parse_args()
    url = f'http://127.0.0.1:{a.port}/state.json'
    pidfile = pathlib.Path(__file__).resolve().parent / 'play.pid'
    last_summary = time.time()
    first_rss = {}
    restarts = None
    dropped = None
    fps_seen, bad_fps = [], 0
    down_since = None
    while True:
        now = time.time()
        stamp = time.strftime('%H:%M:%S')
        try:
            d = json.loads(urllib.request.urlopen(url, timeout=5).read())
            if down_since:
                print(f'{stamp} ✅ cockpit answers again after {now - down_since:.0f} s', flush=True)
                down_since = None
        except OSError as e:
            if not down_since:
                down_since = now
                print(f'{stamp} ❌ cockpit does not answer: {e}', flush=True)
            time.sleep(a.every)
            continue
        e = d.get('emu', {})
        if e.get('state') != 'running':
            print(f'{stamp} ⚠️ emulator state: {e.get("state")}', flush=True)
        if restarts is not None and e.get('restarts', 0) > restarts:
            print(f'{stamp} ❌ emulator restarted (total {e["restarts"]}); events: '
                  f'{[x["msg"] for x in d.get("events", [])[-2:]]}', flush=True)
        restarts = e.get('restarts', 0)
        if dropped is not None and (e.get('dropped') or 0) > dropped:
            print(f'{stamp} ⚠️ audio dropped {e["dropped"] - dropped} blocks '
                  f'(queue {e.get("queued")})', flush=True)
        dropped = e.get('dropped') or 0
        fps = e.get('fps')
        if fps and e.get('speed') == 1 and not e.get('turbo') and not e.get('paused'):
            fps_seen.append(fps)
            if abs(fps - 56.4) > 1.5:
                bad_fps += 1
                print(f'{stamp} ⚠️ x1 pace off: {fps} fps', flush=True)
        for name, pid in (('cockpit', _pid(pidfile)), ('emulator', e.get('pid'))):
            if not pid:
                continue
            r = rss_kb(pid)
            if r is None:
                continue
            key = (name, pid)
            first_rss.setdefault(key, r)
            if r - first_rss[key] > 200_000:
                print(f'{stamp} ⚠️ {name} memory grew {first_rss[key] // 1024} -> {r // 1024} MB', flush=True)
                first_rss[key] = r
        if now - last_summary >= a.summary:
            last_summary = now
            span = f'{min(fps_seen):.1f}..{max(fps_seen):.1f}' if fps_seen else 'n/a'
            # ⚠️ Forget processes that are gone (a restarted cockpit or emulator): keeping them
            # printed a ghost "cockpit ? MB" in every summary.
            live_rss = {k: rss_kb(k[1]) for k in first_rss}
            for k, r in live_rss.items():
                if r is None:
                    first_rss.pop(k, None)
            mem = ', '.join(f'{k[0]} {r // 1024} MB' for k, r in live_rss.items() if r is not None)
            t = d.get('tele', {})
            print(f'{stamp} · {e.get("state")} · x1 fps {span} ({bad_fps} off) · restarts {restarts} · '
                  f'dropped {dropped} · {mem} · peers {e.get("peers")} · {t.get("scene")}', flush=True)
            fps_seen, bad_fps = [], 0
        time.sleep(a.every)


def _pid(pidfile):
    try:
        return int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return None


if __name__ == '__main__':
    main()
