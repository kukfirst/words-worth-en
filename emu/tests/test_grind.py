"""Autobattle decisions (emu/play_grind.py): memory in, keys out. No emulator here.

The rules these tests hold in place are the ones that cost real runs:
  * without readable health it must not run at all;
  * passability comes from the game's own map, not from "did we move";
  * presses are counted in FRAMES, so they mean the same at x1 and at MAX, and a step is a
    SHORT press (measured on floor 5A: 8, 16 and 24 frames walk one cell, 40 and more walk two);
  * a door is a way through, not a wall (several cells there have no other exit);
  * a decision waits until the world has been QUIET for a moment -- a press issued while the
    hero is still arriving is swallowed by the game, and that is how autobattle ended up
    nudging the screen at a perfectly walkable cell;
  * being where we were is only "stuck" if nothing has moved since -- walking a corridor
    returns the hero to the same cell and heading all the time;
  * and the one the owner reported: after a fight it must not decide anything until the world
    stands still, or it turns relative to where it used to be and then walks on.

⚠️ The tests wait for a decision the way the cockpit does -- by feeding readings until one
comes -- instead of hand-picking an interval. Timings here are a CONTRACT, not magic numbers:
when the contract changed, the version of these tests that hard-coded 0.3 s all failed at once.
"""
import pathlib
import sys
import unittest

EMU = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EMU))

import play_grind                                                   # noqa: E402

STEP = ('up', play_grind.STEP_FRAMES)
TURN = ('left', play_grind.TURN_FRAMES)
ATTACK = ('return_key', play_grind.TURN_FRAMES)
OPEN4 = {0: 'open', 1: 'open', 2: 'open', 3: 'open'}
WALLS = {0: 'wall', 1: 'wall', 2: 'wall', 3: 'wall'}


def read(scene='FLOOR5A.MES', x=5, y=5, facing=0, hp=100, hp_max=100, sides=None, moving=False,
         speed=1, cursor=None):
    return {'scene': scene, 'x': x, 'y': y, 'facing': facing, 'hp': hp, 'hp_max': hp_max,
            'sides': dict(OPEN4 if sides is None else sides), 'frame_moving': moving,
            'speed': speed, 'cursor': cursor}


class Drive:
    """A clock and a reading, fed to the script the way the cockpit feeds it."""

    def __init__(self, cfg=None):
        self.t = 100.0
        self.g = play_grind.Grind(cfg, now=self.t)

    def tick(self, r, step=0.2):
        self.t += step
        return self.g.step(r, now=self.t)

    def decide(self, r, tries=12, step=0.2):
        """Feed the same reading until the script answers; {} if it never does."""
        for _ in range(tries):
            got = self.tick(r, step)
            if got:
                return got
        return {}


class Battle(unittest.TestCase):
    def test_it_attacks_in_a_fight(self):
        d = Drive()
        self.assertEqual(d.tick(read(scene='SENTO00.MES')), {'keys': [ATTACK]})

    def test_after_a_fight_nothing_is_decided_until_the_world_stands_still(self):
        """The reported defect: the first post-battle reading still holds the pre-battle cell
        and heading, and the script turned relative to THAT, then walked."""
        d = Drive()
        d.tick(read(scene='SENTO00.MES'))                           # in the fight
        stale = read(x=4, y=5, facing=2, sides=WALLS)               # where it used to be
        d.t += 2.0
        self.assertEqual(d.g.step(stale, now=d.t), {}, 'a stale reading must decide nothing')
        fresh = read(x=5, y=5, facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(fresh), {'keys': [STEP]},
                         'and then it walks from where it actually is')

    def test_a_fight_starts_the_walked_distance_over(self):
        """The span is a patrol length, not a budget for the day: after a fight it restarts,
        so the hero does not turn round the moment the battle ends."""
        d = Drive({'span': 2})
        for i in range(2):
            self.assertEqual(d.decide(read(x=5 + i)), {'keys': [STEP]}, i)
        d.t += 2.0
        self.assertEqual(d.g.step(read(scene='SENTO00.MES'), now=d.t), {'keys': [ATTACK]})
        self.assertEqual(d.g.fights, 0)
        d.t += 2.0
        self.assertEqual(d.g.step(read(x=7), now=d.t), {})           # leaving the fight
        self.assertEqual(d.g.fights, 1)
        self.assertEqual(d.decide(read(x=7)), {'keys': [STEP]},
                         'it walks on instead of turning: the span started over')


