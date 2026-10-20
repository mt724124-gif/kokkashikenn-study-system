"""標準ライブラリだけの学習処理。記録は追記専用Markdown。ネットワーク呼出しなし。"""
from __future__ import annotations
import datetime as dt,hashlib,json,pathlib,re,uuid,os
JST=dt.timezone(dt.timedelta(hours=9)); FIELDS=['学習状態','理解度','学習回数','演習問題数','最終学習日','次回復習日','弱点','関連ログ']
def now():return dt.datetime.now(JST).isoformat(timespec='seconds')
def pages(value):
 out=set()
 for part in value.split(';'):
  ns=list(map(int,part.split('-')))
  if not 1<=len(ns)<=2 or ns[0]<1 or ns[0]>ns[-1]:raise ValueError('PDF範囲が不正')
  out.update(range(ns[0],ns[-1]+1))
 return out
def atomic_json(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,path)
class StoreLock:
 """同じ保存先を別プロセスから同時更新しない。終了時にOSが解除。"""
 def __init__(self,root):
  path=pathlib.Path(root)/'演習ログ';path.mkdir(parents=True,exist_ok=True);self.file=open(path/'.study.lock','a+b')
  try:
   if os.name=='nt':
    import msvcrt
    self.file.seek(0,2)
    if self.file.tell()==0:self.file.write(b'0');self.file.flush()
    self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl
    fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:
   self.file.close();raise ValueError('この保存先は別の学習プログラムで使用中です。先にそちらを終了してください')
 def close(self):self.file.close()
