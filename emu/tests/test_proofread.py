"""The cockpit's proofreading path: identify the line on screen, rewrite it in text/*.json.

Nothing here starts an emulator. What is checked is the part that can silently do damage:
which source line a screen line is attributed to, and under what conditions an edit is
written at all. The rule the tests encode: the cockpit writes ONLY into the proofreader's
JSON, only for a line it can identify beyond doubt, and only if tools/check.py is silent.
"""
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import types
import unittest

EMU = pathlib.Path(__file__).resolve().parent.parent
ROOT = EMU.parent
sys.path.insert(0, str(EMU))
sys.path.insert(0, str(ROOT / 'tools'))

import play       # noqa: E402
import textsrc    # noqa: E402

LIVE = (ROOT / 'en').is_dir() and (ROOT / 'text').is_dir()
FP = lambda s: hashlib.blake2b(s.encode(), digest_size=4).hexdigest()   # noqa: E731


@unittest.skipUnless(LIVE, 'needs the working repo (en/ and text/)')
class Identify(unittest.TestCase):
    """The screen line -> the form in en/*.rkt, with the id the proofreader's JSON uses."""

    @classmethod
    def setUpClass(cls):
        cls.idx = textsrc.index()

    def test_the_innkeeper_line_is_yado_form_zero(self):
        lines = ['[Innkeeper]: Good morning... Clean your room before you', "leave, won't you."]
        got = textsrc.candidates(lines, idx=self.idx)
        self.assertTrue(got, 'the line must be found')
        self.assertEqual((got[0]['file'], got[0]['line'], got[0]['id']), ('YADO.MES', 71, 'YADO.MES#0'))
        self.assertTrue(got[0]['exact'])

    def test_the_id_addresses_the_same_text_in_the_proofreader_copy(self):
        """⚠️ The ordinal must count EVERY form, including the ones the index skips -- that is
        what tools/export_text.py numbers by, and an edit lands by that id."""
        for lines in (['[Innkeeper]: Good morning... Clean your room before you', "leave, won't you."],
                      ['[Innkeeper]: Good morning... I\'ll clean the room for', 'you.']):
            c = textsrc.candidates(lines, idx=self.idx)[0]
            rows = json.loads((ROOT / 'text' / f'{c["file"]}.json').read_text(encoding='utf-8'))
            row = next(r for r in rows if r['id'] == c['id'])
            self.assertEqual(row['was'], FP(c['form']), c['id'])

    def test_a_shared_line_offers_every_source_not_one(self):
        """Regression: the old index kept the first form per key and called the answer exact --
        an editor would have written the correction into someone else's line."""
        shared = [k for k, v in self.idx.items() if len(v) > 1]
        self.assertTrue(shared, 'this game has colliding keys; if it stops having them, drop this test')
        key = max(shared, key=lambda k: len(self.idx[k]))
        got = textsrc.candidates([key.replace('\x01', 'Astral')], idx=self.idx)
        self.assertGreater(len(got), 1)
        self.assertEqual(len(got), len(self.idx[key]))
        self.assertEqual(len({(g['file'], g['line']) for g in got}), len(got))

    def test_the_scene_is_preferred_among_equals(self):
        key = max((k for k, v in self.idx.items() if len({e[0] for e in v}) > 1),
                  key=lambda k: len(self.idx[k]), default=None)
        if key is None:
            self.skipTest('no key shared across files')
        other = self.idx[key][-1][0]
        got = textsrc.candidates([key.replace('\x01', 'Astral')], idx=self.idx, prefer=other)
        self.assertEqual(got[0]['file'], other)

    def test_locate_still_answers_the_old_way(self):
        """mapper.py and the QA dashboard call locate(); its contract must not change."""
        got = textsrc.locate(['[Innkeeper]: Good morning... Clean your room before you',
                              "leave, won't you."], idx=self.idx)
        # what mapper.py reads; extra fields are fine, missing ones are not
        self.assertLessEqual({'file', 'line', 'form', 'exact'}, set(got))
        self.assertEqual((got['file'], got['line'], got['count']), ('YADO.MES', 71, 1))


