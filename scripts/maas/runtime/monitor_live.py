"""Independent host-side checks. No secret values or outbound notifications."""
import json,os,ssl,subprocess,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def inspect_live():
    alerts=[]
    def add(code):alerts.append(code)
    try:
        ids=subprocess.check_output(COMPOSE+['ps','-aq'],text=True,stderr=subprocess.PIPE,timeout=8).split()
        rows=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True,stderr=subprocess.PIPE,timeout=8))
        seen={c['Config']['Labels']['com.docker.compose.service']:c for c in rows}
        for name in ('api','executor','observer','db'):
            c=seen.get(name)
            if not c or not c['State']['Running'] or c['State'].get('Health',{}).get('Status')=='unhealthy':add('SERVICE_DOWN_'+name.upper())
        mounts=seen['api']['Mounts'];volume=Path(next(m['Source'] for m in mounts if m['Destination']=='/var/lib/maas/artifacts'))
        d=json.loads((volume/'.observability/status.json').read_text())
        if not d['ok'] or not 0<=time.time()-d['generated_at_unix']<90:add('OBSERVER_STALE_OR_FAILED')
        for item in d['alerts']:
            if item['severity']!='info':add(item['code'])
    except Exception:add('INSPECTION_UNAVAILABLE')
    try:
        with urllib.request.urlopen('http://127.0.0.1:18830/healthz',timeout=3) as r:assert r.status==200
    except Exception:add('API_HEALTH_FAILED')
    try:
        d=json.loads(Path('/root/wtaobj/maas-runtime/health.json').read_text())
        if not d['ok'] or not 0<=time.time()-d['updated_at']<30:add('COLLECTOR_FAILED_OR_STALE')
    except Exception:add('COLLECTOR_FAILED_OR_STALE')
    try:
        context=ssl.create_default_context(cafile='/root/wtaobj/maas-private-tls/server.crt')
        try:urllib.request.urlopen('https://127.0.0.1:18832/',context=context,timeout=3)
        except urllib.error.HTTPError as e:assert e.code==404
        else:raise AssertionError('Unexpected edge route')
    except Exception:add('TLS_HEALTH_FAILED')
    return {'generated_at_unix':time.time(),'ok':not alerts,'alerts':sorted(set(alerts))}


def main():
    folder=LIVE/'monitor';folder.mkdir(mode=0o700,exist_ok=True);path=folder/'status.json'
    previous=set()
    if path.exists():
        try:previous=set(json.loads(path.read_text())['alerts'])
        except Exception:pass
    value=inspect_live();current=set(value['alerts'])
    for code in sorted(current-previous):print('alert_open code='+code,flush=True)
    for code in sorted(previous-current):print('alert_resolved code='+code,flush=True)
    temp=path.with_suffix('.new');fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


if __name__=='__main__':main()
