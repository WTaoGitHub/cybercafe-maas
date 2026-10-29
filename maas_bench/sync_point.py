"""A quota-limited synchronous point with independent activity/resource checks."""
import json
import os
from pathlib import Path
import threading
import time
import httpx
from . import matrix
from .backend import DatabaseObserver
from .contracts import Parameters
from .prepared import timed_prepared
from .resources import ResourceGuard
from .runtime import ControllerProbe
from .saturation import SupplyMonitor
from .snapshot import read_private,read_secret,save_private,digest,RuntimeFence
from .sync_load import SyncTraffic


def execute_sync(config):
    manifest=read_private(config['fixture_file']);matrix.validate_execution(config,digest(manifest))
    if config['load_mode']!='sync_prepared':raise ValueError('Synchronous point required')
    p=Parameters(**config['parameters']);snapshot=read_private(config['snapshot_file']);fence=RuntimeFence(snapshot)
    if snapshot['parameters']!=p.to_dict():raise ValueError('Unapplied synchronous parameters')
    probe=ControllerProbe(config['source_root'],config['model_id'],config['profile_id'],digest(manifest));fence.check(probe.capture())
    folder=Path(config['output_parent'])/config['run_id'];folder.mkdir(mode=0o700,exist_ok=False)
    observer=DatabaseObserver(read_secret(config['database_dsn_file']),config['project'],config['logical_model'],config['revision'],'e3-'+config['run_id']+':')
    abort=threading.Event();halt=threading.Event();errors=[];lock=threading.Lock()
    log=os.fdopen(os.open(folder/'events.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')
    def emit(row):
        with lock:
            log.write(json.dumps(row)+'\n');log.flush()
            if row['kind'] not in ('sample','resources'):os.fsync(log.fileno())
    def fail(exc):errors.append(type(exc).__name__);emit({'kind':'failed','private_message':str(exc)});abort.set()
    monitor=SupplyMonitor(p,snapshot['generation'],.25);resources=ResourceGuard(snapshot['workers'])
    traffic=SyncTraffic({**config,'sync_rate':1},observer,fail,emit)
    initial=observer.snapshot()
    if any(initial[k] for k in ('global_queued','running','sync_running','unknown','sync_unknown')):raise RuntimeError('Exclusive synchronous window required')
    traffic.before={r['token'] for r in traffic._rows()}
    def sample():
        previous=time.monotonic()
        try:
            while not halt.is_set():
                a=time.monotonic();row=observer.snapshot();b=time.monotonic();monitor.observe(row,a,b,False)
                if row['global_queued'] or row['running']:raise RuntimeError('Async activity in pure synchronous window')
                if b-previous>.25:raise RuntimeError('Synchronous activity sampling gap')
                previous=b;emit({'kind':'sample','at':b,'sync_running':row['sync_running'],'project_sync_running':row['project_sync_running']});halt.wait(.05)
        except Exception as exc:fail(exc)
    def watch():
        try:
            while not halt.is_set():
                fence.check(probe.capture());rows=probe.resources(snapshot);controller=resources.controller();resources.check(rows,controller,time.monotonic())
                emit({'kind':'resources','workers':rows,'controller':controller});halt.wait(.5)
        except Exception as exc:fail(exc)
    threads=[threading.Thread(target=f) for f in (sample,watch)]
    for thread in threads:thread.start()
    try:
        with httpx.Client(base_url=config['base_url'],headers={'Authorization':'Bearer '+read_secret(config['customer_key_file'])},
            timeout=35,trust_env=False,transport=httpx.HTTPTransport(retries=0,verify=config.get('ca_file',True))) as client:
            def invoke(item,sequence):
                value=traffic._one(client,sequence,time.monotonic())
                if value['http_status']!=200:raise RuntimeError('Unexpected rejection with clients <= project slots')
                return value
            result=timed_prepared(invoke,traffic.inputs,min(p.global_limit,p.project_limit),seconds=60,min_successes=200,max_seconds=300,stop=abort)
        usage=traffic.stop()
        halt.set()
        for thread in threads:thread.join(timeout=30)
        if errors or result['state']!='COMPLETE':raise RuntimeError('Synchronous point incomplete')
        fence.check(probe.capture())
        result.update(case_id=config['case_id'],point_id=config['point_id'],parameters=p.to_dict(),usage=usage)
        save_private(folder/'summary.json',result)
        return result
    except BaseException as exc:
        abort.set();save_private(folder/'interrupted.json',{'state':'INTERRUPTED_REQUIRES_INSPECTION','error_class':type(exc).__name__});raise
    finally:
        halt.set();traffic.halt.set()
        for thread in threads:thread.join(timeout=30)
        observer.close();log.close()
