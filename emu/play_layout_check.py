#!/usr/bin/env python3
"""The cockpit's layout, checked by driving it: does the game picture ever move?

    python3 emu/play_layout_check.py

No emulator, no BIOS, no disk image and nothing to install: a stand-in cockpit speaks the real
protocol to the real page in a headless browser. It needs Chrome or Chromium; without one it
says so and passes, because a check that cannot run must not look like a check that failed.

WHY THIS EXISTS. The page is a flex column: the stage takes what the strips below it leave. So
anything that grew down there -- a message of four lines instead of one, the proofreading editor
opening, a refusal listing its reasons, the autobattle button widening its label and re-wrapping
the control bar -- took its height out of the PICTURE, and the game visibly jumped while being
played. Measured before the fix: at 1600x900 the picture took six different sizes across
ordinary states and its height varied by 257px; at 1280x720 it collapsed from 470px wide to
229px. None of that is visible in a screenshot of one state, which is why it survived so long.

WHAT IS ASSERTED, at five window sizes:
  * #screen has exactly ONE rect across every state the bottom strip can be in;
  * the control bar is one row, and no control is scrolled off its edge;
  * the line box is one size, and 56 columns of the game's window fit in it;
  * overlays (settings, the match list, a refusal) move nothing;
  * typing survives the window being re-read;
  * folding the panel is the ONLY thing that may change the picture, and theater mode still
    takes the strips away.
"""
import base64
import json
import http.server
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wsock  # noqa: E402

WEB = HERE / 'play_web'
PORT = int(os.environ.get('WW_LAYOUT_PORT', 8791))
CDP_PORT = int(os.environ.get('WW_LAYOUT_CDP', 9335))
# WW_LAYOUT_SIZES=1366x768 narrows the run to one window; the default is the set that matters
VIEWPORTS = [tuple(int(n) for n in s.split('x')) for s in os.environ.get(
    'WW_LAYOUT_SIZES', '1920x1080,1600x900,1366x768,1280x720,1100x800').split(',')]
BROWSERS = ('google-chrome-stable', 'google-chrome', 'chromium', 'chromium-browser', 'chrome')

FRAME_MAGIC = b'WWF1'
FRAME_HEAD = struct.Struct('<4sHHHHI')
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.css': 'text/css; charset=utf-8'}


# ----------------------------------------------------------------- the stand-in cockpit
PEERS, LOCK = [], threading.Lock()
STATE = {'emu': {'state': 'running', 'fps': 56.4, 'speed': 1, 'mem': True, 'paused': False,
                 'sound': False, 'uptime': 42, 'grind': {'on': False}, 'restarts': 0},
         'tele': {}, 'map': None, 'text': None, 'slots': [],
         'settings': {'sound': False, 'volume': 0.7}}


def hello():
    emu = dict(STATE['emu'], t='emu', settings=STATE['settings'])
    return {'t': 'hello', 'emu': emu, 'tele': STATE['tele'], 'map': STATE['map'],
            'text': STATE['text'], 'slots': STATE['slots'], 'events': [],
            'settings': STATE['settings']}


def broadcast(msg):
    s = json.dumps(msg)
    with LOCK:
        for p in list(PEERS):
            if p.alive:
                p.text(s)


def remember(msg):
    """Keep the stand-in's copy in step, so a page that reloads sees the same state."""
    t = msg.get('t')
    if t == 'text':
        STATE['text'] = msg.get('text')
    elif t == 'emu':
        STATE['emu'] = {k: v for k, v in msg.items() if k not in ('t', 'settings')}