class Study:
 def __init__(self,root):
  self.root=pathlib.Path(root);self.master=json.loads((self.root/'学習マスター/master.json').read_text(encoding='utf-8'));self.mapping=json.loads((self.root/'学習マスター/ID移行台帳.json').read_text(encoding='utf-8'))
  path=self.root/'学習マスター/青本_学習マスター_v1.2.md'
  if hashlib.sha256(path.read_bytes()).hexdigest()!=self.master['markdown_sha256']:raise ValueError('学習マスターMarkdownと索引が不一致。build_master.pyで再生成し、意図しない編集なら復元してください')
  self.units={u['id']:u for u in self.master['units']};self.archive={u['id']:u for u in self.master['archived_units']};self.logdir=self.root/'演習ログ/記録';self.logdir.mkdir(parents=True,exist_ok=True)
  if len(self.units)!=len(self.master['units']):raise ValueError('区画ID重複')
  self.bank=self.questions();self.validate_bank(list(self.bank.values()))
 def events(self):
  if hasattr(self,'_event_cache'):return self._event_cache
  out=[]
  for path in sorted(self.logdir.glob('*.md')):
   text=path.read_text(encoding='utf-8');match=re.search(r'\n```json\n(.*?)\n```\n',text,re.S)
   if not match:raise ValueError('学習ログが読めません：'+path.name)
   e=json.loads(match.group(1));out.append(e)
  self._event_cache=sorted(out,key=lambda e:(e['at'],e['sequence']));return self._event_cache
 def append(self,kind,payload,request_id=None):
  request_id=request_id or str(uuid.uuid4())
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id):raise ValueError('要求IDが不正')
  digest=hashlib.sha256(json.dumps([kind,payload],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
  events=self.events()
  for e in events:
   if e['request_id']==request_id:
    if e['payload_sha256']!=digest:raise ValueError('同じ要求IDに異なる内容が送信されました')
    return e
  eid=str(uuid.uuid4());e=dict(id=eid,at=now(),sequence=len(events)+1,type=kind,request_id=request_id,payload_sha256=digest,payload=payload)
  text='# 学習記録｜'+kind+'\n\n日時：'+e['at']+'\n\n区画：'+str(payload.get('unit_id',''))+'\n\nこの記録は追記専用です。訂正・再評価は別記録として保存します。\n\n```json\n'+json.dumps(e,ensure_ascii=False,indent=2)+'\n```\n'
  # 書込み中断で正式ログが壊れないよう、一時ファイルを同期してから原子的に配置。
  target=self.logdir/(f'{e["sequence"]:08d}-'+eid+'.md');tmp=target.with_suffix('.tmp')
  with open(tmp,'x',encoding='utf-8')as f:f.write(text);f.flush();os.fsync(f.fileno())
  os.replace(tmp,target);self._event_cache.append(e);return e
 def questions(self):
  bank={}
  for q in json.loads((self.root/'問題データ/questions.json').read_text(encoding='utf-8')):
   if q['id']in bank:raise ValueError('初期問題IDが重複')
   bank[q['id']]=q
  for e in self.events():
   if e['type']=='question_revision':
    for q in e['payload']['questions']:bank[q['id']]=q
  return bank
 def invalid(self):return {(e['payload']['question_id'],e['payload']['version'])for e in self.events()if e['type']=='question_report'}
 def validate_bank(self,qs):
  seen=set()
  for q in qs:
   if not isinstance(q,dict)or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}',q.get('id',''))or q['id']in seen:raise ValueError('問題IDが不正または重複')
   seen.add(q['id']);u=self.units.get(q.get('unit_id')) or self.archive.get(q.get('unit_id'))
   if u is None:raise ValueError('存在しない学習区画')
   if type(q.get('version'))is not int or q['version']<1:raise ValueError('問題版番号が不正')
   if q.get('coverage','pilot')not in ['pilot','unit']:raise ValueError('出題範囲の評価区分が不正')
   if q.get('type')not in ['single','multi','text']:raise ValueError('問題形式が不正')
   for field in ['prompt','explanation','topic']:
    if not isinstance(q.get(field),str)or not q[field].strip():raise ValueError('問題の必須項目が空：'+field)
   if not isinstance(q.get('validation'),dict)or q['validation'].get('status')not in ['draft','text_checked','human_checked']:raise ValueError('問題の検証状態が不正')
   if not isinstance(q.get('sources'),list)or not q['sources']or not all(isinstance(x,dict)for x in q['sources']):raise ValueError('根拠がありません')
   for s in q['sources']:
    if s.get('file')!=u['pdf_file']or not pages(s.get('pdf_pages','')).issubset(pages(u['pdf_pages']))or not s.get('note'):raise ValueError('根拠ページが区画範囲と不一致')
   if q['type']=='text':
    if not isinstance(q.get('answer'),str)or not q['answer']or not isinstance(q.get('rubric'),list)or not q['rubric']or not all(isinstance(x,str)and x for x in q['rubric']):raise ValueError('自由記述の解答例・基準が必要')
   else:
    opts=q.get('options');ans=q.get('answer')
    if not isinstance(opts,list)or len(opts)<2 or not all(isinstance(x,str)and x for x in opts):raise ValueError('選択肢が不正')
    if not isinstance(ans,list)or not ans or any(type(x)is not int or x<0 or x>=len(opts)for x in ans)or len(set(ans))!=len(ans):raise ValueError('正答が不正')
    if q['type']=='single'and len(ans)!=1:raise ValueError('単一選択は正答1つ')
 def import_questions(self,qs,reason,request_id):
  if not isinstance(qs,list)or not 1<=len(qs)<=100:raise ValueError('1〜100問を指定してください')
  if not isinstance(reason,str)or not reason.strip():raise ValueError('追加・訂正理由が必要')
  # 通信再送を先に扱い、版番号チェックによる重複登録を防ぐ。
  prior=next((e for e in self.events()if e['request_id']==request_id),None)
  if prior:return self.append('question_revision',{'questions':qs,'reason':reason},request_id)
  bank=self.questions();self.validate_bank(qs)
  if any(q['unit_id']not in self.units for q in qs):raise ValueError('退避区画へ新しい問題は登録できません')
  for q in qs:
   old=bank.get(q['id'])
   if old and (q['unit_id']!=old['unit_id']or q['version']!=old['version']+1):raise ValueError('既存問題は同一区画・次の版番号で訂正してください')
   if not old and q['version']!=1:raise ValueError('新規問題の版は1')
  return self.append('question_revision',{'questions':qs,'reason':reason},request_id)
 def public_question(self,q):return {k:v for k,v in q.items()if k not in ['answer','explanation','rubric']}
 def start(self,unit_id,request_id):
  if unit_id not in self.units:raise ValueError('稼働中の区画を選択してください')
  old=next((e for e in self.events()if e['request_id']==request_id),None)
  if old:
   if old['type']!='start'or old['payload']['unit_id']!=unit_id:raise ValueError('要求ID重複')
   return self.public_start(old)
  invalid=self.invalid();qs=[q for q in self.questions().values()if q['unit_id']==unit_id and q['validation']['status']!='draft'and(q['id'],q['version'])not in invalid][:5]
  if not qs:raise ValueError('この区画には利用可能な問題がありません。問題追加フォームから登録できます')
  e=self.append('start',{'unit_id':unit_id,'questions':qs},request_id);return self.public_start(e)
 def public_start(self,e):return dict(session_id=e['id'],unit_id=e['payload']['unit_id'],questions=[dict(self.public_question(q),void=(q['id'],q['version'])in self.invalid())for q in e['payload']['questions']])
 def get_start(self,sid):
  e=next((e for e in self.events()if e['id']==sid and e['type']=='start'),None)
  if not e:raise ValueError('セッションが見つかりません')
  return e
 def submit(self,sid,answers,request_id):
  start=self.get_start(sid);qs=start['payload']['questions']
  if not isinstance(answers,dict)or set(answers)!=set(q['id']for q in qs):raise ValueError('全問題への解答が必要です')
  for q in qs:
   a=answers[q['id']]
   if (q['id'],q['version'])in self.invalid():continue
   if q['type']=='text':
    if not isinstance(a,str)or not a.strip()or len(a)>20000:raise ValueError('自由記述を入力してください（2万字以内）')
   elif not isinstance(a,list)or not a or any(type(x)is not int or x<0 or x>=len(q['options'])for x in a)or len(a)!=len(set(a))or(q['type']=='single'and len(a)!=1):raise ValueError('選択肢の解答が不正')
  if any(e['type']=='submit'and e['payload']['session_id']==sid for e in self.events()):
   prior=next(e for e in self.events()if e['type']=='submit'and e['payload']['session_id']==sid)
   if prior['payload']['answers']!=answers:raise ValueError('採点済みです。再演習は新しいセッションで開始してください')
   return self.result(sid)
  self.append('submit',{'session_id':sid,'unit_id':start['payload']['unit_id'],'answers':answers},request_id);return self.result(sid)
 def assess(self,sid,scores,request_id):
  result=self.result(sid);textids={q['question']['id']for q in result['items']if q['question']['type']=='text'}
  if not isinstance(scores,dict)or not scores or not set(scores).issubset(textids)or any(type(v)not in [int,float]or v not in [0,0.5,1]for v in scores.values()):raise ValueError('自由記述の自己評価は0・0.5・1')
  self.append('assessment',{'session_id':sid,'unit_id':result['unit_id'],'scores':scores,'method':'self_rubric'},request_id);return self.result(sid)
 def report(self,qid,version,reason,request_id):
  if type(version)is not int or not isinstance(reason,str)or not reason.strip():raise ValueError('対象版と理由が必要')
  exists=any(q['id']==qid and q['version']==version for q in self.questions().values())or any(q['id']==qid and q['version']==version for e in self.events()if e['type']=='start'for q in e['payload']['questions'])
  if not exists:raise ValueError('対象の問題版がありません')
  return self.append('question_report',{'question_id':qid,'version':version,'reason':reason},request_id)
 def result(self,sid):
  events=self.events();start=self.get_start(sid);sub=next((e for e in events if e['type']=='submit'and e['payload']['session_id']==sid),None)
  if not sub:raise ValueError('まだ提出されていません')
  scores={};invalid=self.invalid()
  for e in events:
   if e['type']=='assessment'and e['payload']['session_id']==sid:scores.update(e['payload']['scores'])
  items=[]
  for q in start['payload']['questions']:
   a=sub['payload']['answers'][q['id']];void=(q['id'],q['version'])in invalid;score=None if void else scores.get(q['id'])if q['type']=='text'else float(sorted(a)==sorted(q['answer']))
   items.append(dict(question=q,answer=a,score=score,void=void,pending=not void and score is None))
  valid=[x for x in items if not x['void']];graded=[x for x in valid if x['score']is not None]
  return dict(session_id=sid,unit_id=start['payload']['unit_id'],at=sub['at'],coverage_complete=len(valid)>=5 and all(x['question'].get('coverage')=='unit'for x in valid),items=items,valid_count=len(valid),graded_count=len(graded),pending_count=sum(x['pending']for x in valid),ratio=sum(x['score']for x in graded)/len(graded)if graded else None,weak_topics=[x['question']['topic']for x in graded if x['score']<1])
 def history(self,unit_id):
  # 関連旧履歴は表示するが新子区画の習得率へ足し込まない。
  relevant={unit_id};u=self.units.get(unit_id)or self.archive.get(unit_id)
  if not u:raise ValueError('区画がありません')
  relevant.update(u.get('original_ids',[]));relevant.update(u.get('replaced_by',[]))
  allknown={**self.units,**self.archive};records=[]
  for uid in sorted(relevant):
   if uid in allknown:records.append(dict(unit_id=uid,initial=allknown[uid]['initial'],related_only=uid!=unit_id))
  sessions=[self.result(e['payload']['session_id'])for e in self.events()if e['type']=='submit'and e['payload']['unit_id']in relevant]
  return dict(unit_id=unit_id,baseline_records=records,sessions=sessions)
 def reviews(self,today=None):
  today=today or dt.datetime.now(JST).date();by={}
  for e in self.events():
   if e['type']=='submit':
    r=self.result(e['payload']['session_id'])
    if r['valid_count'] and r['unit_id']in self.units:by.setdefault(r['unit_id'],[]).append(r)
  out=[]
  for uid,rs in by.items():
   rs.sort(key=lambda r:r['at']);r=rs[-1];date=dt.datetime.fromisoformat(r['at']).astimezone(JST).date();ratio=r['ratio']
   stable=False
   if len(rs)>=2:
    prior=rs[-2];gap=(date-dt.datetime.fromisoformat(prior['at']).astimezone(JST).date()).days
    stable=prior['coverage_complete'] and r['coverage_complete'] and gap>=7 and not prior['pending_count']and(prior['ratio']or 0)>=0.8 and not r['pending_count']and(ratio or 0)>=0.8 and not r['weak_topics']
   if r['pending_count']or ratio is None:level='未評価';days=1
   elif stable:level=4;days=21
   elif ratio>=0.8:level=3;days=7
   elif ratio>=0.6:level=2;days=3
   else:level=1;days=1
   due=date+dt.timedelta(days=days);u=self.units[uid]
   out.append(dict(unit_id=uid,theme=u['theme'],subject=u['subject'],last_study=date.isoformat(),next_review=due.isoformat(),overdue=due<today,due=due<=today,understanding=level,study_count=len(rs),question_count=sum(x['valid_count']for x in rs),weak_topics=r['weak_topics'],pending=r['pending_count'],coverage_complete=r['coverage_complete'],evaluation_scope='区画全体'if r['coverage_complete']else '試用問題の範囲（暫定）',priority='期限到来'if due<=today else '評価待ち'if r['pending_count']else '予定',state='学習中'if r['pending_count']else '学習済み'))
  return sorted(out,key=lambda r:(not r['due'],r['next_review'],str(r['understanding']),r['unit_id']))
 def state(self):
  bank=self.questions();bad=self.invalid();counts={}
  for q in bank.values():
   if q['validation']['status']!='draft'and(q['id'],q['version'])not in bad:counts[q['unit_id']]=counts.get(q['unit_id'],0)+1
  reviews=self.reviews();lookup={r['unit_id']:r for r in reviews};out=[]
  for u in self.master['units']:
   out.append(dict(u,question_count_available=counts.get(u['id'],0),progress=lookup.get(u['id'])))
  done={e['payload']['session_id']for e in self.events()if e['type']=='submit'};unfinished=list({e['payload']['unit_id']for e in self.events()if e['type']=='start'and e['id']not in done})
  return dict(version=self.master['version'],unfinished=unfinished,units=out,reviews=reviews,counts=self.master['counts'],summary=dict(total=len(out),learned=len(reviews),mastered=sum(r['understanding']==4 for r in reviews),due=sum(r['due']for r in reviews),questions=sum(counts.values()),available_units=len(counts)),reports=[e for e in self.events()if e['type']=='question_report'])
 def export_views(self):
  reviews=self.reviews();atomic_json(self.root/'復習管理/復習予定.json',reviews)
  text='# 復習予定（追記ログから再生成）\n\n| 区画 | テーマ | 理解度 | 最終学習 | 次回復習 | 弱点 |\n|---|---|---|---|---|---|\n'
  for r in reviews:text+='| '+' | '.join(str(r[k]).replace('|','\\|')for k in ['unit_id','theme','understanding','last_study','next_review','weak_topics'])+' |\n'
  (self.root/'復習管理/復習予定.md').write_text(text,encoding='utf-8')
  path=self.root/'学習ダッシュボード';path.mkdir(exist_ok=True)
  dashboard='# 学習進捗（演習ログから再生成）\n\n分母はv1.2候補の稼働1,344区画。退避44区画は含めない。要確認フラグは未解析・習得済みを意味しない。試用問題は区画全体の習得確認に用いない。\n\n| 科目 | 全区画 | 一度以上学習 | 学習網羅率 | 習得済み | 習得率 | 期限到来 |\n|---|---|---|---|---|---|---|\n'
  for subject,total in self.master['counts'].items():
   rr=[r for r in reviews if r['subject']==subject];mastered=sum(r['understanding']==4 for r in rr)
   dashboard+=f'| {subject} | {total} | {len(rr)} | {len(rr)/total:.1%} | {mastered} | {mastered/total:.1%} | {sum(r["due"]for r in rr)} |\n'
  dashboard+='\n詳細な最新理解度・学習状態・弱点・日付は [[../復習管理/復習予定]] を参照。初期値を保持する学習マスター単独では最新の進捗を表さない。\n'
  (path/'進捗.md').write_text(dashboard,encoding='utf-8')

