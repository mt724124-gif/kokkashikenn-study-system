"""既存成果物のみから正式版候補を再現生成する。PDF・ネットワークアクセスなし。"""
import csv,collections,hashlib,json,pathlib,re,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
FIELDS=['学習状態','理解度','学習回数','演習問題数','最終学習日','次回復習日','弱点','関連ログ']
def dump(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def build(root=ROOT):
 src=root/'資料';text=(src/'青本_学習マスター_v1.1.md').read_text(encoding='utf-8');heads={};ctx={};old=[]
 for line in text.splitlines():
  if line.startswith('#'):heads[len(line)-len(line.lstrip('#'))]=line.lstrip('# ').strip()
  if line.startswith('<!-- subject='):ctx=dict(x.strip().split('=',1)for x in line[4:-3].strip().split(';'))
  if re.match(r'^\| (PHY|CHE|BIO|HYG|PHA1|PHA2|PHT|LAW|PRC)-\d{4} \|',line):
   a=[x.strip()for x in line.strip('|').split('|')];assert len(a)==14
   old.append(dict(id=a[0],subject=ctx['subject'],subject_name=heads[2],chapter=heads[3],section=heads[5],source_items=[ctx['subject']+'/章'+ctx['chapter']+'/'+a[1]],theme=a[2],print_pages=a[3],pdf_file=ctx['source_pdf'],pdf_pages=a[4],source_status=a[5],initial=dict(zip(FIELDS,a[6:])),original_ids=[],legacy_ids=[],review_required=False,review_reasons=[],anchor='',proposal=None,active=True))
 assert len(old)==1313;by={u['id']:u for u in old};assert len(by)==1313
 eva={r['既存ID']:r for r in csv.DictReader(open(src/'Phase1.2_全区画粒度評価.csv',encoding='utf-8-sig'))};assert set(eva)==set(by)
 plans=json.loads((src/'調整案.json').read_text());assert len([g for g in plans if g['children']])==19
 maximum={p:max(int(u['id'].split('-')[1])for u in old if u['subject']==p)for p in dict.fromkeys(u['subject']for u in old)}
 replacements={};mapping=[];fresh=[]
 for g in plans:
  if not g['children']:continue
  olds=[by[x]for x in g['old']];refs=sorted(set(i for u in olds for i in u['source_items']));newids=[]
  for c in g['children']:
   pre=olds[0]['subject'];maximum[pre]+=1;uid=f'{pre}-{maximum[pre]:04d}';newids.append(uid)
   u=dict(olds[0]);u.update(id=uid,theme=c['name'],source_items=refs,pdf_pages=c['pages'],print_pages='未確認',source_status='Phase1.2本文文字層に基づく条件付き採用',initial=dict(zip(FIELDS,['未学習','未評価','0','0','未設定','未設定','未評価','未設定'])),original_ids=g['old'],legacy_ids=[c['id']],review_required=True,review_reasons=['条件付き採用：境界未確定事項は運用中に報告'],anchor=c['anchor'],proposal=g['id'])
   fresh.append(u)
  mapping.append(dict(proposal=g['id'],action=g['action'],old_ids=g['old'],new_ids=newids,temporary_ids=[c['id']for c in g['children']],source_items=refs,history_policy='旧記録保存。子への理解度・回数の複製なし'))
  for x in g['old']:assert x not in replacements;replacements[x]=newids
 active=[];archive=[]
 for u in old:
  if u['id']in replacements:u=dict(u,active=False,replaced_by=replacements[u['id']]);archive.append(u)
  else:
   e=eva[u['id']];u['review_required']=e['判定']=='REVIEW';u['review_reasons']=[e['判定理由']]if u['review_required']else[];active.append(u)
 active+=fresh;assert len(active)==1344 and len(archive)==44
 refsold=set(i for u in old for i in u['source_items']);refsnew=set(i for u in active for i in u['source_items']);assert refsold==refsnew and len(refsnew)==973
 allids=[u['id']for u in active+archive];assert len(allids)==len(set(allids))
 md='# 青本 学習マスター v1.2 正式版候補\n\n19グループを条件付き採用。稼働1,344区画、退避44区画、973原小項目参照。GitHub未反映。資料位置の未確定事項を残して学習を開始する版。\n\n範囲の正本は本Markdown、master.jsonは同時生成した機械用索引。履歴は演習ログ/記録の追記Markdownから計算し、初期値を上書きしない。新規75区画の理解度は未評価。要確認でも出題を禁止せず表示する。\n\n'
 def table(units):
  out='| 区画ID | 科目 | 学習テーマ | 原小項目ID | PDFファイル | PDF範囲 | 紙面内アンカー | 学習状態 | 理解度 | 要確認 | 旧ID対応 |\n|'+ '|'.join(['---']*11)+'|\n'
  for u in units:
   vals=[u['id'],u['subject_name'],u['theme'],';'.join(u['source_items']),u['pdf_file'],u['pdf_pages'],u['anchor']or '現行範囲',u['initial']['学習状態'],u['initial']['理解度'],'あり'if u['review_required']else 'なし',';'.join(u['original_ids'])or '維持']
   out+='| '+' | '.join(str(x).replace('|','\\|').replace('\n',' ')for x in vals)+' |\n'
  return out+'\n'
 counts={}
 for pre in maximum:
  units=[u for u in active if u['subject']==pre];counts[pre]=len(units);md+='## '+units[0]['subject_name']+f'（{len(units)}区画）\n\n'+table(units)
 md+='## 旧区画の参照退避（学習対象外・履歴は保持）\n\n'+table(archive)
 dest=root/'学習マスター';dest.mkdir(exist_ok=True)
 path=dest/'青本_学習マスター_v1.2.md';path.write_text(md,encoding='utf-8')
 data=dict(schema_version=1,version='1.2-candidate',markdown_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),units=active,archived_units=archive,source_items=sorted(refsnew),counts=counts)
 dump(dest/'master.json',data);dump(dest/'ID移行台帳.json',mapping)
 validation=dict(active=len(active),archive=len(archive),new=len(fresh),source_items=len(refsnew),subject_counts=counts,review_required=sum(u['review_required']for u in active),continued_candidates=sum(e['判定']=='REVIEW'and e['Phase1.1候補'].startswith('対象:')for e in eva.values()),legacy_records_preserved=all(by[u['id']]['initial']==u['initial']for u in active+archive if u['id']in by),input_sha256=hashlib.sha256((src/'青本_学習マスター_v1.1.md').read_bytes()).hexdigest())
 assert validation['continued_candidates']==180 and validation['legacy_records_preserved']
 pdfmeta={v['file']:v['pages']for v in json.loads((src/'入力PDF確認.json').read_text())}
 for u in active+archive:
  assert all(u[k] for k in ['id','subject','theme','source_items','pdf_file','pdf_pages'])
  for seg in u['pdf_pages'].split(';'):
   ns=list(map(int,seg.split('-')));assert 1<=ns[0]<=ns[-1]<=pdfmeta[u['pdf_file']]
 dump(dest/'整合性検証.json',validation);return validation
if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