def frames():
    """A plain grey picture, so the canvas has something and layout() runs for real."""
    payload = FRAME_HEAD.pack(FRAME_MAGIC, 640, 400, 1280, 0, 1) + struct.pack('<H', 0x4208) * 256000
    while True:
        with LOCK:
            for p in list(PEERS):
                if p.alive:
                    p.frame(payload)
        time.sleep(0.5)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        msg = json.loads(self.rfile.read(n) or b'{}')
        remember(msg)
        broadcast(msg)
        self._send(200, 'application/json', b'{"ok":true}')

    def do_GET(self):
        if self.path == '/ws':
            return self.websocket()
        if self.path == '/state.json':
            return self._send(200, 'application/json', json.dumps(hello()).encode())
        name = self.path.lstrip('/').split('?')[0] or 'index.html'
        f = (WEB / name).resolve()
        if not f.is_file() or WEB not in f.parents:
            return self._send(404, 'text/plain', b'not found')
        self._send(200, TYPES.get(f.suffix, 'application/octet-stream'), f.read_bytes())

    # ⚠️ NOT `command`: BaseHTTPRequestHandler already owns that name (it holds the HTTP method
    # as a string), so a method called that is shadowed and calling it raises TypeError.
    @staticmethod
    def on_command(cmd, peer):
        """`text_check` (while typing) and `text_edit` (on save), answered the way the real
        checker answers them: a character the game cannot draw, or a renamed speaker tag."""
        if cmd.get('c') not in ('text_edit', 'text_check'):
            return
        en = str(cmd.get('en') or '')
        problems = [f'a character the game cannot draw: {c!r}' for c in '~\\' if c in en]
        if not en.startswith('[Innkeeper]:'):
            problems.append('the [Name]: tag changed: [Innkeeper] -> it is not there any more')
        if cmd.get('c') == 'text_check':
            peer.text(json.dumps({'t': 'text_check', 'id': cmd.get('id'),
                                  'problems': problems}))
            return
        peer.text(json.dumps(
            {'t': 'text_edit', 'ok': False, 'why': 'the game would refuse this line',
             'problems': problems} if problems
            else {'t': 'text_edit', 'ok': True, 'id': cmd.get('id'), 'file': 'YADO.MES.json'}))

    def websocket(self):
        key = self.headers.get('Sec-WebSocket-Key')
        if not key:
            return self._send(400, 'text/plain', b'expected a websocket')
        self.connection.sendall(
            b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
            b'Sec-WebSocket-Accept: ' + wsock.accept_key(key).encode() + b'\r\n\r\n')
        peer = wsock.Peer(self.connection)
        with LOCK:
            PEERS.append(peer)
        peer.text(json.dumps(hello()))
        try:
            while peer.alive:
                op, data = wsock.read_message(self.rfile, on_ping=peer.pong)
                if op == wsock.CLOSE:
                    break
                if op == wsock.TEXT:
                    self.on_command(json.loads(data or b'{}'), peer)
        except (OSError, EOFError, ValueError, wsock.ProtocolError):
            pass
        finally:
            peer.close()
            with LOCK:
                if peer in PEERS:
                    PEERS.remove(peer)
        self.close_connection = True


# ----------------------------------------------------------------- the browser, over CDP
class Cdp:
    """Enough of the DevTools protocol to evaluate and to press a mouse button.

    Hand-rolled for the same reason emu/wsock.py is: the client side of a websocket is thirty
    lines, and this check must run wherever the cockpit runs -- with nothing installed.
    """

    def __init__(self, port):
        tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=10))
        page = next(t for t in tabs if t['type'] == 'page')
        url = page['webSocketDebuggerUrl'].split('://', 1)[1]
        host, path = url.split('/', 1)
        h, p = host.split(':')
        self.s = socket.create_connection((h, int(p)), timeout=30)
        k = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n'
                        f'Connection: Upgrade\r\nSec-WebSocket-Key: {k}\r\n'
                        f'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            head += self.s.recv(1)
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise RuntimeError(head.decode(errors='replace'))
        self.buf = bytearray()
        self.n = 0

    def _send(self, obj):
        data = json.dumps(obj).encode()
        mask, n = os.urandom(4), len(json.dumps(obj).encode())
        if n < 126:
            head = struct.pack('!BB', 0x81, 0x80 | n)
        elif n < 1 << 16:
            head = struct.pack('!BBH', 0x81, 0x80 | 126, n)
        else:
            head = struct.pack('!BBQ', 0x81, 0x80 | 127, n)
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def _frame(self):
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
        fin, op, data = b[0] & 0x80, b[0] & 0x0F, bytes(b[at:at + n])
        del b[:at + n]
        return fin, op, data

    def call(self, method, **params):
        self.n += 1
        self._send({'id': self.n, 'method': method, 'params': params})
        parts, end = [], time.time() + 30
        while time.time() < end:
            got = self._frame()
            if got is None:
                chunk = self.s.recv(1 << 20)
                if not chunk:
                    raise EOFError('the browser closed the socket')
                self.buf += chunk
                continue
            fin, op, data = got
            parts.append(data)
            if not fin:
                continue
            body, parts = b''.join(parts), []
            if op not in (0x1, 0x0):
                continue
            m = json.loads(body)
            if m.get('id') != self.n:
                continue
            if 'error' in m:
                raise RuntimeError(f'{method}: {m["error"]}')
            return m.get('result', {})
        raise TimeoutError(method)

    def js(self, expression):
        r = self.call('Runtime.evaluate', expression=expression, returnByValue=True,
                      awaitPromise=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(f'page threw: {r["exceptionDetails"].get("text")}')
        return r.get('result', {}).get('value')


# ----------------------------------------------------------------- what is driven, and measured
INN = "[Innkeeper]: Good morning... Clean your room before you\nleave, won't you."
SCREEN2 = ['[Innkeeper]: Good morning... Clean your room before you', "leave, won't you."]
SCREEN4 = ['[Innkeeper]: Good morning... Clean your room before you',
           "leave, won't you. The bath is down the hall and the well",
           'is out the back. Breakfast is bread and what the cook',
           'could find, which today is not very much at all.']


def cand(n, **kw):
    c = {'id': f'YADO.MES#{n}', 'file': 'YADO.MES', 'line': 71 + n, 'exact': True, 'form': INN,
         'en': INN, 'editable': True, 'edited': False, 'fresh': True, 'kind': 'src'}
    c.update(kw)
    return c


ONE_EXACT = {'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'sources': 'src',
                                   'candidates': [cand(0)], 'more': 0}}


