"""Independent host-side checks. No secret values or outbound notifications."""
import json,os,ssl,subprocess,time,urllib.request,urllib.error,html,math
from collections import deque
from pathlib import Path
ROOT=Path('/root/wtaobj/maas-framework');LIVE=Path('/root/wtaobj/maas-live')
COMPOSE=['docker','compose','--env-file',str(LIVE/'app.env'),'-f',str(ROOT/'deploy/maas/compose.yaml')]


def inspect_live():
    alerts=[];metrics={}
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
        keep={'maas_successful_tasks_total','maas_successful_sync_requests_total','maas_artifact_live_bytes','maas_artifact_free_bytes','maas_oldest_queue_seconds','maas_unknown_backend_activity','maas_unknown_sync_activity'}
        for line in (volume/'.observability/metrics.prom').read_text().splitlines():
            parts=line.split()
            if len(parts)==2 and parts[0] in keep:
                value=float(parts[1])
                if math.isfinite(value):metrics[parts[0]]=value
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
    return {'generated_at_unix':time.time(),'ok':not alerts,'alerts':sorted(set(alerts)),'metrics':metrics}


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
    history=folder/'history.jsonl'
    if history.exists() and history.stat().st_size>5*1024**2:os.replace(history,folder/'history.previous.jsonl')
    fd=os.open(history,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    with os.fdopen(fd,'a') as f:f.write(json.dumps(value)+'\n')
    with history.open() as f:recent=[json.loads(x) for x in deque(f,maxlen=120)]
    rates=[]
    for before,after in zip(recent,recent[1:]):
        dt=after['generated_at_unix']-before['generated_at_unix']
        delta=after['metrics'].get('maas_successful_tasks_total',0)-before['metrics'].get('maas_successful_tasks_total',0)
        rates.append(max(0,delta)*60/dt if dt>0 else 0)
    maximum=max(rates,default=1) or 1
    points=' '.join(f'{i*700/max(1,len(rates)-1):.1f},{150-rate/maximum*140:.1f}' for i,rate in enumerate(rates))
    table=''.join('<tr><td>'+html.escape(k)+'</td><td>'+str(v)+'</td></tr>' for k,v in sorted(value['metrics'].items()))
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MaaS私有运行看板</title><style>body{font:16px/1.8 system-ui;background:#eef3f8;color:#203448;max-width:1000px;margin:24px auto;padding:20px}table{background:white;width:100%;border-collapse:collapse}td{padding:10px;border-bottom:1px solid #ddd}svg{background:white;width:100%;max-width:720px}code{overflow-wrap:anywhere}</style><h1>MaaS私有运行看板</h1><p>状态：'+('正常' if value['ok'] else '需要检查')+' · Unix观测时间：'+str(round(value['generated_at_unix']))+'</p><p>告警：'+html.escape(', '.join(value['alerts']) or '无')+'</p><h2>最近120个采样的成功任务速率</h2><p>每分钟，当前图上限 '+str(round(maximum,2))+'</p><svg viewBox="0 0 700 160"><polyline fill="none" stroke="#167c6a" stroke-width="3" points="'+points+'"/></svg><h2>聚合指标</h2><table>'+table+'</table><p>私有快照；每次监控采样更新，不包含项目、任务ID或凭据。history.jsonl及上一份文件各最多约5MiB。</p></html>'
    temp=folder/'dashboard.new';fd=os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(page)
    os.replace(temp,folder/'dashboard.html')


if __name__=='__main__':main()