@unittest.skipUnless(LIVE, 'needs the working repo (en/ and text/)')
class WithoutSources(unittest.TestCase):
    """A proofreader's machine has no `en/*.rkt` -- only the pack, and sometimes not even that."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ww-pack-test.'))
        (self.tmp / 'en').mkdir()
        shutil.copytree(ROOT / 'text', self.tmp / 'text')
        self._en, self._text = textsrc.EN, textsrc.TEXT
        self._idx, self._pack = textsrc.INDEX, textsrc.PACK_INDEX
        textsrc.EN, textsrc.TEXT = self.tmp / 'en', self.tmp / 'text'
        textsrc.INDEX, textsrc.PACK_INDEX = self.tmp / 'i.json', self.tmp / 'p.json'

    def tearDown(self):
        textsrc.EN, textsrc.TEXT = self._en, self._text
        textsrc.INDEX, textsrc.PACK_INDEX = self._idx, self._pack
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_pack_alone_identifies_the_line(self):
        self.assertEqual(textsrc.sources(), 'pack')
        lines = ['[Innkeeper]: Good morning... Clean your room before you', "leave, won't you."]
        got = textsrc.candidates(lines)
        self.assertTrue(got)
        self.assertEqual((got[0]['id'], got[0]['kind'], got[0]['exact']), ('YADO.MES#0', 'pack', True))
        self.assertIsNone(got[0]['line'], 'the pack has no source line numbers, and says so')

    def test_a_name_in_the_line_is_matched_through_the_placeholder(self):
        rows = json.loads((self.tmp / 'text/TOWN.MES.json').read_text())
        row = next(r for r in rows if '{0}' in r['en'] and r['screen'])
        on_screen = [l.replace('x' * 6, 'Astral') for l in row['screen']]
        got = textsrc.candidates(on_screen, names=('Astral', 'Pollux'))
        self.assertIn(row['id'], [g['id'] for g in got])

    def test_with_neither_sources_nor_pack_nothing_is_found_and_nothing_breaks(self):
        shutil.rmtree(self.tmp / 'text')
        (self.tmp / 'text').mkdir()
        self.assertIsNone(textsrc.sources())
        self.assertEqual(textsrc.index(), {})
        self.assertEqual(textsrc.candidates(['whatever is on screen']), [])
        self.assertIsNone(textsrc.locate(['whatever is on screen']))


@unittest.skipUnless(LIVE, 'needs the working repo (en/ and text/)')
class Edit(unittest.TestCase):
    """What the cockpit will and will not write."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ww-text-test.'))
        shutil.copy2(ROOT / 'text/YADO.MES.json', self.tmp / 'YADO.MES.json')
        self._text, play.TEXT = play.TEXT, self.tmp
        self._log, play.EDIT_LOG = play.EDIT_LOG, self.tmp / 'edits.jsonl'
        # ⚠️ These tests are about what edit_line will and will not write -- not about whether
        # the working pack happens to be edited at this moment. Normalise the COPY so its entry
        # counts as freshly exported; without this every case here fails as "the source line
        # changed after the export" the moment someone corrects that line in the real text/
        # (which is exactly what a proofreader does, and it happened: 2026-09-16).
        rows = json.loads((self.tmp / 'YADO.MES.json').read_text(encoding='utf-8'))
        rows[0]['was'] = FP(rows[0]['en'])
        (self.tmp / 'YADO.MES.json').write_text(json.dumps(rows, ensure_ascii=False),
                                                encoding='utf-8')
        self.form = rows[0]['en']
        self.hub = types.SimpleNamespace(
            text={'candidates': [{'file': 'YADO.MES', 'line': 71, 'ordinal': 0, 'id': 'YADO.MES#0',
                                  'exact': True, 'editable': True, 'form': self.form,
                                  'en': self.form}]},
            event=lambda *a, **k: None, _text_seq=0, _fingerprint=play.Hub._fingerprint)
        self.edit = lambda i, t: play.Hub.edit_line(self.hub, i, t)

    def tearDown(self):
        play.TEXT, play.EDIT_LOG = self._text, self._log
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self):
        return json.loads((self.tmp / 'YADO.MES.json').read_text(encoding='utf-8'))

    def test_a_good_edit_lands_in_the_json_and_nowhere_else(self):
        new = "[Innkeeper]: Good morning... Do tidy your room before\nyou leave."
        r = self.edit('YADO.MES#0', new)
        self.assertTrue(r['ok'], r)
        row = self.rows()[0]
        self.assertEqual(row['en'], new)
        self.assertEqual(row['was'], FP(self.form), 'the fingerprint of the ORIGINAL must stay')
        self.assertEqual(row['id'], 'YADO.MES#0')
        self.assertEqual(len(self.rows()), len(json.loads((ROOT / 'text/YADO.MES.json').read_text())))
        self.assertEqual(json.loads(play.EDIT_LOG.read_text().strip())['id'], 'YADO.MES#0')

    def test_a_line_the_game_cannot_draw_is_refused(self):
        r = self.edit('YADO.MES#0', '[Innkeeper]: Good morning~ \\ tilde and backslash')
        self.assertFalse(r['ok'])
        self.assertTrue(r.get('problems'))
        self.assertEqual(self.rows()[0]['en'], self.form, 'nothing may be written on refusal')

    def test_a_renamed_speaker_tag_is_refused(self):
        """Who is speaking is not the proofreader's to change.

        ⚠️ Until 2026-09-16 nothing on this path noticed: tools/check.py promised the check in
        its own table ("dropped, renamed or lower-cased") and only ever caught a tag split
        across two lines, so a rename went into text/*.json in silence. The class was guarded
        on the OTHER path -- tools/audit.py over the finished en/*.rkt -- which is no help to
        someone editing from inside the game.
        """
        for bad in ('[Innkeper]', '[innkeeper]'):
            r = self.edit('YADO.MES#0', self.form.replace('[Innkeeper]', bad, 1))
            self.assertFalse(r['ok'], f'{bad} was accepted')
            self.assertTrue(any('[Name]:' in p for p in r.get('problems', [])), r)
            self.assertEqual(self.rows()[0]['en'], self.form, 'nothing may be written on refusal')

    def test_dropping_the_speaker_tag_is_refused(self):
        r = self.edit('YADO.MES#0', self.form.split(': ', 1)[1])
        self.assertFalse(r['ok'])
        self.assertTrue(any('[Name]:' in p for p in r.get('problems', [])), r)

    def test_a_line_too_wide_for_the_window_is_refused(self):
        r = self.edit('YADO.MES#0', '[Innkeeper]: ' + 'Good morning and welcome to the inn ' * 3)
        self.assertFalse(r['ok'])
        self.assertTrue(any('layout' in p for p in r.get('problems', [])), r)
        self.assertEqual(self.rows()[0]['en'], self.form)

    def test_an_id_that_is_not_on_screen_is_refused(self):
        r = self.edit('YADO.MES#7', 'whatever')
        self.assertFalse(r['ok'])
        self.assertIn('on screen', r['why'])

    def test_the_same_line_in_several_places_is_corrected_in_all_of_them(self):
        """⚠️ 12% of the game's screen lines are rendered by more than one entry, and half of
        those sit in one file -- neither the scene nor a picker can say which occurrence is on
        screen. When they hold the same text the question does not need answering."""
        # ⚠️ An id that cannot already be in the file: YADO.MES#77 is a real entry, and looking
        # a twin up by that id found the REAL row, whose fingerprint belongs to another line.
        twin_id = 'YADO.MES#9999'
        rows = self.rows()
        rows.append({**rows[0], 'id': twin_id})
        (self.tmp / 'YADO.MES.json').write_text(json.dumps(rows, ensure_ascii=False))
        self.hub.text['candidates'].append({**self.hub.text['candidates'][0],
                                            'id': twin_id, 'line': 900})
        new = "[Innkeeper]: Good morning... Tidy your room before you\nleave, won't you."
        r = self.edit('YADO.MES#0', new)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['places'], 2)
        got = {row['id']: row['en'] for row in self.rows()}
        self.assertEqual(got['YADO.MES#0'], new)
        self.assertEqual(got[twin_id], new, 'every copy of the line must be corrected')

    def test_places_that_hold_different_text_are_refused(self):
        """One correction must not be spread over entries that are not the same line."""
        twin_id = 'YADO.MES#9999'          # must not collide with a real entry
        rows = self.rows()
        rows.append({**rows[0], 'id': twin_id, 'en': rows[0]['en'] + ' Do come again.'})
        (self.tmp / 'YADO.MES.json').write_text(json.dumps(rows, ensure_ascii=False))
        self.hub.text['candidates'].append({**self.hub.text['candidates'][0],
                                            'id': twin_id, 'line': 900})
        r = self.edit('YADO.MES#0', 'still a good line')
        self.assertFalse(r['ok'])
        self.assertIn('different text', r['why'])
        self.assertEqual(self.rows()[0]['en'], self.form, 'nothing may be written on refusal')

    def test_an_edit_on_a_stale_export_is_refused(self):
        """tools/import_text.py would refuse it later: better to say so before it is written."""
        self.hub.text['candidates'][0]['form'] = self.form + ' (the source moved on)'
        r = self.edit('YADO.MES#0', 'a fine replacement line')
        self.assertFalse(r['ok'])
        self.assertIn('changed', r['why'])
        self.assertEqual(self.rows()[0]['en'], self.form)

    def test_in_pack_mode_the_json_is_the_source_and_an_edited_entry_stays_editable(self):
        """On a proofreader's machine `form` IS the pack's current text; fingerprinting it
        against `was` would refuse every second edit."""
        rows = self.rows()
        rows[0]['en'] = 'a line edited in an earlier session'
        (self.tmp / 'YADO.MES.json').write_text(json.dumps(rows, ensure_ascii=False))
        c = self.hub.text['candidates'][0]
        c.update(kind='pack', line=None, form=rows[0]['en'])
        r = self.edit('YADO.MES#0', '[Innkeeper]: Morning. Tidy the room before you go.')
        self.assertTrue(r['ok'], r)
        # ⚠️ Against what setUp normalised the copy to -- NOT against the working pack, which a
        # proofreader may have edited five seconds ago (that made this whole class fail once).
        self.assertEqual(self.rows()[0]['was'], FP(self.form),
                         'the export fingerprint stays, whatever the entry now says')

    def test_no_pack_file_at_all_is_a_plain_refusal(self):
        (self.tmp / 'YADO.MES.json').unlink()
        r = self.edit('YADO.MES#0', 'anything')
        self.assertFalse(r['ok'])
        self.assertIn('cannot read', r['why'])

    def test_writing_the_same_text_is_not_an_edit(self):
        r = self.edit('YADO.MES#0', self.form)
        self.assertFalse(r['ok'])
        self.assertIn('nothing changed', r['why'])


if __name__ == '__main__':
    unittest.main()
