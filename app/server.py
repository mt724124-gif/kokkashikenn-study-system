"""python app/server.py --port 8765 で起動。127.0.0.1のみで待ち受ける。"""
import argparse,json,pathlib,secrets,sys,webbrowser
from http.server import HTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
from engine import Study,StoreLock
ROOT=pathlib.Path(__file__).resolve().parents[1]
def create_server(root=ROOT,port=8765):
 store_lock=StoreLock(root)
 try:study=Study(root)
 except Exception:store_lock.close();raise
 token=secrets.token_urlsafe(32)
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def send(self,obj,code=200,ctype='application/json; charset=utf-8'):
   data=(json.dumps(obj,ensure_ascii=False)if not isinstance(obj,bytes)else obj);data=data.encode('utf-8')if isinstance(data,str)else data
   self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(data)
  def do_GET(self):
   try:
    p=urlsplit(self.path);q=parse_qs(p.query)
    if p.path=='/api/state':self.send(dict(study.state(),token=token))
    elif p.path=='/api/history':self.send(study.history(q.get('unit_id',[''])[0]))
    elif p.path=='/api/result':self.send(study.result(q.get('session_id',[''])[0]))
    elif p.path=='/api/resume':
     done={e['payload']['session_id']for e in study.events()if e['type']=='submit'};starts=[e for e in study.events()if e['type']=='start'and e['id']not in done and e['payload']['unit_id']==q.get('unit_id',[''])[0]]
     self.send(study.public_start(starts[-1])if starts else None)
    elif p.path in ['/','/app.js','/style.css']:
     f=ROOT/'app'/('index.html'if p.path=='/'else p.path[1:]);ct='text/html'if p.path=='/'else 'text/javascript'if p.path.endswith('.js')else 'text/css';self.send(f.read_bytes(),ctype=ct+'; charset=utf-8')
    else:self.send({'error':'ページがありません'},404)
   except (ValueError,KeyError,TypeError)as e:self.send({'error':str(e)},400)
   except Exception:self.send({'error':'読込に失敗しました。ファイルを確認してください'},500)
  def do_POST(self):
   # ローカル単一利用向け。外部ページからの変更要求とDNS再束縛を防ぐ。
   expected=f'127.0.0.1:{self.server.server_port}'
   if self.headers.get('Host')not in [expected,f'localhost:{self.server.server_port}']or self.headers.get('X-Study-Token')!=token or self.headers.get('Origin')not in [None,'http://'+expected,f'http://localhost:{self.server.server_port}']:
    return self.send({'error':'同じローカル画面から操作してください'},403)
   try:
    n=int(self.headers.get('Content-Length','0'))
    if n<1 or n>2_000_000:return self.send({'error':'要求サイズが不正'},413)
    d=json.loads(self.rfile.read(n));route=urlsplit(self.path).path;rid=d.get('request_id')
    if not isinstance(rid,str):raise ValueError('要求IDが必要')
    if route=='/api/start':value=study.start(d['unit_id'],rid)
    elif route=='/api/submit':value=study.submit(d['session_id'],d['answers'],rid)
    elif route=='/api/assess':value=study.assess(d['session_id'],d['scores'],rid)
    elif route=='/api/report':value=study.report(d['question_id'],d['version'],d['reason'],rid)
    elif route=='/api/questions':value=study.import_questions(d['questions'],d['reason'],rid)
    else:return self.send({'error':'処理がありません'},404)
    # 正本保存済みならビュー生成の失敗で再送を誘発しない。
    try:study.export_views()
    except OSError:pass
    self.send(value)
   except (ValueError,KeyError,TypeError)as e:self.send({'error':str(e)},400)
   except Exception:self.send({'error':'保存処理に失敗しました。学習ログを確認してから再試行してください'},500)
 class LocalServer(HTTPServer):
  def server_close(self):
   super().server_close();store_lock.close()
 try:server=LocalServer(('127.0.0.1',port),Handler)
 except Exception:store_lock.close();raise
 server.study=study;return server
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
 try:
  server=create_server(port=args.port);url=f'http://127.0.0.1:{server.server_port}';print('青本 学習システム：'+url,flush=True)
  if not args.no_browser:webbrowser.open(url)
  server.serve_forever()
 except KeyboardInterrupt:pass
 except (OSError,ValueError) as e:print('起動できません。ポートの使用状況と保存権限を確認してください：'+str(e));sys.exit(1)
 finally:
  if 'server'in locals():server.server_close()
