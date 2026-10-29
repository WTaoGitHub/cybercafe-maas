"""T4 disposable TLS/auth ingress experiment, then bounded real OCR confirmation."""
import argparse
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,read_secret,save_private,replace_private
from maas_bench.arrival_point import execute_arrivals


def command(args,timeout=30):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=timeout)


def run(config):
    template=read_private(config['template_file']);queue,_=proof_entry(config['queue_proof_file'],'T4-QUEUE',template)
    out=Path(config['output_directory']);out.mkdir(mode=0o700,exist_ok=False)
    api_name='maas-e3-http-isolated';proxy_name='maas-e3-http-isolated-tls'
    existing=command(['docker','ps','-a','--format','{{.Names}}']).splitlines()
    if any(n in existing for n in (api_name,proxy_name)):raise RuntimeError('Existing isolated experiment: inspect, do not replace')
    for port in (18994,18995):
        with socket.socket() as s:s.bind(('127.0.0.1',port))
    production=json.loads(command(['docker','inspect','maas-tasks-api-1']))[0]
    env=dict(s.split('=',1) for s in production['Config']['Env'] if '=' in s)
    test_env={}
    for line in Path(config['test_env_file']).read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            k,v=line.split('=',1);test_env[k]=v
    dsn=out/'test-database-dsn';save_text(dsn,test_env['MAAS_TEST_DATABASE_URL'])
    key=out/'customer-key';save_text(key,read_secret(template['customer_key_file']))
    pepper=out/'pepper';save_text(pepper,secrets.token_hex(32))
    binding=out/'bindings.json';save_private(binding,{'credentials':[{'id':'e3-http-isolated','project':'isolated-http-e3','secret_file':str(key),'models':['ocr.default']}]})
    original=read_private(template['snapshot_file'])
    catalog=out/'catalog.json';save_private(catalog,{'ocr.default':{'driver':'gpustack.ocr','revision':template['revision'],'upstream_model':original['model']['name'],'max_runtime_s':30}})
    tls=Path('/root/wtaobj/maas-private-tls');cfg=(tls/'nginx.conf').read_text().replace(':18832',':18995').replace(':18830',':18994')
    nginx=out/'nginx.conf';nginx.write_text(cfg);nginx.chmod(0o644)
    nginx_image=json.loads(command(['docker','inspect','maas-private-tls']))[0]['Image']
    source=Path(template['source_root'])
    isolated={
        'output_directory':str(out/'isolated'),'allow_reset_test_schema':True,'test_database_dsn_file':str(dsn),
        'gpustack_url':env['MAAS_GPUSTACK_URL'],'bindings_file':str(binding),'catalog_file':str(catalog),'pepper_file':str(pepper),
        'project':'isolated-http-e3','revision':template['revision'],'customer_key_file':str(key),'api_port':18994,
        'base_url':'https://127.0.0.1:18995','ca_file':'/tls/server.crt'}
    isolated_cfg=out/'isolated-config-private.json';save_private(isolated_cfg,isolated)
    proxy=['docker','run','--rm','--name',proxy_name,'--network','host','--read-only','--user','nginx','--cpus','1','--memory','256m',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=64m',
        '-v',str(nginx)+':/etc/nginx/nginx.conf:ro','-v',str(tls)+':/tls:ro','--entrypoint','nginx',nginx_image]
    state={'state':'STARTING','production_api_image':production['Image'],'nginx_image':nginx_image,
        'http_version':'HTTP/1.1, persistent clients, transport retries=0; loopback private TLS',
        'limits':{'api_generator_mock_consumers_shared_cpus':4,'api_generator_mock_consumers_shared_ram_bytes':4*1024**3,'tls_cpus':1,'tls_ram_bytes':256*1024**2},
        'http_concurrency_expansion_above_16':'NOT_EXECUTED; dedicated extension not implemented; no absolute maximum claim'}
    replace_private(out/'status.json',state)
    try:
        command(proxy+['-t']);command(proxy[:2]+['-d']+proxy[2:]+['-g','daemon off;'])
        cmd=['docker','run','--rm','--name',api_name,'--user','0:0','--network','host','--cpus','4','--memory','4g',
            '-v',str(source)+':'+str(source)+':ro','-v',str(out)+':'+str(out),'-v',str(tls)+':/tls:ro',
            '-w',str(source),'-e','PYTHONPATH='+str(source),production['Image'],'python','-m','maas_bench.http_experiment','--config',str(isolated_cfg),'--execute']
        with (out/'isolated-output-private.log').open('w') as log:
            Path(log.name).chmod(0o600)
            p=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=2400)
        if p.returncode:raise RuntimeError('Disposable HTTP experiment stopped; inspect private evidence')
        isolated_result=read_private(out/'isolated/status.json')
        if isolated_result['state']!='COMPLETE':raise RuntimeError('Disposable HTTP evidence incomplete')
        state.update(state='REAL_OCR_CONFIRMATION',isolated_state='COMPLETE');replace_private(out/'status.json',state)
    except BaseException as exc:
        state.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(out/'status.json',state);raise
    finally:
        # Only the experiment's own ephemeral containers are stopped.
        for name in (api_name,proxy_name):subprocess.run(['docker','stop','-t','20',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    params=queue['selected_candidate']['parameters'];q=min(params['global_queue'],params['project_queue'])
    previous=config['queue_proof_file'];cases=[]
    for case in ('T4-HTTP-ACCEPT','T4-HTTP-REJECT','T4-HTTP-RECOVER'):
        tiers=[c for c in (1,2,4,8,16) if c<=q] if case.endswith('ACCEPT') else [16] if case.endswith('REJECT') else [1]
        points=[]
        for c in tiers:
            count=c if case.endswith('ACCEPT') else min(200,q*4) if case.endswith('REJECT') else min(3,q)
            points.append({'point_id':case+'-C'+str(c),'parameters':params,'mode':'bounded_burst','arrival_policy':{'count':count,'clients':c}})
        dep='T4-QUEUE' if case.endswith('ACCEPT') else 'T4-HTTP-ACCEPT' if case.endswith('REJECT') else 'T4-HTTP-REJECT'
        phase=Phase({**config,'output_directory':str(out/case.lower())},case,points,[(dep,previous,('COMPLETE',))])
        try:
            for n,p in enumerate(points):
                cfg=phase.point_config(p,'t4-http-'+case.rsplit('-',1)[1].lower()+'-20261029-'+str(n));v=execute_arrivals(cfg)
                phase.completed({k:v[k] for k in v if k not in ('accepted_task_ids','download_verification')})
            phase.finish(scope='bounded real OCR bursts, not stable HTTP or GPU capacity',
                skipped_queue_budget=[c for c in (1,2,4,8,16) if c>q] if case.endswith('ACCEPT') else [],
                queue_full_observed=any(r['queue_full_rejections']>0 for r in phase.status['points']))
        except BaseException as exc:
            phase.interrupted(exc);state.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(out/'status.json',state);raise
        finally:phase.close()
        previous=str(phase.output/'status.json');cases.append({'case_id':case,'state':phase.status['state'],'proof_file':previous})
    state.update(state='COMPLETE',real_cases=cases);replace_private(out/'status.json',state)


def save_text(path,text):
    fd=__import__('os').open(path,__import__('os').O_WRONLY|__import__('os').O_CREAT|__import__('os').O_EXCL,0o600)
    with __import__('os').fdopen(fd,'w') as f:f.write(text)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