class Fuse(unittest.TestCase):
    def test_low_health_stops_it(self):
        d = Drive({'hp_floor': 40})
        got = d.decide(read(hp=30, hp_max=100))
        self.assertIn('stop', got)
        self.assertIn('below 40%', got['stop'])

    def test_unreadable_health_stops_it_but_not_on_the_first_miss(self):
        d = Drive({'hp_floor': 20})
        r = read(hp=None, hp_max=None)
        self.assertEqual(d.decide(r, tries=6), {}, 'a few unreadable frames are normal')
        got = {}
        for _ in range(play_grind.HP_MISS_LIMIT + 4):
            got = d.decide(r, tries=4)
            if got:
                break
        self.assertIn('stop', got)
        self.assertIn('fuse', got['stop'])

    def test_with_the_fuse_off_it_walks_without_health(self):
        d = Drive({'hp_floor': 0})
        self.assertEqual(d.decide(read(hp=None, hp_max=None)), {'keys': [STEP]})


class Walking(unittest.TestCase):
    def test_it_walks_while_the_way_is_open_and_the_span_lasts(self):
        d = Drive({'span': 3})
        for i in range(3):
            self.assertEqual(d.decide(read(x=5 + i)), {'keys': [STEP]}, i)
        self.assertNotEqual(d.decide(read(x=8)), {'keys': [STEP]}, 'the span is spent: turn')

    def test_a_door_is_a_way_through_not_a_wall(self):
        """Measured on floor 5A: several cells have no exit but a door, and walking into one
        opens it. Treating doors as walls left autobattle standing still."""
        d = Drive()
        self.assertEqual(d.decide(read(facing=2, sides={0: 'wall', 1: 'wall', 2: 'door', 3: 'wall'})),
                         {'keys': [STEP]})

    def test_a_wall_ahead_turns_it_back_first(self):
        d = Drive()
        got = d.decide(read(facing=0, sides={0: 'wall', 1: 'wall', 2: 'open', 3: 'wall'}))
        self.assertEqual(got, {'keys': [TURN, TURN]}, 'heading 0 -> 2 is two left turns')

    def test_after_turning_it_does_not_count_the_same_cell_as_stuck(self):
        """A turn changes the heading, not the cell: the arrival must read as a change."""
        d = Drive()
        d.decide(read(facing=0, sides={0: 'wall', 1: 'wall', 2: 'open', 3: 'wall'}))
        after = read(facing=2, sides={0: 'wall', 1: 'wall', 2: 'open', 3: 'wall'})
        self.assertEqual(d.decide(after), {'keys': [STEP]})
        self.assertIsNone(d.g.stopped)

    def test_a_forced_turn_does_not_go_back_where_it_came_from(self):
        """Traced on floor 5A: always turning back first made the hero pace between two cells
        for three minutes while the other open sides of the crossing were never tried."""
        d = Drive()
        self.assertEqual(d.decide(read(x=14, y=3, facing=2)), {'keys': [STEP]})
        crossing = read(x=13, y=3, facing=2,
                        sides={0: 'open', 1: 'open', 2: 'wall', 3: 'open'})   # wall ahead: turn
        got = d.decide(crossing)
        self.assertTrue(got.get('keys'), 'a wall ahead means a turn')
        self.assertNotEqual(got['keys'], [TURN, TURN], 'east is where it just came from')
        self.assertIn(len(got['keys']), (1, 3), 'so it turns north or south instead')

    def test_an_avoided_cell_is_treated_as_a_wall(self):
        d = Drive({'avoid': [[6, 5]]})
        got = d.decide(read(x=5, y=5, facing=0, sides={0: 'open', 1: 'wall', 2: 'open', 3: 'wall'}))
        self.assertEqual(got, {'keys': [TURN, TURN]}, 'forward leads into the avoided cell')

    def test_boxed_in_it_turns_instead_of_hammering(self):
        d = Drive()
        self.assertEqual(d.decide(read(sides=WALLS)), {'keys': [TURN]})


