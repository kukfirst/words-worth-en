#!/usr/bin/env python3
"""Hear the game's music -- core audio goes to PipeWire, not /dev/null.

libretro.py's audio drivers only know how to buffer samples into an array or write a WAV.
This is a third: frames go out to `pw-cat`, i.e. straight to the machine's audio server.

⚠️ The emulator MUST NOT be made to wait on the audio server. The agent runs the core at
whatever speed is requested: at "max" that's ~850 frames/s, and a synchronous write to the
pipe would instantly back up and drag the game's pace down. So the write happens from a
separate thread through a BOUNDED queue, and overflow is simply dropped -- a click beats a
frozen game.

⚠️ Sound is only meaningful at ×1 speed. At "max" the core emits samples ten times faster
than real time: the queue chokes and you hear noise. That's why the cockpit turns sound on
together with ×1 speed, and says so.
"""
import queue
import shutil
import subprocess
import threading

from libretro.drivers import AudioDriver

PLAYER = 'pw-cat'          # PipeWire; on PulseAudio pacat works with the same flags
FALLBACK = 'pacat'
QUEUE = 24                 # blocks in the queue; more -- latency, fewer -- dropouts
# ⚠️ The core calls sample_batch ~960 times a second, 182 B each (measured). Queuing at that
# granularity means measuring the queue's length in milliseconds and losing packets on any
# thread hiccup. Buffer at the PRODUCER up to a block: 8 KB is 46 ms of audio, and ~20 pipe
# writes per second.
BLOCK = 8192


def player():
    """What to play with. None -- nothing available, and that's not an error: sound just
    won't turn on."""
    for name in (PLAYER, FALLBACK):
        if shutil.which(name):
            return name
    return None


def args_for(name, rate, latency='300ms'):
    """What and how to play a raw stream with.

    ⚠️ `--raw` is mandatory. Without it pw-cat expects a WAV container, reads our raw PCM as
    the header, doesn't understand it, and EXITS SILENTLY. The queue overflows instantly,
    and by symptoms it looks like "sound can't keep up" -- measured as 39 dropped blocks out
    of 39, i.e. silence.
    """
    if name == PLAYER:
        return [name, '-p', '--raw', '--rate', str(rate), '--channels', '2',
                # ⚠️ 300 ms by default, not 100: the agent runs the game in bursts, and with a
                # short buffer sound empties out between them -- audible as "skipped notes".
                # A steadily paced caller (play_core.py) can afford less.
                '--format', 's16', '--latency', latency, '-']
    return [name, '--raw', '--rate', str(rate), '--channels', '2', '--format', 's16le']


class Speaker:
    """Receiver for core samples. While off -- silently discards everything."""

    def __init__(self, rate=44100, latency='300ms'):
        self.rate = int(rate) or 44100
        self.latency = latency
        self._proc = None
        self._q = None
        self._thread = None
        self._acc = bytearray()
        self.dropped = 0

    @property
    def on(self):
        return self._proc is not None

    def start(self):
        if self.on:
            return True
        name = player()
        if not name:
            return False
        args = args_for(name, self.rate, self.latency)
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        except OSError:
            self._proc = None
            return False
        self._acc = bytearray()
        self._q = queue.Queue(maxsize=QUEUE)
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        p, self._proc = self._proc, None
        if self._q is not None:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
        if p:
            try:
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(timeout=1)
            except Exception:
                p.kill()
        self._q = None

    def _pump(self):
        """Write to the pipe in BATCHES.

        ⚠️ The core hands out audio as many small packets per frame, and one write+flush
        per packet is hundreds of syscalls per second. Measured, that dropped 334 packets
        in six seconds -- audible gaps. Flush everything that's accumulated as one chunk.
        """
        q, p = self._q, self._proc
        while True:
            item = q.get()
            if item is None or p.poll() is not None:
                return
            chunk = [item]
            while True:
                try:
                    nxt = q.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    return
                chunk.append(nxt)
            try:
                p.stdin.write(b''.join(chunk))
                p.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return

    def feed(self, data: bytes):
        """Frames from the core. Queue full -- the block is dropped, the game doesn't wait."""
        if not self.on or not data:
            return
        self._acc += data
        if len(self._acc) < BLOCK:
            return
        block, self._acc = bytes(self._acc), bytearray()
        try:
            self._q.put_nowait(block)
        except (queue.Full, AttributeError):
            self.dropped += 1


class Driver(AudioDriver):
    """AudioDriver for libretro.py: everything the protocol asks for, and one speaker inside.

    ⚠️ We subclass EXPLICITLY. The protocol is declared `runtime_checkable`, but Session
    checks it via isinstance, and the protocol has more than just methods -- a plain
    duck-typed object gets rejected with "Expected an AudioDriver".
    """

    def __init__(self, speaker: Speaker):
        self.speaker = speaker
        self._av = None

    def sample(self, left: int, right: int) -> None:
        self.speaker.feed(int(left).to_bytes(2, 'little', signed=True)
                          + int(right).to_bytes(2, 'little', signed=True))

    def sample_batch(self, frames) -> int:
        view = frames if getattr(frames, 'format', 'h') == 'h' else frames.cast('B').cast('h')
        self.speaker.feed(view.tobytes())
        return len(view) // 2

    # -- protocol parts we don't have; the core has to survive them being refused ---------
    @property
    def callbacks(self):
        return None

    @callbacks.setter
    def callbacks(self, cb):
        pass

    @property
    def buffer_status(self):
        return None

    @buffer_status.setter
    def buffer_status(self, cb):
        pass

    @property
    def minimum_latency(self):
        return None

    @minimum_latency.setter
    def minimum_latency(self, v):
        pass

    @property
    def system_av_info(self):
        return self._av

    @system_av_info.setter
    def system_av_info(self, info):
        self._av = info
        rate = getattr(getattr(info, 'timing', None), 'sample_rate', None)
        if rate:
            self.speaker.rate = int(rate)


if __name__ == '__main__':
    import math, sys, time
    sp = Speaker(44100)
    if not sp.start():
        sys.exit('nothing to play: pw-cat and pacat not found')
    buf = bytearray()
    for i in range(44100):                       # one second at 440 Hz -- path check
        v = int(6000 * math.sin(2 * math.pi * 440 * i / 44100))
        buf += v.to_bytes(2, 'little', signed=True) * 2
    sp.feed(bytes(buf))
    time.sleep(1.5)
    sp.stop()
    print('if it was heard -- track is alive')
