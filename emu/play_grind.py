#!/usr/bin/env python3
"""Autobattle for the play cockpit: a script, not a model. Only memory and keys.

It walks the floor, attacks whatever it meets, and hands the game back to the human the moment
anything is off: health below the floor, a screen that eats input, a dead end. Carried over
from the QA agent (emu/agent.py `_grind_tick`) with its hard-won rules kept:

  * the fuse first -- without a readable HP it does not run at all (it once ground a hero to
    game over during an address hunt, because a losing fight had nothing to stop it);
  * passability comes from the GAME'S OWN MAP, never from "did we move" (inferring it made the
    hero spin in place between two headings forever);
  * a cell that swallowed input is remembered in the avoid list and never entered again;
  * combat is recognised by the scene name in memory (SENTO*), not from the picture.

⚠️ And the reason it was rewritten rather than copied: the agent decided the next move from a
snapshot taken while the game was still coming out of a battle, so the heading and the cell
were the pre-battle ones -- it finished a turn relative to where it used to be and only then
walked on (reported by the owner, 2026-09-16). Here every decision waits for the world to
STAND STILL: two readings in a row with the same scene, cell and heading, and nothing owed to
the emulator. Leaving a fight drops the settled state, so the first post-battle decision is
made on fresh ground.
"""
import time

SENTO = 'SENTO'          # the battle scenes; the game names them itself
DEFAULTS = {'hp_floor': 20, 'span': 10, 'avoid': []}
SETTLE_READS = 2         # identical readings in a row before a decision is made
# ⚠️ ...and that is not enough on its own: two readings are a quarter of a second, and the hero
# is still finishing his step. A press issued then is SWALLOWED. Traced 2026-09-16: the turn in
# the dead end (where he stood still) worked, the identical turn right after walking into (14,3)
# was lost, and from there autobattle only ever nudged the screen. Decisions wait until nothing
# has changed for this long -- measured from the last change, not from our own press.
# ⚠️ 1.2 s, not a quarter of one: a step in this game takes about that long, and the trace of
# 2026-09-16 shows why it matters. The turn decided 1.1 s after the hero walked into (14,3) was
# swallowed -- twenty readings later he was still facing the same way -- while the identical
# turn at (12,3), where he had been standing still, worked at once. The same threshold also
# throws away the garbage reading right after a save loads (cell (0,0), map from nowhere):
# it never lives long enough to be acted on.
QUIET_FOR = 1.2          # s at x1, scaled by the speed the game runs at
ACT_PAUSE = 0.55         # s after a key press before the next reading is trusted (x1)
# ⚠️ A step TAKES time: the press is 8 frames, but the hero walks for about a second and the
# coordinate in memory only changes when he arrives. Judging "nothing happened" before that is
# how autobattle declared the cell a dead end and hammered return_key at a game that was simply
# walking (found by the running acceptance, traced 2026-09-16: decision at 53.682, verdict at
# 54.412). Nothing may be called stuck until the action has had this long to play out.
STILL_AFTER = 2.0        # s at x1; scaled by the speed the game is running at
GAME_FPS = 56.4          # PC-98 frame rate at x1
MAX_SPEED = 15           # what "MAX" is worth when scaling waits (~850 fps / 56.4)
# ⚠️ The first press after the game loads a save is SWALLOWED. Measured on floor 5A: tap up --
# nothing, tap space -- nothing, the next tap up walked. So when the world did not change, retry
# the MOVE first; nudging the screen with return_key/space (what the old agent did) cannot help
# a keypress that simply never arrived, and autobattle gave up on a perfectly walkable cell.
RETRY_MOVES = 2          # how many times the same move is repeated before nudging the screen
CUTSCENE_GRACE = 6       # how often a moving picture may excuse a world that is not moving
# ⚠️ A press is counted in FRAMES, so it means the same at x1 and at MAX. Measured on floor 5A
# from one saved spot: 8, 16 and 24 frames walk exactly ONE cell; 40 frames and more walk TWO --
# the hero keeps going while the key is down. One decision must move one cell, so a step is a
# short press, like a turn.
# ⚠️⚠️ An earlier "measurement" said a step needs 68 frames and doors need a long hold. It was
# worthless: the cockpit did not forward the 'tap' command to the emulator at all, so those
# presses never happened. Check that the thing you are measuring is actually reaching the game.
STEP_FRAMES = 8
TURN_FRAMES = 8
# A door is a way through, not a wall: on this floor the only exits of several cells are doors,
# and walking into one opens it (the same hold that walks).
WALKABLE = ('open', 'door')
HP_MISS_LIMIT = 20       # unreadable health for this many decisions -> no fuse, stop
STUCK_LIMIT = 8          # nudges with nothing moving -> a real dead end