class Stuck(unittest.TestCase):
    def test_it_does_not_press_while_the_hero_is_still_arriving(self):
        """Traced 2026-09-16: the turn issued a quarter of a second after the hero walked into a
        cell was swallowed by the game, and autobattle spent the rest of the run nudging the
        screen. A decision waits until nothing has changed for QUIET_FOR."""
        d = Drive()
        walking = read(x=14, y=3, facing=0, sides={0: 'wall', 1: 'wall', 2: 'open', 3: 'wall'})
        d.t += 1.0
        d.g.step(walking, now=d.t)               # first sight of the new cell
        d.t += 0.2
        self.assertEqual(d.g.step(walking, now=d.t), {},
                         'two readings in a row is a quarter second: too early to press')
        d.t += play_grind.QUIET_FOR
        self.assertEqual(d.g.step(walking, now=d.t), {'keys': [TURN, TURN]},
                         'once it has really stopped, turn')

    def test_a_walk_in_progress_is_not_called_stuck(self):
        """The hero was still walking when the script decided nothing had happened, went into
        its stuck branch and hammered return_key at a game that was fine."""
        d = Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(r), {'keys': [STEP]})
        for _ in range(6):                       # ~1.2 s: the walk has not finished yet
            self.assertEqual(d.tick(r), {}, 'it must wait, not press again')
        arrived = read(x=6, facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(arrived), {'keys': [STEP]}, 'and then walk on')
        self.assertIsNone(d.g.stopped)

    def test_a_swallowed_press_is_retried_before_the_screen_is_nudged(self):
        """Measured on floor 5A: the first movement key after the game loads a save does
        nothing, and the next one walks. Nudging with return_key cannot help a press that never
        arrived -- autobattle gave up on a walkable cell because it never tried the move again."""
        d = Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(r), {'keys': [STEP]})
        d.t += 3.0                                    # the move had its time and nothing changed
        self.assertEqual(d.decide(r), {'keys': [STEP]}, 'try the move again')
        arrived = read(x=6, facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(arrived), {'keys': [STEP]})
        self.assertIsNone(d.g.stopped)

    def test_coming_back_to_the_same_cell_later_is_not_stuck(self):
        """Walking a corridor returns the hero to the same cell and heading all the time.
        Comparing against one remembered state called that a dead end, and a perfectly walkable
        cell ended up in the avoid list."""
        d = Drive({'span': 1})
        self.assertEqual(d.decide(read(x=14, y=3, facing=0)), {'keys': [STEP]})
        for cell in (read(x=15, y=3, facing=0), read(x=14, y=3, facing=0)):   # there and back
            self.assertTrue(d.decide(cell).get('keys'), 'it keeps deciding')
        self.assertIsNone(d.g.stopped, 'a lap around the corridor is not a dead end')
        self.assertEqual(d.g.avoid, set(), 'and nothing may be written off as impassable')

    def test_a_menu_that_moves_its_own_cursor_ends_the_run_at_once(self):
        """Seen for real on floor 5A: the hero walked into his own room, the game opened the
        diary (save) menu, and from then on the arrow keys moved that menu's cursor while the
        hero stood still. Blind presses in a save menu are how the game was once crashed."""
        d = Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'}, cursor=(305, 91))
        self.assertEqual(d.decide(r), {'keys': [STEP]})
        d.t += 3.0
        got = d.decide(dict(r, cursor=(297, 91)))          # only the menu's cursor moved
        self.assertIn('stop', got)
        self.assertIn('menu', got['stop'])
        self.assertIn((5, 5), d.g.avoid, 'and the cell is remembered')

    def test_a_screen_that_eats_input_is_nudged_then_given_up_on(self):
        d = Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(r), {'keys': [STEP]})
        # ⚠️ Rounds, not a guess: a give-up costs the cutscene grace, then the move retries,
        # then the stuck limit. Counting them by hand made this test fail whenever any of the
        # three changed -- so allow plenty and assert the OUTCOME.
        for _ in range(play_grind.CUTSCENE_GRACE + play_grind.RETRY_MOVES + play_grind.STUCK_LIMIT + 8):
            d.t += 3.0
            d.decide(r, tries=4)
            if d.g.stopped:
                break
        self.assertIsNotNone(d.g.stopped, 'it must hand the game back, not keep pressing')
        self.assertIn('stuck at (5, 5)', d.g.stopped)
        self.assertIn((5, 5), d.g.avoid, 'and remember the cell')

    def test_a_moving_picture_means_a_cutscene_not_a_dead_end(self):
        d = Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        d.decide(r)
        moving = dict(r, frame_moving=True)
        for _ in range(play_grind.CUTSCENE_GRACE - 2):
            d.t += 3.0
            d.decide(moving, tries=4)
        self.assertIsNone(d.g.stopped, 'while the screen keeps changing it is progress')

    def test_an_animated_floor_cannot_keep_it_nudging_for_ever(self):
        """⚠️ The torches on floor 5A animate all the time, so "the picture is moving" is not
        proof of progress. Unlimited, that excuse kept autobattle pressing return_key for two
        minutes while the way ahead stood open (traced 2026-09-16)."""
        d = Drive()
        moving = dict(read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'}),
                      frame_moving=True)
        d.decide(moving)
        # ⚠️ Run until it gives up, with a generous cap -- counting the rounds by hand was wrong
        # twice (a decision costs one round, the settling after it another).
        for _ in range(80):
            d.t += 3.0
            d.decide(moving, tries=4)
            if d.g.stopped:
                break
        self.assertIsNotNone(d.g.stopped, 'it must hand the game back eventually')

    def test_a_nudge_is_not_remembered_as_the_move_to_retry(self):
        """Traced 2026-09-16: `retry the move` repeated whatever was pressed last, and the last
        press was the nudge itself -- so not one step was ever tried again."""
        d = Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(d.decide(r), {'keys': [STEP]})
        moving = dict(r, frame_moving=True)
        d.t += 3.0
        self.assertEqual(d.decide(moving), {'keys': [ATTACK]}, 'a cutscene gets a nudge')
        d.t += 3.0
        self.assertEqual(d.decide(r), {'keys': [STEP]}, 'and the MOVE is what gets retried')

    def test_a_reading_without_a_position_is_not_pressed_at(self):
        """The coordinates read (0,0) with no map for a moment after a save loads."""
        d = Drive()
        self.assertEqual(d.decide(read(x=None, y=None, facing=None, sides={})), {})
        self.assertIsNone(d.g._last_move)

    def test_a_reading_that_does_not_last_is_never_acted_on(self):
        """Right after a save loads the cell reads (0,0) with a map from nowhere, for well under
        a second. Acting on it sent the hero a blind keypress (traced 2026-09-16)."""
        d = Drive()
        garbage = read(x=0, y=0, facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'open'})
        for _ in range(4):                       # 0.8 s of garbage, then the truth
            self.assertEqual(d.tick(garbage), {}, 'too brief to be believed')
        real = read(x=14, y=3, facing=2, sides={0: 'wall', 1: 'wall', 2: 'open', 3: 'wall'})
        self.assertEqual(d.decide(real), {'keys': [STEP]})
        self.assertEqual(d.g._last_move, [STEP], 'and the blind press was never made')


class Pacing(unittest.TestCase):
    def test_it_does_not_decide_again_before_the_last_press_played_out(self):
        d = Drive()
        self.assertEqual(d.decide(read()), {'keys': [STEP]})
        d.t += 0.05
        self.assertEqual(d.g.step(read(x=6), now=d.t), {})
        d.t += play_grind.ACT_PAUSE
        self.assertEqual(d.g.step(read(x=6), now=d.t), {}, 'one reading is not stillness')
        self.assertEqual(d.decide(read(x=6)), {'keys': [STEP]})

    def test_at_higher_speed_it_waits_proportionally_less(self):
        slow, fast = Drive(), Drive()
        r = read(facing=0, sides={0: 'open', 1: 'wall', 2: 'wall', 3: 'wall'})
        self.assertEqual(slow.decide(r), {'keys': [STEP]})
        self.assertEqual(fast.decide(dict(r, speed=4)), {'keys': [STEP]})
        self.assertLess(fast.t, slow.t, 'at x4 the same decision comes sooner')


if __name__ == '__main__':
    unittest.main()