def floor(w, h, name='FLOOR02.MES'):
    """A floor of a given shape. Floors differ wildly, and the map card must not."""
    return {'t': 'map', 'floor': name, 'facing': 0, 'here': [0, 0], 'edges': {},
            'cells': {f'{x},{y}': {'seen': True} for x in range(w) for y in range(h)}}
EMU_BASE = {'t': 'emu', 'state': 'running', 'fps': 56.4, 'speed': 1, 'mem': True, 'uptime': 99,
            'settings': {'sound': False, 'volume': 0.7}}

SCENARIOS = [
    ('empty window', {'t': 'text', 'text': None}),
    ('one line, no source', {'t': 'text', 'text': {
        'lines': ['[Innkeeper]: Good morning...', '', '', ''], 'sources': None,
        'candidates': [], 'more': 0}}),
    ('two lines, one exact: the box becomes the editor', ONE_EXACT),
    ('four lines, one exact', {'t': 'text', 'text': {
        'lines': SCREEN4, 'sources': 'src', 'candidates': [cand(0)], 'more': 0}}),
    ('four lines, six candidates', {'t': 'text', 'text': {
        'lines': SCREEN4, 'sources': 'src', 'candidates': [cand(i) for i in range(6)],
        'more': 3}}),
    ('settling', {'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'settling': True,
                                        'candidates': [], 'more': 0, 'sources': None}}),
    ('not editable, with a long reason', {'t': 'text', 'text': {
        'lines': SCREEN2 + ['', ''], 'sources': 'src', 'more': 0,
        'candidates': [cand(0, editable=False,
                            why='the source line changed after the export: re-export text/ first')]}}),
    ('back to one exact', ONE_EXACT),
    ('autobattle running', dict(EMU_BASE, grind={'on': True, 'fights': 12,
                                                 'cfg': {'hp_floor': 20}})),
    ('autobattle stopped, long reason', dict(EMU_BASE, grind={
        'on': False, 'fights': 12, 'stopped': 'a menu is holding the input at 7,3'})),
    ('no map at all', {'t': 'map', 'floor': 'YADO.MES', 'cells': {}}),
    ('a small floor', floor(3, 3)),
    ('a big floor', floor(16, 16)),
    ('a wide floor', floor(24, 6)),
    ('a tall floor', floor(5, 20)),
]

