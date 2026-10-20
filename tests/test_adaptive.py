"""実ログは読み取りのみ。合成回答・保存試験はTemporaryDirectoryに限定。"""
import copy
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import adaptive_learning as a


class AdaptiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = {q['id']: q for q in json.loads((ROOT / '問題データ/questions.json').read_text())}
        cls.events = a.load_events(ROOT)
        cls.model = a.rebuild(cls.events, cls.bank)
        cls.examples = json.loads((ROOT / 'tests/fixtures/adaptive_examples.json').read_text())

    def test_A_correct_with_gap_targets_structure(self):
        c = self.model['concepts']['BIO-0001/phospholipid']
        self.assertEqual(c['observations'][0]['score'], 1)
        self.assertEqual(c['state'], '理解不足')
        p = a.plan(c, dt.date(2026, 10, 20))
        self.assertEqual(p['mode'], '弱点補強')
        self.assertIn('Q-BIO-0001-1', p['avoid_question_ids'])
        self.assertNotIn(self.examples[0]['id'], p['avoid_question_ids'])

    def test_B_returns_to_prerequisite(self):
        c = self.model['concepts']['BIO-0001/protein']
        self.assertEqual(c['observations'][0]['score'], 0)
        self.assertEqual(a.plan(c, dt.date(2026, 10, 20))['target'], 'BIO-0001/biomolecule_classes')

    def test_C_initial_gap_and_later_report_coexist(self):
        c = self.model['concepts']['BIO-0001/glycan']
        self.assertIn('全然しらない', c['observations'][0]['free_response'])
        self.assertTrue(c['post_explanation_report'])
        self.assertEqual(c['state'], '定着確認待ち')
        self.assertFalse(c['retention_confirmed'])

    def test_D_application_before_due(self):
        c = self.model['concepts']['BIO-0001/bilayer']
        p = a.plan(c, dt.date(2026, 10, 20))
        self.assertEqual(p['mode'], '応用')
        self.assertTrue(p['target'].endswith('/application'))

    def test_E_original_text_unanswered_alternative_separate(self):
        rows = [r for r in self.model['attempts'] if r['unit_id'] == 'BIO-0001']
        self.assertNotIn('Q-BIO-0001-5', [r['question_id'] for r in rows])
        alt = next(r for r in rows if r['question_id'].endswith('-ALT01'))
        self.assertTrue(alt['provisional'])
        self.assertIsNone(alt['score'])
        self.assertEqual(alt['original_question_id'], 'Q-BIO-0001-5')
        self.assertEqual(sum(r['score'] for r in rows if r['score'] is not None), 3)

    def test_F_sources_and_no_fabricated_validation(self):
        for q in self.examples:
            self.assertTrue(a.validate_question(q))
            self.assertEqual(q['validation']['status'], 'text_checked')
            self.assertEqual(q['origin'], 'original_from_sources')
        q = copy.deepcopy(self.examples[0]); q['sources'][0]['status'] = 'unavailable'
        with self.assertRaises(ValueError): a.validate_question(q)
        self.assertEqual(a.decide(self.model, 'BIO-0001', dt.date(2026, 10, 20), False)['status'], 'source_unavailable')

    def test_G_append_readback_idempotency_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp) / '演習ログ/記録'; d.mkdir(parents=True); (d / 'TEST_ONLY').touch()
            e = a.make_event('adaptive_question', dict(session_id='test', unit_id='BIO-0001', question=self.examples[0]), 'test-g', 1, '2026-10-20T15:00:00+09:00')
            a.append_for_test(d, e)
            self.assertEqual(a.append_for_test(d, e), e)
            files = list(d.glob('*.md')); self.assertEqual(len(files), 1)
            reread = json.loads(re.search(r'```json\n(.*?)\n```', files[0].read_text(), re.S).group(1))
            self.assertEqual(reread, e)
            conflicting = a.make_event('adaptive_question', dict(session_id='other', unit_id='BIO-0001', question=self.examples[0]), 'test-g', 2, e['at'])
            with self.assertRaises(ValueError): a.append_for_test(d, conflicting)
            wrong_seq = a.make_event('session_pause', dict(session_id='test'), 'test-seq', 1, e['at'])
            with self.assertRaises(ValueError): a.append_for_test(d, wrong_seq)
            q = self.examples[0]
            p = dict(session_id='test', unit_id='BIO-0001', question=q, concept_ids=q['concept_ids'], answer=q['answer'], score=1,
                     free_response='正答できたが理由は説明できない', additional_questions=[], review_need=1)
            attempt = a.make_event('adaptive_attempt', p, 'test-g-attempt', 2, e['at'])
            a.append_for_test(d, attempt)
            reread_model = a.rebuild(a.load_events(tmp), self.bank)
            next_plan = a.decide(reread_model, 'BIO-0001', dt.date(2026, 10, 20))
            self.assertEqual(next_plan['mode'], '弱点補強')
            self.assertEqual(next_plan['next_review'], '2026-10-21')
            self.assertEqual(len(list(d.glob('*.md'))), 2)

    def test_all_protected_blobs_exact(self):
        manifest = json.loads((ROOT / 'tests/fixtures/protected_blobs.json').read_text())
        for path, sha in manifest.items():
            b = (ROOT / path).read_bytes()
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest(), sha, path)

    def test_master_active_units_and_bank(self):
        master = json.loads((ROOT / '学習マスター/master.json').read_text())
        self.assertEqual(len(master['units']), 1344)
        self.assertEqual(len({u['id'] for u in master['units']}), 1344)
        self.assertEqual(len(self.bank), 45)

    def test_legacy_attempts_not_counted_twice(self):
        rows = self.model['attempts']
        keys = [(r['session_id'], r['question_id']) for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sum(r['unit_id'] == 'BIO-0001' for r in rows), 5)
        self.assertEqual(sum(r['unit_id'] == 'PHA1-0001' for r in rows), 5)

    def test_unknown_legacy_alternative_not_invented(self):
        r = next(r for r in self.model['attempts'] if '@alternative' in r['question_id'])
        self.assertIsNone(r['question'])
        self.assertTrue(r['provisional'])

    def test_unlearned_and_due(self):
        p = a.decide(self.model, 'PHY-0002', dt.date(2026, 10, 20))
        self.assertEqual(p['mode'], '基礎確認')
        c = self.model['concepts']['BIO-0001/bilayer']
        self.assertEqual(a.plan(c, dt.date(2026, 10, 30))['mode'], '定着確認')

    def test_subjective_five_does_not_erase_gap(self):
        c = copy.deepcopy(self.model['concepts']['BIO-0001/phospholipid']); c['review_need'] = 5
        self.assertEqual(a.plan(c, dt.date(2026, 10, 20))['next_review'], '2026-10-21')

    def test_question_report_excludes_weakness(self):
        e = dict(id='test-report', at='2026-10-20T15:00:00+09:00', sequence=99, type='question_report', payload=dict(question_id='Q-BIO-0001-4', version=1))
        m = a.rebuild(self.events + [e], self.bank)
        self.assertNotIn('BIO-0001/protein', m['concepts'])

    def test_adaptive_attempt_and_observation_roundtrip(self):
        q = self.examples[0]
        payload = dict(session_id='test-roundtrip', unit_id='BIO-0001', question=q, concept_ids=q['concept_ids'], answer=q['answer'], score=1, free_response='理由はまだ説明できない', additional_questions=[], review_need=2)
        event = a.make_event('adaptive_attempt', payload, 'test-roundtrip', 1, '2026-10-21T10:00:00+09:00')
        obs = a.make_event('learner_observation', dict(session_id='test-roundtrip', unit_id='BIO-0001', question_id=q['id'], version=1, interpretation=dict(post_explanation_understood=True)), 'test-observation', 2, '2026-10-21T10:05:00+09:00')
        c = a.rebuild([event, obs], self.bank)['concepts'][q['concept_ids'][0]]
        self.assertEqual(c['state'], '定着確認待ち')
        self.assertIn('説明できない', c['observations'][0]['free_response'])
        self.assertFalse(c['retention_confirmed'])

    def test_retention_requires_delay_different_question_and_reason(self):
        q = self.examples[0]
        p = dict(session_id='test-retention', unit_id='BIO-0001', question=q, concept_ids=q['concept_ids'], answer=q['answer'], score=1, free_response='', additional_questions=[], review_need=4, interpretation=dict(reason_verified=True, depth='仕組みの理解'))
        one = a.make_event('adaptive_attempt', p, 'test-one', 1, '2026-10-20T10:00:00+09:00')
        p2 = copy.deepcopy(p); p2['question']['id'] = 'DEMO-NEW'; p2['session_id'] = 'test-new'
        immediate = a.make_event('adaptive_attempt', p2, 'test-two', 2, '2026-10-20T11:00:00+09:00')
        self.assertFalse(a.rebuild([one, immediate], self.bank)['concepts'][q['concept_ids'][0]]['retention_confirmed'])
        later = a.make_event('adaptive_attempt', p2, 'test-later', 2, '2026-10-28T10:00:00+09:00')
        self.assertTrue(a.rebuild([one, later], self.bank)['concepts'][q['concept_ids'][0]]['retention_confirmed'])

    def test_hash_failure_is_not_empty_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp) / '演習ログ/記録'; d.mkdir(parents=True)
            e = copy.deepcopy(self.events[0]); e['payload']['changed'] = True
            (d / 'broken.md').write_text('```json\n' + json.dumps(e) + '\n```\n')
            with self.assertRaises(ValueError): a.load_events(tmp)

    def test_reject_inconsistent_grade_and_production_write(self):
        q = self.examples[0]
        with self.assertRaises(ValueError):
            a.make_event('adaptive_attempt', dict(question=q, answer=q['answer'], score=0), 'test-bad-score', 1, '2026-10-20T10:00:00+09:00')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): a.append_for_test(tmp, self.events[0])


if __name__ == '__main__':
    unittest.main()
