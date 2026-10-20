"""ブラウザ不要の対話CLI。python app/cli.py [--unit CHE-0004]"""
import argparse,json,pathlib,uuid
from engine import Study,StoreLock
ROOT=pathlib.Path(__file__).resolve().parents[1]
def run(study,uid,input_fn=input,output=print):
 start=study.start(uid,str(uuid.uuid4()));answers={}
 output('区画：'+uid+'（試用問題の範囲を評価します）')
 for i,q in enumerate(start['questions'],1):
  output(f'\n第{i}問：'+q['prompt'])
  if q['type']=='text':
   while True:
    value=input_fn('自由記述：').strip()
    if value:answers[q['id']]=value;break
  else:
   for j,o in enumerate(q['options'],1):output(f' {j}. {o}')
   while True:
    try:
     value=[int(x.strip())-1 for x in input_fn('番号（複数はカンマ区切り）：').split(',')]
     if not value or len(value)!=len(set(value))or any(x<0 or x>=len(q['options'])for x in value)or(q['type']=='single'and len(value)!=1):raise ValueError()
     answers[q['id']]=value;break
    except ValueError:output('有効な選択肢番号を入力してください。')
 result=study.submit(start['session_id'],answers,str(uuid.uuid4()));scores={}
 for x in result['items']:
  q=x['question'];output('\n'+q['prompt']);output('解説：'+q['explanation'])
  if q['type']=='text':
   output('基準：'+' / '.join(q['rubric']))
   while True:
    try:
     score=float(input_fn('自己評価 0=不十分 / 0.5=部分的 / 1=説明できた：'))
     if score not in [0,0.5,1]:raise ValueError()
     scores[q['id']]=score;break
    except ValueError:output('0・0.5・1から選んでください。')
  else:output('正答：'+' / '.join(q['options'][i]for i in q['answer']));output('判定：'+('正答'if x['score']==1 else '誤答'))
 if scores:result=study.assess(start['session_id'],scores,str(uuid.uuid4()))
 study.export_views();review=next(r for r in study.reviews()if r['unit_id']==uid);output('保存済み。次回復習：'+review['next_review']);return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--unit');a=p.parse_args();lock=StoreLock(ROOT)
 try:
  study=Study(ROOT);available=[u for u in study.state()['units']if u['question_count_available']]
  if not a.unit:
   for i,u in enumerate(available,1):print(i,u['id'],u['theme'])
   a.unit=available[int(input('区画番号：'))-1]['id']
  run(study,a.unit)
 except (ValueError,IndexError,KeyboardInterrupt,EOFError)as e:print('終了：'+str(e))
 finally:lock.close()