GEOM = """(() => {
  const r = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
  const bar = document.querySelector('.ctlbar');
  const need = ['screen','text-en','text-save','text-src','text-alt','text-why',
                'tc-fold','tc-hold','tc-state','btn-view','sliders','aspect','intscale',
                'mouse-mode','grind-info','text-cands','view-pop','text-pop','why-pop','netinfo'];
  const shown = document.getElementById('text-en');
  return {
    viewport: [innerWidth, innerHeight],
    screen: r(document.getElementById('screen')),
    padrow: r(document.querySelector('.padrow')), bar: r(bar), box: r(shown),
    mapcard: r(document.querySelector('.mapcard')),
    editor: !document.getElementById('text-en').readOnly,
    missing: need.filter(i => !document.getElementById(i)),
    barOverflow: bar ? [bar.scrollWidth, bar.clientWidth] : null,
    // Save must lie INSIDE the panel at every width: the header is one nowrap row, and every
    // control added to it (the hold button carries a word now) pushes the rest towards the edge
    saveInside: (() => {
      const card = document.querySelector('.textcard').getBoundingClientRect();
      const s = document.getElementById('text-save').getBoundingClientRect();
      return [Math.round(s.left - card.left), Math.round(card.right - s.right)]; })(),
    barRows: bar ? new Set([...bar.children].filter(e => e.offsetParent !== null)
        .map(e => { const b = e.getBoundingClientRect();
                    return Math.round(b.top + b.height / 2); })).size : 0,
    fits56: (() => { const s = document.createElement('span');
      s.style.cssText = 'position:absolute;visibility:hidden;white-space:pre';
      s.style.font = getComputedStyle(shown).font; s.textContent = 'M'.repeat(56);
      document.body.appendChild(s); const w = s.getBoundingClientRect().width; s.remove();
      return [Math.round(w), Math.round(shown.clientWidth) - 16]; })(),
  };
})()"""


def push(msg):
    urllib.request.urlopen(urllib.request.Request(
        f'http://127.0.0.1:{PORT}/_push', data=json.dumps(msg).encode(),
        headers={'Content-Type': 'application/json'}), timeout=10).read()


