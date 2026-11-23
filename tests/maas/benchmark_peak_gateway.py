"""Measure two comparable closed-loop QoS phases on an existing test MaaS."""
import argparse
import base64
import collections
import concurrent.futures
import json
import math
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from maas_platform.auth import AuthConnector
from maas_platform.capacity_observe import status
from maas_platform.config import Settings, private_text
from maas_platform.maintenance import Maintenance
from maas_platform.recovery import probe_registered_models
from maas_platform.service import TaskService

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=['baseline', 'least_inflight'])
parser.add_argument('--fixture-dir', type=Path, required=True)
parser.add_argument('--credential-file', type=Path, required=True)
parser.add_argument('--project', required=True)
parser.add_argument('--duration', type=int, default=180)
args = parser.parse_args()
phase, duration, project = args.phase, args.duration, args.project
if not 30 <= duration <= 900 or not project.startswith('test_agent_peak8_'):
    parser.error('Use a 30 to 900 second window and an isolated test_agent_peak8_ project')
key = private_text(args.credential_file)
settings = Settings.from_env()
auth = AuthConnector(settings)
service = TaskService(settings, auth)
pp, ovis, doc = 'ocr.default', 'ocr.ovisocr3', 'document.unlimited-ocr-2'
if {x['id'] for x in auth.bindings()} != {project}:
    raise RuntimeError('The API must bind only the isolated test credential')
if (settings.max_running, settings.max_project_running, settings.executor_slots) != (8, 8, 8):
    raise RuntimeError('The test global limit, project limit, and executor slots must each be eight')


def report(name, **data):
    print(json.dumps({'stage': name, 'phase': phase, **data}, default=str), flush=True)


def http(path, payload=None, media='application/json', key_suffix=None):
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': media}
    if key_suffix:
        headers['Idempotency-Key'] = f'{project}-{phase}-{key_suffix}'
    body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
    request = urllib.request.Request('http://127.0.0.1:18830' + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.load(error)


def attempt(response, expected_status):
    status_code, body = response
    if status_code != expected_status:
        raise RuntimeError(f'HTTP {status_code} {body.get("error", {}).get("code", "")}')
    return body


if not Maintenance(service).status()['managed_requests_drained']:
    raise RuntimeError('The test task store must be paused and drained')
pp_bytes = (args.fixture_dir / 'pp.png').read_bytes()
ovis_bytes = (args.fixture_dir / 'ovis.png').read_bytes()
img_ids={pp:attempt(http('/maas/v1/artifacts',pp_bytes,'image/png'),201)['id'],
         ovis:attempt(http('/maas/v1/artifacts',ovis_bytes,'image/png'),201)['id']}
doc_bytes = (args.fixture_dir / 'unlimited.pdf').read_bytes()
doc_id=attempt(http('/maas/v1/artifacts',doc_bytes,'application/pdf'),201)['id']
sync_payload=json.dumps({'model':pp,'stream':False,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{
 'url':'data:image/png;base64,'+base64.b64encode(pp_bytes).decode()}}]}]}).encode()
snap=status(service.db)
if snap['global'] != {'total': 8, 'async_limit': 6, 'sync_reserved': 2}:
    raise RuntimeError('The test snapshot must provide eight total slots and two reserved for sync')
if next(p for p in snap['pools'].values() if pp in p['aliases'])['async_limit'] != 3:
    raise RuntimeError('The PP pool must reserve two of its five slots for sync')
