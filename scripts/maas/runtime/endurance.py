"""Bounded live asynchronous OCR load over verified private TLS; single client.
Not a batch API or executor-concurrency enhancement. Never clears UNKNOWN.
"""
import argparse,hashlib,json,os,signal,ssl,subprocess,threading,time,urllib.request,urllib.error,uuid
from pathlib import Path
LIVE=Path('/root/wtaobj/maas-live');FIXTURES=Path('/root/wtaobj/maas-endurance-fixtures')


def atomic(path,value):
 temp=path.with_suffix('.new');fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(temp,path)
def percentile(values,q):
 if not values:return None
 a=sorted(values);return a[min(len(a)-1,int((len(a)-1)*q))]


def main():
 p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,required=True);p.add_argument('--interval',type=float,default=5);p.add_argument('--run-id',required=True);a=p.parse_args()
 if a.seconds<5 or a.interval<1 or not a.run_id.replace('-','').isalnum():raise ValueError('Invalid bounded load settings')
 folder=LIVE/'endurance'/a.run_id;folder.mkdir(parents=True,mode=0o700)
 if (folder/'status.json').exists():raise RuntimeError('Never overwrite an endurance run')
 stop=threading.Event()
 for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
 key=(LIVE/'secrets/pilot-key').read_text().strip();ctx=ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt')
 def request(path,body=None,media='application/json',idem=None):
  raw=json.dumps(body).encode() if isinstance(body,dict) else body
  h={'Authorization':'Bearer '+key,'Content-Type':media}
  if idem:h['Idempotency-Key']=idem
  with urllib.request.urlopen(urllib.request.Request('https://127.0.0.1:18832'+path,data=raw,headers=h),context=ctx,timeout=40) as r:return r.status,json.load(r)
 inputs=[]
 for item in json.loads((FIXTURES/'manifest.json').read_text()):
  raw=(FIXTURES/item['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256']
  code,art=request('/maas/v1/artifacts',raw,item['media_type']);assert code==201
  inputs.append({**item,'artifact':art['id']})
 started=time.monotonic();wall=time.time();end=started+a.seconds;submit_ms=[];e2e=[];accepted=0;success=0;errors=[];line_count=0
 status={'run_id':a.run_id,'state':'RUNNING','started_at':wall,'duration_target_seconds':a.seconds,'interval_seconds':a.interval,
     'fixtures':len(inputs),'tls_verified':True,'concurrency':1,'errors':errors}
 def checkpoint():
  status.update(elapsed_seconds=round(time.monotonic()-started,2),accepted=accepted,succeeded=success,
    submit_p95_ms=percentile(submit_ms,.95),e2e_p95_seconds=percentile(e2e,.95),lines_total=line_count,updated_at=time.time())
  atomic(folder/'status.json',status)
 checkpoint()
 try:
  while time.monotonic()<end and not stop.is_set():
   loop=time.monotonic();item=inputs[accepted%len(inputs)];idem='endurance-'+a.run_id+'-'+str(accepted)
   code,task=request('/maas/v1/tasks',{'model':'ocr.default','input':{'image':{'artifact_id':item['artifact']}},'timeout_seconds':120},idem=idem)
   assert code==202;accepted+=1;submit_ms.append((time.monotonic()-loop)*1000)
   deadline=time.monotonic()+150
   while True:
    code,row=request('/maas/v1/tasks/'+task['id']);assert code==200
    if row['state'] in ('SUCCEEDED','FAILED','CANCELLED'):break
    if time.monotonic()>deadline:raise RuntimeError('task_poll_deadline')
    time.sleep(.3)
   if row['state']!='SUCCEEDED':raise RuntimeError('task_terminal_'+str(row.get('error_code')))
   code,result=request('/maas/v1/artifacts/'+row['result_artifact_id']);assert code==200 and isinstance(result['outputs']['lines'],list)
   success+=1;line_count+=len(result['outputs']['lines']);e2e.append(time.monotonic()-loop)
   record={'index':accepted,'image':item['id'],'source':item['source'],'task':task['id'],'attempts':row['attempts'],'submit_ms':submit_ms[-1],'e2e_seconds':e2e[-1],'lines':len(result['outputs']['lines'])}
   with (folder/'requests.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
   checkpoint();stop.wait(max(0,a.interval-(time.monotonic()-loop)))
  status['state']='COMPLETE' if not stop.is_set() and time.monotonic()>=end else 'INTERRUPTED'
 except Exception as e:
  # Record only stable classifications, never raw HTTP/SQL/payload exceptions.
  errors.append({'at':time.time(),'code':str(e) if isinstance(e,RuntimeError) else type(e).__name__})
  status['state']='FAILED'
 finally:
  # Match persisted success events against only this run's idempotency namespace.
  prefix='endurance-'+a.run_id+'-%'
  sql="SELECT json_build_object('tasks',count(*),'succeeded',count(*) FILTER (WHERE state='SUCCEEDED'),'usage_events',(SELECT count(*) FROM task_usage_events u JOIN tasks t ON t.id=u.task_id WHERE t.idempotency_key LIKE '"+prefix+"')) FROM tasks WHERE idempotency_key LIKE '"+prefix+"'"
  try:
   raw=subprocess.check_output(['docker','exec','maas-tasks-db-1','psql','-h','127.0.0.1','-p','18835','-U','maas','-d','maas','-tAc',sql],text=True,stderr=subprocess.PIPE,timeout=15)
   status['database_check']=json.loads(raw);assert status['database_check']=={'tasks':accepted,'succeeded':success,'usage_events':success}
  except Exception:status['state']='FAILED';errors.append({'at':time.time(),'code':'DATABASE_RECONCILIATION_FAILED'})
  status['finished_at']=time.time();checkpoint()
 print(json.dumps(status))


if __name__=='__main__':main()