def settle(p, times=3):
    for _ in range(times):
        p.js('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
    time.sleep(0.12)


def behaviour(p, fail):
    """The promises the fixed layout makes, exercised rather than assumed."""
    push(ONE_EXACT)
    settle(p)
    base = p.js(GEOM)['screen']

    def steady(what):
        got = p.js(GEOM)['screen']
        if got != base:
            fail(f'{what}: the picture moved {base} -> {got}')

    # --- R2 and R10 of emu/PANEL_UX.md: arrival must not be a trap ----------------------
    # The game TYPES its messages out, so the window is read several times before it settles,
    # and the cockpit can only name the entry once it has. Throughout that, the thing under the
    # pointer where the box is must stay the SAME control -- otherwise a click while the line is
    # already legible lands on nothing, and it reads as "it will not let me edit it".
    under = """(() => { const w = document.querySelector('.tbox-wrap').getBoundingClientRect();
        const el = document.elementFromPoint(w.x + w.width / 2, w.y + w.height / 2);
        return el ? (el.id || el.tagName) : 'none'; })()"""
    arrival = []
    for step in ({'t': 'text', 'text': None},
                 {'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'settling': True,
                                        'candidates': [], 'more': 0, 'sources': None}},
                 ONE_EXACT):
        push(step)
        settle(p)
        arrival.append(p.js(under))
    if len(set(arrival)) > 1:
        fail(f'PANEL_UX R2 (one surface): what you would click on where the box is changes as '
             f'the line arrives: {" -> ".join(arrival)}')
    if p.js("document.getElementById('text-en').readOnly"):
        fail('PANEL_UX R10: the line settled and the box did not become editable by itself')

    # ...and the same with someone clicking into the box WHILE it is still settling, which is
    # what people actually do: that click must not freeze the line out of reach.
    push({'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'settling': True,
                                'candidates': [], 'more': 0, 'sources': None}})
    settle(p)
    p.js("document.getElementById('text-en').focus()")
    settle(p)
    push(ONE_EXACT)
    settle(p)
    if p.js("document.getElementById('text-en').readOnly"):
        fail('PANEL_UX R10: a click while the line was settling left it read-only for good')
    if not p.js("document.getElementById('app').classList.contains('held')"):
        fail('PANEL_UX R10: the early click did not count -- it asks for a second one')
    steady('a click while the line is still settling')
    p.js("document.getElementById('tc-hold').click()")
    settle(p)

    # --- R14: the same line in several places ---------------------------------------------
    # Rendered by several entries that hold the SAME text: which occurrence the game is showing
    # cannot be known and does not matter -- the correction belongs in all of them.
    same = [cand(0), dict(cand(0), id='YADO.MES#4', line=90),
            dict(cand(0), id='YADO.MES#9', line=120)]
    push({'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'sources': 'src',
                                'candidates': same, 'more': 0}})
    settle(p)
    if p.js("document.getElementById('text-en').readOnly"):
        fail('PANEL_UX R14: a line held identically in 3 places cannot be edited at all')
    if p.js("document.getElementById('text-save').disabled"):
        fail('PANEL_UX R14: Save is out of reach for a line that appears in several places')
    said = p.js("document.getElementById('text-src').textContent")
    if '3' not in said:
        fail(f'PANEL_UX R14: the panel does not say the correction lands in 3 places: {said!r}')
    steady('a line that appears in several places')

    # ...and when those places hold DIFFERENT text, it must refuse and say so, not offer a
    # picker that changes nothing.
    differing = [cand(0), dict(cand(0), id='YADO.MES#4', line=90,
                               en='[Innkeeper]: Welcome, {0}, and good morning.')]
    push({'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'sources': 'src',
                                'candidates': differing, 'more': 0}})
    settle(p)
    if not p.js("document.getElementById('text-save').disabled"):
        fail('PANEL_UX R14: Save is offered where the occurrences hold different text')
    steady('several places holding different text')

    # --- R4 of emu/PANEL_UX.md: holding is for a line that can be edited -----------------
    # The engine prints some windows in pieces (a name by one instruction, the sentence by
    # another), so there is no single entry to rewrite and the panel says "part of a line".
    # Offering to hold that pins a dead panel: the box cannot be typed in, Save is disabled,
    # and the note reads "the game keeps running" as though all were well.
    push({'t': 'text', 'text': {
        'lines': ['[Pollux]:This is the town square... What will you', 'do?', '', ''],
        'sources': 'src', 'candidates': [], 'more': 0}})
    settle(p)
    if not p.js("document.getElementById('tc-hold').disabled"):
        fail('PANEL_UX R4: hold is offered for a line that cannot be edited')
    p.js("document.getElementById('tc-hold').click()")
    settle(p)
    if p.js("document.getElementById('app').classList.contains('held')"):
        note = p.js("document.getElementById('tc-state').textContent")
        fail(f'PANEL_UX R4: the panel held a line nobody can rewrite, saying {note!r}')
        p.js("document.getElementById('tc-hold').click()")
        settle(p)
    push(ONE_EXACT)
    settle(p)
    if p.js("document.getElementById('tc-hold').disabled"):
        fail('PANEL_UX R4: hold is refused for a line that CAN be edited')
    steady('a line the cockpit cannot rewrite')

    for name, button, pop in (('picture settings', 'btn-view', 'view-pop'),):
        p.js(f"document.getElementById('{button}').click()")
        settle(p)
        if p.js(f"document.getElementById('{pop}').hidden"):
            fail(f'the {name} overlay did not open')
        steady(f'{name} overlay open')
        p.js(f"document.getElementById('{button}').click()")
        settle(p)

    push({'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'sources': 'src',
                                'candidates': [cand(0), cand(3), cand(5)], 'more': 0}})
    settle(p)
    if p.js("document.getElementById('text-alt').hidden"):
        fail('three candidates, but no button to choose between them')
    p.js("document.getElementById('text-alt').click()")
    settle(p)
    if p.js("document.getElementById('text-pop').hidden"):
        fail('the match list did not open')
    steady('match list open')
    p.js("document.querySelectorAll('#text-cands .cand')[1].click()")
    settle(p)
    # ⚠️ The heading now also says how many places the correction lands in (R14), so this
    # matches the start of it rather than the whole string.
    if not p.js("document.getElementById('text-src').textContent").strip().startswith('YADO.MES:74'):
        fail('choosing a match did not switch the box to it: '
             + p.js("document.getElementById('text-src').textContent"))
    steady('after choosing a match')

    # ⚠️ The message window is re-read several times a second. It must not take the keyboard.
    push(ONE_EXACT)
    settle(p)
    typed = '[Innkeeper]: half a typed correction'
    p.js(f"""(() => {{ const e = document.getElementById('text-en');
             e.focus(); e.value = {json.dumps(typed)}; }})()""")
    push({'t': 'text', 'text': {'lines': SCREEN4, 'sources': 'src', 'candidates': [cand(0)],
                                'more': 0}})
    settle(p)
    if p.js("document.getElementById('text-en').value") != typed:
        fail('a redraw of the message window threw away what was being typed')
    steady('while typing')

    # ⚠️ Only what may be changed should be changed: renaming the speaker is named WHILE it is
    # typed, not just when Save is pressed -- and saying so must not move anything either.
    p.js("""(() => { const e = document.getElementById('text-en');
             e.focus(); e.value = '[Innkeper]: a renamed speaker';
             e.dispatchEvent(new Event('input', {bubbles: true})); })()""")
    for _ in range(40):
        if not p.js("document.getElementById('text-why').hidden"):
            break
        time.sleep(0.1)
    else:
        fail('renaming the speaker tag was not flagged while it was being typed')
    steady('warning shown while typing')

    p.js("""(() => { document.getElementById('text-en').value =
             '[Innkeeper]: a tilde ~ and a backslash \\\\';
             document.getElementById('text-save').click(); })()""")
    for _ in range(40):
        if not p.js("document.getElementById('why-pop').hidden"):
            break
        time.sleep(0.1)
    else:
        fail('a refused edit never said why')
    p.js("document.getElementById('text-en').blur()")
    settle(p)
    steady('refusal shown')
    if p.js("document.getElementById('text-save').disabled"):
        fail('Save stayed disabled after a refusal: the edit could not be retried')
    # ⚠️ A real press: the overlays close on pointerdown, which element.click() does not send.
    for kind in ('mousePressed', 'mouseReleased'):
        p.call('Input.dispatchMouseEvent', type=kind, x=700, y=24, button='left', clickCount=1)
    settle(p)
    if not p.js("document.getElementById('why-pop').hidden"):
        fail('pressing outside did not put the refusal away')

    # ⚠️ The panel is HELD while editing -- and it has to SAY so, or a frozen panel over a
    # running game reads as a frozen cockpit. Resuming throws the edit away, behind a confirm.
    if not p.js("document.getElementById('tc-state').textContent").strip():
        fail('the panel stopped following the screen without saying so')
    steady('held')

    # ...and when the game walks away from the held line, the cockpit can no longer write it:
    # that has to be visible, not discovered by pressing a Save that quietly does nothing.
    push({'t': 'text', 'text': {'lines': ['[Armorer]: something else entirely', '', '', ''],
                                'sources': 'src', 'candidates': [], 'more': 0}})
    settle(p)
    if not p.js("document.getElementById('text-save').disabled"):
        fail('the game moved off the held line but Save still looked available')
    if 'moved on' not in p.js("document.getElementById('tc-state').textContent"):
        fail('the game moved off the held line and the panel did not say so')
    steady('held while the game moved on')
    push({'t': 'text', 'text': {'lines': SCREEN2 + ['', ''], 'sources': 'src',
                                'candidates': [cand(0)], 'more': 0}})
    settle(p)
    if p.js("document.getElementById('text-save').disabled"):
        fail('the held line came back on screen but Save stayed out of reach')
    steady('held, line back on screen')
    before = p.js("document.getElementById('text-en').value")
    p.js("document.getElementById('tc-hold').click()")            # dirty: this only arms
    settle(p)
    if p.js("document.getElementById('text-en').value") != before:
        fail('one click on Follow threw the edit away without asking')
    p.js("document.getElementById('tc-hold').click()")            # ...and this confirms
    settle(p)
    if p.js("document.getElementById('tc-state').textContent").strip():
        fail('resuming left the held marker on')
    if p.js("document.getElementById('text-en').value") == before:
        fail('resuming did not discard the edit')
    steady('after resuming')

    # ⚠️ Putting the caret in the box IS a hold, whether or not anyone pressed the button --
    # otherwise a reading of the window lands on top of a half-typed correction. (Regression:
    # resuming used to leave the caret behind, and the panel followed the screen with someone
    # still typing into it.)
    push(ONE_EXACT)
    settle(p)
    p.js("document.getElementById('text-en').focus()")
    settle(p)
    if not p.js("document.getElementById('app').classList.contains('held')"):
        fail('putting the caret in the box did not hold the panel')
    mid = '[Innkeeper]: typed without pressing hold'
    p.js(f"document.getElementById('text-en').value = {json.dumps(mid)}")
    push({'t': 'text', 'text': {'lines': SCREEN4, 'sources': 'src',
                                'candidates': [cand(0)], 'more': 0}})
    settle(p)
    if p.js("document.getElementById('text-en').value") != mid:
        fail('a reading of the window overwrote a correction that was being typed')
    steady('typing without having pressed hold')
    p.js("document.getElementById('tc-hold').click()")            # dirty: arms
    settle(p)
    p.js("document.getElementById('tc-hold').click()")            # ...and confirms
    settle(p)
    if p.js("document.getElementById('app').classList.contains('held')"):
        fail('the panel would not go back to following the screen')

    # --- R13: a finished save lets go ----------------------------------------------------
    # Staying held after a successful write leaves a frozen picture of the past: the message
    # window closes behind it, Save goes dark because that line is no longer on screen, and
    # nothing can be edited again until the panel is released by hand.
    push(ONE_EXACT)
    settle(p)
    p.js("document.getElementById('text-en').focus()")
    settle(p)
    p.js("""(() => { const e = document.getElementById('text-en');
             e.value = '[Innkeeper]: a correction that will be accepted';
             e.dispatchEvent(new Event('input', {bubbles: true})); })()""")
    time.sleep(0.7)
    p.js("document.getElementById('text-save').click()")
    time.sleep(1.2)
    settle(p)
    if p.js("document.getElementById('app').classList.contains('held')"):
        fail('PANEL_UX R13: the panel stayed held after the correction was written — '
             'the next line is then unreachable until it is released by hand')
    steady('after a correction is written')

    # ⚠️ A hold that began with nothing on screen used to pin "nothing" and then freeze the panel
    # for good: when a line landed, `renderText` was still held off, so the box stayed read-only
    # with Save dead until the tab was reloaded. Found by a proofreader, not by this check
    # (2026-09-16). Holding nothing is refused, and a held line that comes back is taken back.
    push({'t': 'text', 'text': None})
    settle(p)
    p.js("document.getElementById('tc-hold').click()")
    settle(p)
    if p.js("document.getElementById('app').classList.contains('held')"):
        fail('the panel held an empty window, which can only freeze it')
    push(ONE_EXACT)
    settle(p)
    if p.js("document.getElementById('text-en').readOnly"):
        fail('a line landed after a blank moment and the box stayed read-only')
    if p.js("document.getElementById('text-save').disabled"):
        fail('a line landed after a blank moment and Save stayed out of reach')
    steady('a line landing after a blank moment')

    # the same trap from the other side: hold a line, let the game walk off it, bring it back
    p.js("document.getElementById('text-en').focus()")
    settle(p)
    push({'t': 'text', 'text': None})
    settle(p)
    if not p.js("document.getElementById('text-save').disabled"):
        fail('the game walked off the held line and Save still looked available')
    push(ONE_EXACT)
    settle(p)
    if p.js("document.getElementById('text-en').readOnly") or \
            p.js("document.getElementById('text-save').disabled"):
        fail('the held line came back and the panel did not take it back')
    if 'moved on' in p.js("document.getElementById('tc-state').textContent"):
        fail('the held line is back on screen but the panel still says the game moved on')
    steady('a held line taken back')
    p.js("document.getElementById('tc-hold').click()")
    settle(p)
    if p.js("document.getElementById('app').classList.contains('held')"):
        p.js("document.getElementById('tc-hold').click()")     # it armed a discard: confirm
        settle(p)

    push(ONE_EXACT)
    settle(p)
    p.js("document.getElementById('tc-fold').click()")
    settle(p)
    if p.js(GEOM)['screen'] == base:
        fail('folding the panel away gave the picture no room')
    p.js("document.getElementById('tc-fold').click()")
    settle(p)
    steady('after unfolding')

    p.js("document.getElementById('btn-theater').click()")
    settle(p)
    vis = p.js("""(() => { const d = s => { const e = document.querySelector(s);
                    return e ? getComputedStyle(e).display : 'gone'; };
                  return [d('.textcard'), d('.padrow'), d('.ctlbar')]; })()""")
    if vis[:2] != ['none', 'none'] or vis[2] == 'none':
        fail(f'theater mode is wrong: .textcard/.padrow/.ctlbar are {vis}')
    p.js("document.getElementById('btn-theater').click()")
    settle(p)


