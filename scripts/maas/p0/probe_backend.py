"""CPU-only disposable backend for gateway tracing; no real model/token usage."""
import argparse, collections, json, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--model',default='probe');a=p.parse_args()
counts=collections.Counter();lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,value,content_type='application/json'):
  data=value if isinstance(value,bytes) else json.dumps(value).encode()
  self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):
  if self.path=='/health':return self.send(200,{'ok':True,'version':'p0-fixture-r1'})
  if self.path=='/probe/stats':
   with lock:data=dict(counts)
   return self.send(200,{'instance':socket.gethostname(),'counts':data})
  return self.send(404,{'error':'not_found'})
 def do_POST(self):
  n=int(self.headers.get('Content-Length','0'))
  if n>1048576:return self.send(413,{'error':'too_large'})
  try:body=json.loads(self.rfile.read(n) or b'{}')
  except Exception:return self.send(400,{'error':'bad_json'})
  case=self.headers.get('x-maas-probe-case') or body.get('probe_case','unmarked')
  if len(case)>100:return self.send(400,{'error':'invalid_case'})
  with lock:counts[case]+=1;count=counts[case]
  if body.get('probe_fail')=='always_503':return self.send(503,{'error':{'message':'Injected P0 failure','type':'server_error','code':'probe_503'}})
  if self.path=='/native/echo':return self.send(200,{'native':True,'path':self.path,'payload':body,'instance':socket.gethostname(),'invocation':count})
  if self.path!='/v1/chat/completions':return self.send(404,{'error':'not_found'})
  value={'id':'p0-fixture','object':'chat.completion','created':int(time.time()),'model':body.get('model',a.model),'choices':[{'index':0,'message':{'role':'assistant','content':'P0 synthetic fixture'},'finish_reason':'stop'}]}
  if not body.get('probe_no_usage'):value['usage']={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
  if body.get('stream'):
   event={'id':'p0-fixture','object':'chat.completion.chunk','created':int(time.time()),'model':value['model'],'choices':[{'index':0,'delta':{'role':'assistant','content':'P0 synthetic fixture'},'finish_reason':'stop'}]}
   if 'usage' in value:event['usage']=value['usage']
   wire=('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode()
   return self.send(200,wire,'text/event-stream')
  return self.send(200,value)
ThreadingHTTPServer(('0.0.0.0',a.port),Handler).serve_forever()
