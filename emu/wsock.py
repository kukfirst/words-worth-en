"""Server side of RFC 6455 WebSocket: the handshake and the framing, nothing more.

The play cockpit pushes every changed frame to the browser and takes key presses back. The
old cockpit did that through files: the agent wrote live.png, the viewer polled its mtime every
20 ms and re-sent it as a multipart PNG stream, and key presses went through a control.json
that two processes read and rewrote without a lock (a press landing between the read and the
reset was lost). One socket in each direction replaces all of it.

The stdlib has no WebSocket server, and this needs so little of the protocol that a dependency
would be bigger than the code: no extensions, no compression, the server never masks, and the
client (a browser tab on 127.0.0.1) always does.
"""
import base64
import hashlib
import struct
import threading

GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
TEXT, BINARY, CLOSE, PING, PONG, CONT = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0
MAX_MESSAGE = 1 << 20          # client messages are small JSON commands


class ProtocolError(Exception):
    pass


def accept_key(key):
    """Sec-WebSocket-Accept for a client's Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1(key.strip().encode() + GUID).digest()).decode()


def encode(payload, opcode=BINARY):
    """One unfragmented, unmasked server frame."""
    n = len(payload)
    if n < 126:
        head = struct.pack('!BB', 0x80 | opcode, n)
    elif n < 1 << 16:
        head = struct.pack('!BBH', 0x80 | opcode, 126, n)
    else:
        head = struct.pack('!BBQ', 0x80 | opcode, 127, n)
    return head + payload


def _exact(rfile, n):
    buf = rfile.read(n)
    if buf is None or len(buf) < n:
        raise EOFError
    return buf


def read_frame(rfile):
    """(fin, opcode, payload) of one frame; EOFError when the peer is gone."""
    b0, b1 = _exact(rfile, 2)
    fin, opcode, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
    if n == 126:
        n = struct.unpack('!H', _exact(rfile, 2))[0]
    elif n == 127:
        n = struct.unpack('!Q', _exact(rfile, 8))[0]
    if n > MAX_MESSAGE:
        raise ProtocolError(f'frame of {n} bytes')
    if not masked:
        # RFC 6455 5.1: a server MUST close the connection on an unmasked client frame
        raise ProtocolError('unmasked client frame')
    mask = _exact(rfile, 4)
    data = bytearray(_exact(rfile, n))
    for i in range(n):
        data[i] ^= mask[i & 3]
    return bool(fin), opcode, bytes(data)


def read_message(rfile, on_ping=None):
    """(opcode, payload) of the next whole data message; (CLOSE, b'') when the peer closes.

    Control frames in between are answered through `on_ping` and skipped.
    """
    parts, first = [], None
    while True:
        fin, op, data = read_frame(rfile)
        if op == CLOSE:
            return CLOSE, b''
        if op == PING:
            if on_ping:
                on_ping(data)
            continue
        if op == PONG:
            continue
        if op != CONT:
            first, parts = op, []
        parts.append(data)
        if sum(len(p) for p in parts) > MAX_MESSAGE:
            raise ProtocolError('message too large')
        if fin:
            return first, b''.join(parts)


class Peer:
    """One connected browser. Sends from its own thread so a slow tab never blocks the game.

    Frames are LATEST-ONLY: if the tab is still receiving the previous frame when a new one
    arrives, the older one is replaced, not queued -- a queue of stale frames is exactly the
    growing lag the old MJPEG stream had. Text messages (telemetry, events) are all kept.
    """

    def __init__(self, sock):
        self.sock = sock
        self.alive = True
        self._cv = threading.Condition()
        self._frame = None
        self._texts = []
        self.sent_frames = 0
        self.dropped_frames = 0
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def frame(self, payload):
        with self._cv:
            if self._frame is not None:
                self.dropped_frames += 1
            self._frame = payload
            self._cv.notify()

    def text(self, s):
        with self._cv:
            self._texts.append(s.encode() if isinstance(s, str) else s)
            del self._texts[:-256]           # a tab that stopped reading must not grow us
            self._cv.notify()

    def pong(self, data):
        with self._cv:
            self._texts.append((PONG, data))
            self._cv.notify()

    def close(self):
        with self._cv:
            self.alive = False
            self._cv.notify()

    def _pump(self):
        try:
            while True:
                with self._cv:
                    while self.alive and self._frame is None and not self._texts:
                        self._cv.wait(1.0)
                    if not self.alive:
                        return
                    texts, self._texts = self._texts, []
                    frame, self._frame = self._frame, None
                out = []
                for t in texts:
                    out.append(encode(t[1], t[0]) if isinstance(t, tuple) else encode(t, TEXT))
                if frame is not None:
                    out.append(encode(frame, BINARY))
                    self.sent_frames += 1
                self.sock.sendall(b''.join(out))
        except OSError:
            pass
        finally:
            self.alive = False