service.set_capacity_mode(True)
Maintenance(service).resume(lambda:report('warmup',result=probe_registered_models(service,project,'/run/maas-probe/ocr-bilingual.png')))
stop=threading.Event()
sync_times=[]
sync_rejections=collections.Counter()
errors=[]
submitted=collections.Counter()
deadline=time.monotonic()+duration
clients=[pp]*5+[ovis]*3+[doc]*2+['sync']*2
useful_after=time.monotonic()+10
def lane(lane_no,model):
 with psycopg.connect(settings.database_url,autocommit=True,row_factory=dict_row,connect_timeout=5) as c:
  seq=0
  while not stop.is_set() and time.monotonic()<deadline:
   seq+=1
   t=time.monotonic()
   if model=='sync':
    code,r=http('/gpustack/v1/chat/completions',sync_payload)
    if code==200:
     result=json.loads(r['choices'][0]['message']['content'])
     if not result['lines']:raise RuntimeError('Empty synchronous PP result')
     sync_times.append((t,time.monotonic()-t))
    elif code==429 and r.get('error',{}).get('code')=='SYNC_CAPACITY':
     sync_rejections['capacity']+=1
     stop.wait(.1)
    else:raise RuntimeError(f"Sync HTTP {code} {r.get('error',{}).get('code','')}")
   else:
    b={'model':model,'timeout_seconds':220,'max_attempts':1}
    if model==doc:b.update(input={'document':{'artifact_id':doc_id}},parameters={'dpi':180})
    else:b['input']={'image':{'artifact_id':img_ids[model]}}
    r=attempt(http('/maas/v1/tasks',b,key_suffix=f'{lane_no}-{seq}'),202)
    submitted[model]+=1
    end=time.monotonic()+225
    while time.monotonic()<=end:
     result=c.execute('SELECT state,error_code,result_artifact_id,attempts FROM tasks WHERE id=%s',(r['id'],)).fetchone()
     if result['state']=='SUCCEEDED':
      if not result['result_artifact_id'] or result['attempts']!=1:raise RuntimeError('Invalid completed task')
      break
     if result['state'] in ('FAILED','CANCELLED'):
      raise RuntimeError(f"{model} {result['state']} {result['error_code']}")
     time.sleep(.17)
    else:raise RuntimeError('Async drain exceeded deadline')
def guarded(index,model):
 try:lane(index,model)
 except Exception as e:
  errors.append(str(e))
  stop.set()
def percentile(values):
 if not values:return None
 return round(sorted(values)[math.ceil(.95*len(values))-1],1)
def measures(rows):
 return {model:{'n':len(v),'queue_p95_ms':percentile([x['queue_ms'] for x in v]),
   'end_to_end_p95_ms':percentile([x['total_ms'] for x in v])}
   for model in (pp,ovis,doc) if (v:=[x for x in rows if x['model']==model])}
