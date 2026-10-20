import subprocess,copy,datetime as dt,hashlib,json,pathlib,shutil,sys,tempfile,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
from engine import Study,JST,StoreLock
from cli import run as cli_run
from server import create_server
class SystemTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
  for name in ['学習マスター','問題データ']:shutil.copytree(ROOT/name,self.root/name)
  self.s=Study(self.root)
 def tearDown(self):self.tmp.cleanup()
 def start_answer(self,uid,rid='a'):
  st=self.s.start(uid,'start-'+rid);bank=self.s.questions();answers={q['id']:('説明した内容'if q['type']=='text'else bank[q['id']]['answer'])for q in st['questions']};r=self.s.submit(st['session_id'],answers,'submit-'+rid);return st,answers,r
 def test_master_integrity_973_and_all_ids(self):
  units=self.s.master['units'];archive=self.s.master['archived_units'];self.assertEqual(len(units),1344);self.assertEqual(len(archive),44);self.assertEqual(len({u['id']for u in units+archive}),1388);self.assertEqual(len(set(i for u in units for i in u['source_items'])),973);self.assertEqual(sum(self.s.master['counts'].values()),1344)
  by={u['id']:u for u in units+archive};self.assertEqual(sum(len(g['new_ids'])for g in self.s.mapping),75)
  for g in self.s.mapping:
   for uid in g['new_ids']:
    self.assertEqual(by[uid]['original_ids'],g['old_ids']);self.assertEqual(by[uid]['initial']['理解度'],'未評価')
   for uid in g['old_ids']:self.assertIn(uid,by)
  self.assertTrue(by['CHE-0087']['active']);self.assertTrue(by['CHE-0087']['review_required'])
 def test_nine_subjects_full_cycle_reload(self):
  bank=self.s.questions();units=list(dict.fromkeys(q['unit_id']for q in bank.values()));self.assertEqual(len(units),9)
  for i,uid in enumerate(units):
   with self.subTest(uid=uid):
    st,ans,r=self.start_answer(uid,str(i));self.assertEqual(len(st['questions']),5);self.assertEqual(r['pending_count'],1);self.assertNotIn('answer',st['questions'][0]);self.assertNotIn('explanation',st['questions'][0])
    scores={q['id']:1 for q in st['questions']if q['type']=='text'};self.s.assess(st['session_id'],scores,'assess-'+str(i));self.s=Study(self.root);h=self.s.history(uid);self.assertEqual(len(h['sessions']),1);r=h['sessions'][0];self.assertEqual(r['ratio'],1);self.assertEqual(r['pending_count'],0)
    rev=next(r for r in self.s.reviews()if r['unit_id']==uid);self.assertEqual(rev['understanding'],3);self.assertEqual(rev['study_count'],1);self.assertEqual(rev['question_count'],5);self.assertEqual(rev['next_review'],(dt.date.fromisoformat(rev['last_study'])+dt.timedelta(days=7)).isoformat())
  self.s.export_views();self.assertTrue((self.root/'復習管理/復習予定.md').exists());self.assertEqual(self.s.state()['summary']['learned'],9)
 def test_submit_idempotency_and_conflicting_answers(self):
  st,ans,r=self.start_answer('CHE-0004');n=len(self.s.events());self.s.submit(st['session_id'],ans,'another-retry');self.assertEqual(len(self.s.events()),n)
  bad=copy.deepcopy(ans);q=st['questions'][0];bad[q['id']]=[(ans[q['id']][0]+1)%4]
  with self.assertRaises(ValueError):self.s.submit(st['session_id'],bad,'changed')
 def test_review_low_partial_pending_and_stable(self):
  st,ans,r=self.start_answer('PHT-0002');self.assertEqual(self.s.reviews()[0]['understanding'],'未評価');scores={q['id']:0 for q in st['questions']if q['type']=='text'};self.s.assess(st['session_id'],scores,'score');self.assertEqual(self.s.reviews()[0]['understanding'],3)
  st2=self.s.start('PHT-0002','start2');bad={q['id']:('答え'if q['type']=='text'else [(self.s.questions()[q['id']]['answer'][0]+1)%4])for q in st2['questions']};self.s.submit(st2['session_id'],bad,'sub2');self.s.assess(st2['session_id'],{q['id']:0 for q in st2['questions']if q['type']=='text'},'score2');self.assertEqual(self.s.reviews()[0]['understanding'],1);self.assertTrue(self.s.reviews(today=dt.date(2100,1,1))[0]['due'])
 def test_mastery_requires_spacing_and_full_coverage(self):
  uid='CHE-0004'
  def finish(label,day):
   with patch('engine.now',return_value=day+'T10:00:00+09:00'):
    st,ans,r=self.start_answer(uid,label);self.s.assess(st['session_id'],{q['id']:1 for q in st['questions']if q['type']=='text'},'score-'+label)
  finish('first','2026-01-01');finish('second','2026-01-10');self.assertEqual(self.s.reviews()[0]['understanding'],3)
  qs=[copy.deepcopy(q)for q in self.s.questions().values()if q['unit_id']==uid]
  for q in qs:q['version']=2;q['coverage']='unit'
  self.s.import_questions(qs,'区画全体の設計確認済みのテスト用設定','unit-coverage')
  finish('third','2026-01-11');self.assertEqual(self.s.reviews()[0]['understanding'],3)
  finish('fourth','2026-01-19');r=self.s.reviews()[0];self.assertEqual(r['understanding'],4);self.assertEqual(r['next_review'],'2026-02-09')
 def test_partial_review_and_reassessment_is_append_only(self):
  uid='CHE-0004';st=self.s.start(uid,'start');bank=self.s.questions();answers={q['id']:('説明'if q['type']=='text'else bank[q['id']]['answer'])for q in st['questions']}
  choice=[q for q in st['questions']if q['type']!='text'];answers[choice[0]['id']]=[(answers[choice[0]['id']][0]+1)%4];self.s.submit(st['session_id'],answers,'submit');textid=next(q['id']for q in st['questions']if q['type']=='text');self.s.assess(st['session_id'],{textid:0},'score1');self.assertEqual(self.s.reviews()[0]['understanding'],2)
  before=[p.read_bytes()for p in sorted(self.s.logdir.glob('*.md'))];self.s.assess(st['session_id'],{textid:1},'score2');self.assertEqual(self.s.reviews()[0]['understanding'],3);self.assertEqual([p.read_bytes()for p in sorted(self.s.logdir.glob('*.md'))][:len(before)],before)
 def test_report_invalidates_past_future_and_preserves_bytes(self):
  st,ans,r=self.start_answer('BIO-0001');paths=list((self.root/'演習ログ/記録').glob('*.md'));before={p.name:p.read_bytes()for p in paths};q=st['questions'][0];self.s.report(q['id'],q['version'],'解答が疑わしい','report1');r=self.s.result(st['session_id']);self.assertTrue(r['items'][0]['void']);self.assertEqual(r['valid_count'],4)
  self.assertTrue(all((self.root/'演習ログ/記録'/p).read_bytes()==b for p,b in before.items()));self.assertEqual(len(self.s.start('BIO-0001','newstart')['questions']),4)
  qnext=copy.deepcopy(self.s.questions()[q['id']]);qnext['version']=2;qnext['correction_history']=[dict(from_version=1,reason='訂正')];self.s.import_questions([qnext],'訂正版','correction');self.assertEqual(len(self.s.start('BIO-0001','again')['questions']),5);self.assertTrue(self.s.result(st['session_id'])['items'][0]['void'])
 def test_report_before_submit_can_skip_void_answer(self):
  st=self.s.start('LAW-0010','start');qid=st['questions'][0]['id'];self.s.report(qid,1,'誤記','report');ans={q['id']:('自由記述'if q['type']=='text'else self.s.questions()[q['id']]['answer'])for q in st['questions']};ans[qid]=[];r=self.s.submit(st['session_id'],ans,'sub');self.assertEqual(r['valid_count'],4)
 def test_all_void_excludes_learning(self):
  st,ans,r=self.start_answer('LAW-0010')
  for i,q in enumerate(st['questions']):self.s.report(q['id'],1,'無効','report-'+str(i))
  self.assertEqual(self.s.state()['summary']['learned'],0);self.assertEqual(self.s.reviews(),[]);self.assertEqual(self.s.result(st['session_id'])['valid_count'],0)
 def test_legacy_history_readable_not_duplicated(self):
  g=self.s.mapping[0];uid=g['new_ids'][0];old=g['old_ids'][0];q=copy.deepcopy(next(iter(self.s.questions().values())));q['unit_id']=old
  start=self.s.append('start',{'unit_id':old,'questions':[q]},'legacy-start');self.s.append('submit',{'unit_id':old,'session_id':start['id'],'answers':{q['id']:q['answer']}},'legacy-sub')
  h=Study(self.root).history(uid);self.assertIn(old,[r['unit_id']for r in h['baseline_records']]);self.assertEqual(len(h['sessions']),1);self.assertEqual(h['sessions'][0]['unit_id'],old);self.assertNotIn(uid,[r['unit_id']for r in self.s.reviews()]);self.assertEqual(self.s.archive[old]['initial']['学習回数'],'0')
 def test_question_validation_and_draft_exclusion(self):
  q=copy.deepcopy(next(iter(self.s.questions().values())));q['id']='new-question';q['sources'][0]['pdf_pages']='99999'
  with self.assertRaises(ValueError):self.s.import_questions([q],'追加','bad1')
  q['sources'][0]['pdf_pages']=self.s.units[q['unit_id']]['pdf_pages'];q['validation']['status']='draft';self.s.import_questions([q],'下書き','draft');self.assertNotIn('new-question',[x['id']for x in self.s.start(q['unit_id'],'start')['questions']])
  q['id']='duplicate';q['version']=2
  with self.assertRaises(ValueError):self.s.import_questions([q],'追加','bad2')
 def test_corrupt_log_fails_without_resetting(self):
  (self.root/'演習ログ/記録/broken.md').write_text('broken')
  with self.assertRaises(ValueError):Study(self.root)
 def test_master_index_drift_detected(self):
  with open(self.root/'学習マスター/青本_学習マスター_v1.2.md','a')as f:f.write('changed')
  with self.assertRaises(ValueError):Study(self.root)
 def test_cli_learning_flow(self):
  qs=[q for q in self.s.questions().values()if q['unit_id']=='CHE-0004'];values=[]
  for q in qs:values.append('説明した'if q['type']=='text'else ','.join(str(i+1)for i in q['answer']))
  values.append('1');it=iter(values);output=[];r=cli_run(self.s,'CHE-0004',lambda _:next(it),output.append);self.assertEqual(r['ratio'],1);self.assertTrue(any('次回復習' in v for v in output));self.assertEqual(len(Study(self.root).history('CHE-0004')['sessions']),1)
 def test_second_process_store_lock(self):
  lock=StoreLock(self.root)
  try:
   with self.assertRaises(ValueError):StoreLock(self.root)
   child=subprocess.run([sys.executable,'-c',"import sys;sys.path.insert(0,sys.argv[1]);from engine import StoreLock;StoreLock(sys.argv[2])",str(ROOT/'app'),str(self.root)],capture_output=True,text=True);self.assertNotEqual(child.returncode,0)
  finally:lock.close()
  again=StoreLock(self.root);again.close()
 def test_http_end_to_end_and_origin_rejection(self):
  server=create_server(self.root,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
  try:
   def get(path):return json.load(urllib.request.urlopen(base+path))
   token=get('/api/state')['token']
   def post(path,data,origin=None):
    headers={'Content-Type':'application/json','X-Study-Token':token}
    if origin:headers['Origin']=origin
    req=urllib.request.Request(base+path,json.dumps(data).encode(),headers=headers);return json.load(urllib.request.urlopen(req))
   st=post('/api/start',dict(unit_id='PRC-0002',request_id='start'));answers={q['id']:('回答'if q['type']=='text'else self.s.questions()[q['id']]['answer'])for q in st['questions']};result=post('/api/submit',dict(session_id=st['session_id'],answers=answers,request_id='submit'));self.assertEqual(result['graded_count'],4);self.assertEqual(len(get('/api/history?unit_id=PRC-0002')['sessions']),1)
   with self.assertRaises(urllib.error.HTTPError)as e:post('/api/start',dict(unit_id='PRC-0002',request_id='cross'),'https://unrelated.invalid')
   self.assertEqual(e.exception.code,403)
  finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main(verbosity=2)
