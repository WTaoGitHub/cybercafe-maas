"""Compatible app-image forward/back rehearsal. No schema downgrade or volume deletion."""
import base64,json,os,subprocess,time,urllib.request,uuid
from pathlib import Path
LIVE=Path('/root/wtaobj/maas-live');ROOT=Path('/root/wtaobj/maas-framework')
BASE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]

def run(*a,env=None):return subprocess.check_output(a,text=True,stderr=subprocess.PIPE,timeout=150,env=env)
def cli(*a):return json.loads(run(*BASE,'exec','-T','api','python','-m','maas_platform.cli',*a))
def main():
 before=json.loads(run('docker','inspect','maas-tasks-api-1'))[0]['Image'];new=json.loads(run('docker','image','inspect','cybercafe-maas-tasks:p4-r2'))[0]['Id'];assert before!=new
 key=(LIVE/'secrets/pilot-key').read_text().strip()
 def request(path,body=None,media='application/json',idem=None):
  data=json.dumps(body).encode() if isinstance(body,dict) else body;h={'Authorization':'Bearer '+key,'Content-Type':media}
  if idem:h['Idempotency-Key']=idem
  with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18831'+path,data=data,headers=h),timeout=40) as r:return r.status,json.load(r)
 def switch(image):
  run(*BASE,'stop','--timeout','30','api','executor','observer')
  env={**os.environ,'MAAS_TASK_IMAGE':image};run(*BASE,'up','-d','--no-build','--wait',env=env)
 report={'started_at':time.time(),'old_image':before,'candidate_image':new,'schema_downgraded':False}
 paused=cli('pause','--reason','P4 compatible image rollback rehearsal');assert paused['managed_requests_drained']
 try:
  image=(LIVE/'probes/upright.png').read_bytes();_,art=request('/maas/v1/artifacts',image,'image/png')
  _,task=request('/maas/v1/tasks',{'model':'ocr.default','input':{'image':{'artifact_id':art['id']}},'timeout_seconds':600},idem='rollback-'+uuid.uuid4().hex)
  switch('cybercafe-maas-tasks:p4-r2');assert cli('drain-status')['paused'];assert request('/maas/v1/tasks/'+task['id'])[1]['state']=='QUEUED'
  assert json.loads(run('docker','inspect','maas-tasks-api-1'))[0]['Image']==new
  report['forward_preserved_queue']=True
 finally:
  switch('cybercafe-maas-tasks:p4-r1')
  assert json.loads(run('docker','inspect','maas-tasks-api-1'))[0]['Image']==before
  cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
 end=time.monotonic()+120
 while time.monotonic()<end:
  _,row=request('/maas/v1/tasks/'+task['id'])
  if row['state']=='SUCCEEDED':break
  if row['state'] in ('FAILED','CANCELLED'):raise RuntimeError('Restored task failed')
  time.sleep(.5)
 else:raise RuntimeError('Restored task timeout')
 _,result=request('/maas/v1/artifacts/'+row['result_artifact_id']);assert len(result['outputs']['lines'])==46
 report.update(rolled_back=True,queued_task_succeeded=True,attempts=row['attempts'],finished_at=time.time(),ok=True)
 (LIVE/'p4-ops/rollback.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