class Grind:
    """Pure decision-making: feed it readings, it answers with keys. No emulator in here."""

    def __init__(self, cfg=None, now=None):
        c = {**DEFAULTS, **(cfg or {})}
        self.hp_floor = int(c['hp_floor'])
        self.span = int(c['span'])
        self.avoid = {tuple(a) for a in c['avoid']}
        self.steps = 0
        self.fights = 0
        self.stopped = None
        self._seen = None          # the last reading, for the stillness test
        self._same = 0
        self._settled = None       # the world as it stood when we last decided
        self._stuck = 0
        self._hp_miss = 0
        self._in_battle = False
        self._wait_until = now or 0.0
        self._acted_at = now or 0.0     # when we last pressed anything
        self._last_keys = None          # ...and what it was, in case the game swallowed it
        self._last_move = None          # the last STEP or TURN -- what a retry must repeat
        self._cutscene = 0              # times a moving picture excused a still world
        self._changed_since = False     # did anything move since our last press?
        self._last_change_at = now or 0.0   # when the world last looked different
        self._cursor = None             # the game's own mouse cursor: a menu moves it
        self._scale = 1.0               # wall-clock waits shrink as the game runs faster

    # -- helpers ---------------------------------------------------------------------------
    def stop(self, why):
        self.stopped = why
        return {'stop': why}

    def _still(self, reading, now):
        """True once the world has looked the same for QUIET_FOR -- not merely twice in a row."""
        key = (reading.get('scene'), reading.get('x'), reading.get('y'), reading.get('facing'))
        if key == self._seen:
            self._same += 1
        else:
            self._seen, self._same = key, 1
            self._last_change_at = now
        return (self._same >= SETTLE_READS
                and now - self._last_change_at >= QUIET_FOR * self._scale)

    # -- the step --------------------------------------------------------------------------
    def step(self, r, now=None):
        """One decision from one reading.

        `r`: {'scene', 'x', 'y', 'facing', 'hp', 'hp_max', 'sides', 'frame_moving'}
        `sides`: {0..3: 'open'|'wall'|...} for the cell we stand in, from the game's own map.
        Returns {'keys': [...]}, {'stop': why}, or {} -- nothing to do yet.
        """
        now = time.monotonic() if now is None else now
        if self.stopped:
            return {}
        speed = r.get('speed')
        self._scale = 1.0 / (MAX_SPEED if speed == 0 else (speed or 1))
        if now < self._wait_until:
            return {}

        scene = (r.get('scene') or '').upper()
        in_battle = scene.startswith(SENTO)
        if in_battle:
            self._in_battle = True
            self._settled = self._seen = None      # the world will be different afterwards
            self._same = 0
            return self._act(['return_key'], now)  # attack

        if self._in_battle:                        # just came out of a fight
            self._in_battle = False
            self.fights += 1
            self.steps = 0
            self._settled = self._seen = None
            self._same = 0
            self._stuck = 0
            return {}                              # decide nothing until the world stands still

        if not self._still(r, now):
            return {}

        hp, hp_max = r.get('hp'), r.get('hp_max')
        if self.hp_floor > 0:
            if not hp or not hp_max:
                self._hp_miss += 1
                if self._hp_miss > HP_MISS_LIMIT:
                    return self.stop('health unreadable: autobattle has no fuse')
                return {}
            self._hp_miss = 0
            if 100 * hp / hp_max < self.hp_floor:
                return self.stop(f'HP {hp}/{hp_max} below {self.hp_floor}%')

        x, y, f = r.get('x'), r.get('y'), r.get('facing')
        sides = r.get('sides') or {}
        if x is None or f is None or not sides:
            # ⚠️ Right after a save loads, the coordinates read (0,0) with no map for a moment.
            # Pressing anything then is pressing blind -- and it used to set the "last move" to
            # that blind press. Wait: the cockpit asks again in an eighth of a second.
            return {}

        here = (x, y, f)
        if here != self._settled:
            self._changed_since = True
        # ⚠️ A MENU is holding the input. Seen for real: autobattle walked east into (14,3),
        # which is the door of the hero's own room, and the game opened the diary list -- the
        # SAVE menu. There the arrow keys move the mouse cursor and the hero never turns, so
        # every press looked like "nothing happened". Pressing blindly in that menu is how the
        # game was once driven into a save and a crash (STATUS §12), so this is a reason to
        # hand the game back at once, not to keep poking.
        cursor = tuple(r.get('cursor') or ())
        if (here == self._settled and not self._changed_since and cursor and cursor != self._cursor
                and self._cursor is not None and self._acted_at):
            self.avoid.add((x, y))
            self._cursor = cursor
            return self.stop(f'a menu is holding the input at ({x}, {y}) -- '
                             f'the keys move its cursor, not the hero')
        if cursor:
            self._cursor = cursor
        # ⚠️ Being in the same place as when we last pressed is only "stuck" if NOTHING has
        # moved since. Walking a corridor brings the hero back to the same cell and heading all
        # the time, and comparing against a single remembered state called that a dead end --
        # autobattle then wrote a perfectly good cell into the avoid list (traced 2026-09-16).
        if here == self._settled and not self._changed_since:
            # nothing changed after our own last move -- but a move TAKES time, so this only
            # means something once the action has had time to play out.
            if now - self._acted_at < STILL_AFTER * self._scale:
                return {}
            # a window is holding the input, or a cutscene is playing. A changing picture means
            # progress, so keep nudging.
            self._stuck += 1
            # ⚠️ ...but only so many times. On a dungeon floor the torches animate for ever, so
            # "the picture is moving" is no proof of progress: unlimited, this excuse kept
            # autobattle pressing return_key for two minutes while the way ahead stood open
            # (traced 2026-09-16 -- twenty nudges in a row, not one step retried).
            if r.get('frame_moving') and self._cutscene < CUTSCENE_GRACE:
                self._cutscene += 1
                self._stuck = 1
                return self._act(['return_key'], now)
            if self._stuck > STUCK_LIMIT:
                self.avoid.add((x, y))
                return self.stop(f'stuck at ({x}, {y}) facing {f} -- cell added to the avoid list')
            # ⚠️ Retry the MOVE, not whatever was pressed last: `_last_keys` also holds the
            # nudges, so "retry" used to repeat return_key at a hero who had simply never been
            # told to walk again (traced 2026-09-16 -- not one step was ever repeated).
            if self._stuck <= RETRY_MOVES and self._last_move:
                return self._act(list(self._last_move), now)      # the press may have been eaten
            return self._act(['return_key' if self._stuck % 2 else 'space'], now)

        self._stuck = 0
        self._cutscene = 0
        self._settled = here
        step = STEP.get(f)
        ahead = (sides.get(f) in WALKABLE
                 and not (step and (x + step[0], y + step[1]) in self.avoid))
        if ahead and self.steps < self.span:
            self.steps += 1
            return self._act([('up', STEP_FRAMES)], now, move=True)

        # Time to turn: BACK first, so the hero paces one stretch of corridor -- that is what
        # grinding is. ⚠️ A version that preferred the way it had been in least recently
        # (2026-09-16) turned autobattle into a slow tour of the whole floor, reported by the
        # owner the same evening. Other open sides are only for when the way back is shut.
        back = (f + 2) % 4
        for want in [back] + [d for d in range(4) if d not in (f, back)]:
            s = STEP.get(want)
            if s and (x + s[0], y + s[1]) in self.avoid:
                continue
            if sides.get(want) in WALKABLE:
                self.steps = 0
                # ⚠️ `_settled` stays the state we acted FROM (heading f), not the one we are
                # turning to: the arrival must read as a change, or the turn itself would look
                # like "nothing happened" and count as being stuck.
                return self._act([('left', TURN_FRAMES)] * ((want - f) % 4), now, move=True)
        return self._act([('left', TURN_FRAMES)], now, move=True)   # boxed in: turn, look again

    def key_gap(self, frames):
        """Wall-clock wait after sending one key: the press itself plus a breath, at game speed.

        ⚠️ The cockpit used to wait a fixed 0.55 s + frames/56.4 between keys whatever the speed,
        so at MAX a back-turn (two presses) still took 1.5 s of a run that is meant to fly.
        """
        return (ACT_PAUSE + frames / GAME_FPS) * self._scale

    def _act(self, keys, now, move=False):
        """`keys` is a list of (key, frames): the game counts frames, so scripts must too.

        `move` marks a step or a turn -- the thing worth repeating if the game swallowed it.
        """
        keys = [(k, TURN_FRAMES) if isinstance(k, str) else k for k in keys]
        self._acted_at = now
        self._last_keys = keys
        if move:
            self._last_move = keys
        self._changed_since = False
        self._wait_until = now + ACT_PAUSE * self._scale * max(1, len(keys))
        self._seen, self._same = None, 0           # anything read before the press is stale
        return {'keys': keys}


# where a step forward leads for each heading (emu/state.py STEP, kept here so the decision
# logic has no imports of its own)
STEP = {0: (1, 0), 1: (0, -1), 2: (-1, 0), 3: (0, 1)}