def main():
    browser = next((b for b in BROWSERS if shutil.which(b)), None)
    if not browser:
        print('SKIPPED: no Chrome or Chromium here, and the layout can only be measured in one.')
        return 0
    if not (WEB / 'index.html').is_file():
        print(f'no cockpit page at {WEB}')
        return 1

    threading.Thread(target=frames, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    profile = pathlib.Path(os.environ.get('TMPDIR', '/tmp')) / f'ww-layout-{os.getpid()}'
    chrome = subprocess.Popen([
        browser, '--headless=new', '--disable-gpu', '--no-sandbox',
        f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
        f'--remote-debugging-port={CDP_PORT}', '--remote-allow-origins=*',
        '--window-size=1920,1080', f'http://127.0.0.1:{PORT}/'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    failures = []
    fail = failures.append
    try:
        p = None
        for _ in range(60):
            try:
                p = Cdp(CDP_PORT)
                break
            except Exception:
                time.sleep(0.5)
        if p is None:
            print(f'{browser} never answered on the debugging port')
            return 1
        for _ in range(60):
            if p.js('!!(window.WW && WW.S && WW.S.link)'):
                break
            time.sleep(0.5)
        else:
            print('the page never opened its socket: play.js did not run through')
            return 1
        p.js('new Promise(r => document.fonts.ready.then(r))')

        for vw, vh in VIEWPORTS:
            p.call('Emulation.setDeviceMetricsOverride', width=vw, height=vh,
                   deviceScaleFactor=1, mobile=False)
            settle(p)
            if p.js(GEOM)['viewport'] != [vw, vh]:
                print(f'the browser would not take a {vw}x{vh} viewport')
                return 1
            rects, boxes, maps = set(), set(), set()
            for name, msg in SCENARIOS:
                push(msg)
                settle(p)
                g = p.js(GEOM)
                rects.add(tuple(g['screen']))
                boxes.add(tuple(g['box']))
                maps.add(tuple(g['mapcard']))
                where = f'{vw}x{vh} · {name}'
                if g['missing']:
                    fail(f'{where}: the page is missing {g["missing"]}')
                if g['barRows'] != 1:
                    fail(f'{where}: the control bar stands on {g["barRows"]} rows')
                sw, cw = g['barOverflow']
                if sw > cw + 1:
                    fail(f'{where}: {sw - cw}px of the control bar is off the edge, '
                         f'and the scrollbar is hidden')
                need, have = g['fits56']
                if need > have:
                    fail(f'{where}: 56 columns need {need}px, the box offers {have}px')
                l, r = g['saveInside']
                if l < 0 or r < 0:
                    fail(f'{where}: Save is pushed out of the panel '
                         f'({l}px from its left edge, {r}px from its right)')
            if len(rects) > 1:
                hs = sorted(r[3] for r in rects)
                fail(f'{vw}x{vh}: the picture takes {len(rects)} sizes, heights {hs[0]}..{hs[-1]}px')
            if len(boxes) > 1:
                fail(f'{vw}x{vh}: the line box changes size between states: {sorted(boxes)}')
            if len(maps) > 1:
                hs = sorted(m[3] for m in maps)
                fail(f'{vw}x{vh}: the map card takes {len(maps)} heights, {hs[0]}..{hs[-1]}px -- '
                     f'every card under it moves as you change floors')
            print(f'  {vw}x{vh}: picture {sorted(rects)[0]}, line box {sorted(boxes)[0]}, '
                  f'{len(rects)} size(s) across {len(SCENARIOS)} states')

        p.call('Emulation.setDeviceMetricsOverride', width=1600, height=900,
               deviceScaleFactor=1, mobile=False)
        settle(p)
        behaviour(p, fail)
    finally:
        chrome.terminate()
        try:
            chrome.wait(10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        shutil.rmtree(profile, ignore_errors=True)

    if failures:
        print(f'\n{len(failures)} problem(s):')
        for f in failures:
            print(' ·', f)
        return 1
    print('\nthe picture is the same size in every state, at every window size checked')
    return 0


if __name__ == '__main__':
    sys.exit(main())
