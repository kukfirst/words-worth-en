#!/usr/bin/env python3
"""End-to-end check of the play cockpit: a real core, a real server, a WebSocket client.

    emu/.venv/bin/python emu/play_live_test.py [image.hdi]

Runs on a PRIVATE copy of the image (default: the playable one), its own system directory,
states directory and port, so it never touches a running game. What it proves, in order:

  1. the page is served and the socket delivers frames (640x400 RGB565, magic WWF1);
  2. the core runs at its own 56.4 Hz at x1, not faster, not slower;
  3. game memory is read in place: telemetry sees the title menu;
  4. the absolute mouse puts the engine's cursor exactly where it was aimed;
  5. a click on "Load 1" as fast as a browser sends it loads the game (regression: 3-frame
     clicks were ignored);
  6. a snapshot saves to a slot and loads back;
  7. a killed emulator is reported and restarted by the cockpit;
  8. stopping the cockpit leaves no emulator behind.

Exit code 0 only if every step passed. ~1 minute.
"""
import base64
import json
import os
import pathlib
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
PY = HERE / '.venv/bin/python'
LOAD1 = (372, 82)          # "Load 1" on the title menu, screen pixels


class Client:
    """The browser's side of the socket: masked frames out, unmasked frames in."""

    def __init__(self, port):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n'
                        f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                        f'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            head += self.s.recv(1)
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise RuntimeError(head.decode(errors='replace'))
        # ⚠️ Not makefile(): a buffered reader is unusable after one socket timeout
        # ("cannot read from timed out object"), and timeouts are how pump() waits.
        self.buf = bytearray()
        self.frames = 0
        self.frame_shape = None
        self.last_frame = None
        self.last = {}

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = struct.pack('!BB', 0x81, 0x80 | n) if n < 126 else struct.pack('!BBH', 0x81, 0x80 | 126, n)
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def _frame(self):
        """(opcode, payload) of one complete frame in the buffer, or None if not all there."""
        b = self.buf
        if len(b) < 2:
            return None
        n, at = b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, at = struct.unpack_from('!H', b, 2)[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, at = struct.unpack_from('!Q', b, 2)[0], 10
        if len(b) < at + n:
            return None
        op, data = b[0] & 0x0F, bytes(b[at:at + n])
        del b[:at + n]
        return op, data

    def pump(self, seconds, until=None):
        """Read messages for up to `seconds`; stop early when until(msg) is true."""
        end = time.time() + seconds
        while time.time() < end:
            got = self._frame()
            if got is None:
                self.s.settimeout(max(0.05, end - time.time()))
                try:
                    chunk = self.s.recv(1 << 20)
                except (socket.timeout, TimeoutError):
                    break
                if not chunk:
                    raise EOFError('cockpit closed the socket')
                self.buf += chunk
                continue
            op, data = got
            if op == 2:
                self.frames += 1
                magic, w, h, pitch = struct.unpack_from('<4sHHH', data, 0)
                self.frame_shape = (magic, w, h, len(data) - 16)
                self.last_frame = data          # kept so a failing check can show the screen
                continue
            if op != 1:
                continue
            m = json.loads(data)
            self.last[m.get('t')] = m
            if m.get('t') == 'hello':
                for k in ('emu', 'tele'):
                    if m.get(k):
                        self.last[k] = m[k]
            if until and until(m):
                return m
        return None


def main():
    image = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / 'game/WordsWorth_play.hdi')
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ww-play-test.'))
    disk = tmp / 'test.hdi'
    shutil.copy2(image, disk)
    shutil.copytree(HERE / 'system', tmp / 'system')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    env = {**os.environ, 'WW_DISK': str(disk), 'WW_SYSTEM': str(tmp / 'system'),
           'WW_SAVE': str(tmp / 'save'), 'WW_PLAY_STATES': str(tmp / 'states'),
           'WW_PLAY_PORT': str(port)}
    # its own settings file would be nice, but the cockpit keeps one; sound is forced off here
    proc = subprocess.Popen([str(PY), str(HERE / 'play.py')], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    results = []

    def check(name, ok, detail=''):
        results.append(ok)
        print(f'  {"✅" if ok else "❌"} {name}' + (f' -- {detail}' if detail else ''), flush=True)

    child = None
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/state.json', timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        page = urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5).read()
        c = Client(port)
        c.send({'c': 'sound', 'on': False})
        c.pump(40, until=lambda m: m.get('t') == 'emu' and m.get('state') == 'running' and m.get('mem'))
        c.pump(2)
        check('page served and frames arrive', b'play.js' in page and c.frames > 0
              and c.frame_shape == (b'WWF1', 640, 400, 640 * 400 * 2), f'{c.frames} frames, {c.frame_shape and c.frame_shape[1:3]}')
        emu = c.last.get('emu', {})
        child = emu.get('pid')
        fps = []
        end = time.time() + 4
        while time.time() < end:
            m = c.pump(1, until=lambda m: m.get('t') == 'emu')
            if m and m.get('fps'):
                fps.append(m['fps'])
        steady = fps[1:] or fps
        check('x1 runs at the core\'s 56.4 Hz', bool(steady) and all(abs(f - 56.4) < 1.5 for f in steady), f'{steady}')
        c.pump(3, until=lambda m: m.get('t') == 'tele' and m.get('scene'))
        tele = c.last.get('tele', {})
        check('telemetry reads the title menu from memory', tele.get('scene') == 'START1.MES', f'scene={tele.get("scene")}')
        ok_aim = True
        for x, y in ((100, 50), (320, 200), LOAD1):
            c.send({'c': 'aim', 'x': x, 'y': y})
            m = c.pump(2, until=lambda m: m.get('t') == 'tele' and m.get('cursor') == [x, y])
            ok_aim &= m is not None
        check('absolute mouse reaches its aim exactly', ok_aim, f'last cursor {c.last.get("tele", {}).get("cursor")}')
        # A real browser click: the pointer lands and clicks at once, so aim, down and up arrive
        # in one batch while the cursor is still far away (regression: the menu ignored it).
        c.send({'c': 'aim', 'x': 600, 'y': 300})
        c.pump(2, until=lambda m: m.get('t') == 'tele' and m.get('cursor') == [600, 300])
        c.send({'c': 'aim', 'x': LOAD1[0], 'y': LOAD1[1]})
        c.send({'c': 'btn', 'b': 'left', 'd': 1})
        c.send({'c': 'btn', 'b': 'left', 'd': 0})
        m = c.pump(12, until=lambda m: m.get('t') == 'tele' and m.get('in_play'))
        check('a browser-fast click on "Load 1" loads the game', m is not None,
              f'scene={c.last.get("tele", {}).get("scene")}')
        c.send({'c': 'slot_save', 'name': 'slot1'})
        m = c.pump(10, until=lambda m: m.get('t') == 'slots' and any(s['name'] == 'slot1' and s['used'] for s in m['slots']))
        c.send({'c': 'slot_load', 'name': 'slot1'})
        m2 = c.pump(10, until=lambda m: m.get('t') == 'event' and 'slot1' in m.get('msg', ''))
        check('snapshot saves to a slot and loads back', m is not None and m2 is not None
              and m2.get('level') != 'error', m2 and m2.get('msg'))
        if child:
            os.kill(child, signal.SIGKILL)
        crashed = c.pump(10, until=lambda m: m.get('t') == 'emu' and m.get('state') == 'crashed')
        back = c.pump(40, until=lambda m: m.get('t') == 'emu' and m.get('state') == 'running'
                      and m.get('pid') != child and m.get('mem'))
        check('a killed emulator is reported and restarted', crashed is not None and back is not None,
              f'new pid {back and back.get("pid")}')
        child = (back or {}).get('pid') or child
    except Exception as e:
        check('no exception during the run', False, repr(e))
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)
        gone = child is None or not pathlib.Path(f'/proc/{child}').exists()
        check('stopping the cockpit leaves no emulator behind', gone)
        err = proc.stderr.read().decode(errors='replace')
        if err.strip():
            print('--- cockpit stderr ---\n' + err[-2000:])
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(results)
    print(f'{"✅" if all(results) else "❌"} play cockpit end to end: {passed}/{len(results)}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