samples=collections.Counter()
peak_by_model=collections.Counter()
idle_by_model = collections.Counter()
document_stages = collections.Counter()
peak_per_replica=0
report('steady_start',seconds=duration,pool_limits=[3,2,1],sync_clients=2)
t0=time.monotonic()
try:
 with psycopg.connect(settings.database_url,autocommit=True,row_factory=dict_row,connect_timeout=5) as c, concurrent.futures.ThreadPoolExecutor(max_workers=len(clients)) as group:
  futures=[group.submit(guarded,i,name) for i,name in enumerate(clients)]
  next_status=t0+20
  while not all(f.done() for f in futures):
   row=c.execute("""SELECT
     (SELECT count(*) FROM inference_slots WHERE project=%s AND asynchronous AND NOT backend_finished) AS reserved,
     (SELECT count(*) FROM tasks WHERE project=%s AND model=%s AND state='QUEUED') AS queued,
     (SELECT coalesce(jsonb_agg(to_jsonb(x)),'[]'::jsonb) FROM (
       SELECT t.model,true AS async,b.gpu_instance
       FROM backend_activity b JOIN tasks t ON t.id=b.task_id WHERE t.project=%s AND b.state='DISPATCHING' AND b.gpu_finished_at IS NULL
       UNION ALL
       SELECT s.model,false,s.gpu_instance FROM sync_activity s WHERE s.project=%s AND NOT s.is_probe AND s.state='DISPATCHING' AND s.gpu_finished_at IS NULL
     ) x) AS running,
     (SELECT coalesce(min(CASE
         WHEN b.state='DISPATCHING' AND b.gpu_finished_at IS NULL AND b.gpu_instance IS NOT NULL THEN 1
         WHEN b.state='DISPATCHING' AND b.gpu_finished_at IS NULL THEN 2
         WHEN t.state='RUNNING' AND b.token IS NULL THEN 3
         WHEN t.state='RUNNING' THEN 4
         ELSE 5 END),6)
      FROM tasks t LEFT JOIN backend_activity b ON b.token=t.token
      WHERE t.project=%s AND t.model=%s AND t.state IN ('RUNNING','QUEUED','RETRY_WAIT')) AS document_stage,
     (SELECT count(*) FROM sync_activity WHERE project=%s AND state='UNKNOWN')+
     (SELECT count(*) FROM backend_activity b JOIN tasks t ON t.id=b.task_id WHERE t.project=%s AND b.state='UNKNOWN') AS unknown
   """,(project,project,pp,project,project,project,doc,project,project)).fetchone()
   now=time.monotonic()
   r=row['running']
   a=sum(x['async'] for x in r)
   s=len(r)-a
   by_model=collections.Counter(x['model'] for x in r)
   if (row['unknown'] or row['reserved']>6 or len(r)>8 or a>6 or by_model[pp]>5 or by_model[ovis]>2 or by_model[doc]>1) and not stop.is_set():
    errors.append('Capacity violation or UNKNOWN')
    stop.set()
   if useful_after<=now<deadline:
    gpu=[x for x in r if x['gpu_instance']]
    instances=collections.Counter((x['model'],x['gpu_instance']) for x in gpu)
    peak_per_replica=max(peak_per_replica,max(instances.values(),default=0))
    samples['observations']+=1
    samples['gpu_total']+=len(instances)
    samples['peak_gpu']=max(samples['peak_gpu'],len(instances))
    samples['peak_reserved_async']=max(samples['peak_reserved_async'],row['reserved'])
    samples['peak_async_dispatch']=max(samples['peak_async_dispatch'],a)
    samples['peak_authorized']=max(samples['peak_authorized'],len(r))
    samples['queue_sum']+=row['queued']
    samples['full_gpu']+=int(len(instances)==8)
    samples['six_async_two_sync']+=int(a==6 and s==2)
    samples['sync_reserve_with_backlog']+=int(a==6 and s in (1,2) and row['queued']>0)
    by_model_gpu=collections.Counter(model for model,_ in instances)
    for name,count in by_model_gpu.items():peak_by_model[name]=max(peak_by_model[name],count)
    idle_by_model.update({name: max(0, limit - by_model_gpu[name]) for name, limit in ((pp, 5), (ovis, 2), (doc, 1))})
    stage = ('gpu', 'awaiting_gpu_claim', 'preparing', 'finalizing', 'queued', 'idle')[row['document_stage'] - 1]
    document_stages[stage] += 1
   if now>=next_status:
    report('progress',elapsed=int(now-t0),samples=samples['observations'],gpu_peak=samples['peak_gpu'],sync_ok=len(sync_times),submitted=dict(submitted),errors=errors[:3])
    next_status=now+20
   time.sleep(.075)
  for f in futures:f.result()
  rows=c.execute("""SELECT t.model,t.state,
    extract(epoch FROM a.started_at-t.created_at)::float*1000 AS queue_ms,
    extract(epoch FROM b.created_at-a.started_at)::float*1000 AS preparation_ms,
    extract(epoch FROM t.updated_at-t.created_at)::float*1000 AS total_ms,
    b.gpu_finished_at IS NOT NULL AND t.attempts=1 AND u.task_id IS NOT NULL AS verified
    FROM tasks t LEFT JOIN task_attempts a ON a.task_id=t.id AND a.attempt=1
    LEFT JOIN backend_activity b ON b.task_id=t.id LEFT JOIN task_usage_events u ON u.task_id=t.id WHERE t.project=%s""",(project,)).fetchall()
  sync_receipts=c.execute("""SELECT count(*) AS n FROM sync_activity s JOIN sync_usage_events u ON s.token=u.token
    WHERE s.project=%s AND NOT s.is_probe AND s.outcome='SUCCEEDED' AND s.gpu_finished_at IS NOT NULL""",(project,)).fetchone()['n']
  sync_window=[ms*1000 for started,ms in sync_times if useful_after<=started<deadline]
  report('benchmark_result',samples=dict(samples),peak_by_model=dict(peak_by_model),peak_per_replica=peak_per_replica,
    async_success=sum(r['state']=='SUCCEEDED' and r['verified'] for r in rows),async_total=len(rows),
    sync_success=len(sync_times),sync_receipts=sync_receipts,sync_p95_ms=percentile(sync_window),sync_sample_size=len(sync_window),
    async_metrics=measures([r for r in rows if r['verified'] and r['state']=='SUCCEEDED']),
    idle_slot_samples=dict(idle_by_model),document_stage_samples=dict(document_stages),
    document_preparation_p95_ms=percentile([r['preparation_ms'] for r in rows if r['model']==doc and r['verified']]),
    sync_rejections=dict(sync_rejections),errors=errors)
  if errors or len(sync_times)!=sync_receipts or not rows or not sync_window or not all(r['state']=='SUCCEEDED' and r['verified'] for r in rows):
   raise RuntimeError('Benchmark phase failed')
finally:
 service.pause(True,'Long capacity benchmark phase complete')
 auth.client.close()
