"""GPUStack-managed private registry probes, isolated from the OCR deployment.

Temporary pull-only credentials enter only disposable probe model env. They are
not worker boot credentials and must be revoked during cleanup. Only IDs created
by this probe are deleted; model creation is never automatically replayed.
"""
import argparse,contextlib,io,json,os,secrets,sys,urllib.error,urllib.request
from pathlib import Path
sys.path.insert(0,'/root/wtaobj')
from gpustack_admin import api as raw_api
ROOT=Path('/root/wtaobj/maas-e5-registry')
STATE=ROOT/'probe.json'
PREFIX='maas-e5-registry-pilot'


def api(path,body=None,method=None):
    # Existing helper prints HTTP bodies on errors; suppress these because a
    # validation error could otherwise echo a probe's temporary password.
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        try:return raw_api(path,body,method)
        except urllib.error.HTTPError as e:raise RuntimeError('GPUStack control HTTP '+str(e.code)) from None


def save(state):
    fd=os.open(STATE,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(state,f,indent=2);f.flush();os.fsync(f.fileno())


def create(case):
    release=json.loads((ROOT/'state.json').read_text());assert release['phase']=='PUBLISHED'
    state=json.loads(STATE.read_text()) if STATE.exists() else {'cases':{},'phase':'CREATING_BACKEND'}
    if case in state['cases']:raise RuntimeError('Case already created; inspect before retry')
    worker=api('/v2/workers/5');assert worker['cluster_id']==3 and worker['state']=='ready'
    assert worker['hostname']=='XZ-31-002' and worker['ip']=='10.11.31.2'
    if case=='cold':assert state['cases']['wrong'].get('authorization_failure_confirmed')
    if case=='cache':
        import subprocess
        assert state['cases']['cold'].get('running_confirmed')
        assert subprocess.run(['docker','inspect','-f','{{.State.Running}}','maas-e5-registry-tls'],capture_output=True,text=True).stdout.strip()=='false'
    if 'backend_id' not in state:
        if STATE.exists():raise RuntimeError('Uncertain backend creation state; inspect manually')
        save(state)
        backend=api('/v2/inference-backends',{'backend_name':PREFIX+'-custom','health_check_path':'/health',
            'default_version':'pilot-r1','version_configs':{'pilot-r1':{'image_name':release['canonical_image'],
            'custom_framework':'cuda','entrypoint':'python3 /app/probe_backend.py','run_command':'--port {{port}} --model {{model_name}}'}}})
        state['backend_id']=backend['id'];save(state)
    name=PREFIX+'-'+case
    assert not any(m['name']==name for m in api('/v2/models')['items'])
    reader=json.loads((ROOT/'credentials.json').read_text())['reader']
    password=secrets.token_urlsafe(32) if case=='wrong' else reader['password']
    state['cases'][case]={'name':name,'phase':'CREATING'};save(state)
    m=api('/v2/models',{'name':name,'source':'local_path','local_path':'/opt/maas-models/ocr-e5-r1/model',
        'cluster_id':3,'replicas':1,'categories':['llm'],'backend':PREFIX+'-custom','backend_version':'pilot-r1',
        'cpu_offloading':False,'distributed_inference_across_workers':False,
        'worker_selector':{'worker-name':worker['name']},'env':{
            'GPUSTACK_MODEL_VRAM_CLAIM':str(64*1024**2),
            'GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_USERNAME':reader['username'],
            'GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_PASSWORD':password}})
    state['cases'][case].update(model_id=m['id'],phase='CREATED');state['phase']='TESTING';save(state)
    print(json.dumps({'case':case,'model_id':m['id'],'image':release['canonical_image']}))


def observe(case):
    state=json.loads(STATE.read_text());entry=state['cases'][case]
    rows=api('/v2/models/'+str(entry['model_id'])+'/instances')['items']
    result={'case':case,'instances':[{k:r.get(k) for k in ['id','state','worker_id','worker_ip','port']} for r in rows]}
    if len(rows)==1:
        row=rows[0];entry['instance_id']=row['id']
        if case=='wrong' and row['state']=='error':
            msg=(row.get('state_message') or '').lower()
            denied=any(x in msg for x in ['unauthorized','authentication required','401','denied'])
            assert denied,'Negative case failed for a reason other than authorization'
            entry['authorization_failure_confirmed']=True;result['authorization_failure_confirmed']=True
        elif case!='wrong' and row['state']=='running':
            assert row['worker_id']==5
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://'+row['worker_ip']+':'+str(row['port'])+'/health',timeout=5) as r:d=json.load(r)
            assert d['ok'] and d['version']=='p0-fixture-r1'
            entry['running_confirmed']=True;entry['health']=d;result['health']=d
            if case=='cache':entry['registry_offline_during_create']=True
    save(state);print(json.dumps(result))


def cleanup():
    state=json.loads(STATE.read_text())
    assert state['cases']['wrong'].get('authorization_failure_confirmed')
    assert all(state['cases'][case].get('running_confirmed') for case in ['cold','cache'])
    removed=[]
    for entry in state['cases'].values():
        # This probe does not create routes. Never delete a pre-existing route
        # merely because its name happens to match a disposable model.
        model=api('/v2/models/'+str(entry['model_id']));assert model['name']==entry['name']
        api('/v2/models/'+str(model['id']),method='DELETE');removed.append(['model',model['id']])
    api('/v2/inference-backends/'+str(state['backend_id']),method='DELETE');removed.append(['backend',state['backend_id']])
    # Revoke both temporary accounts, preserving bound inodes. Backup is private
    # on the operator Mac; no long-lived publisher/reader is being provisioned.
    for name in ['readers.htpasswd','publishers.htpasswd']:
        with (ROOT/name).open('w') as f:f.flush();os.fsync(f.fileno())
    state.update(phase='CLEANED',removed=removed,reader_revoked=True,publisher_revoked=True);save(state)
    print(json.dumps({'phase':state['phase'],'removed':removed,'reader_revoked':True,'publisher_revoked':True}))


if __name__=='__main__':
    import socket
    assert socket.gethostname()=='XZ-31-001' and os.getuid()==0
    p=argparse.ArgumentParser();p.add_argument('action',choices=['create','observe','cleanup']);p.add_argument('--case',choices=['wrong','cold','cache'])
    a=p.parse_args()
    if a.action=='create':create(a.case)
    elif a.action=='observe':observe(a.case)
    else:cleanup()
