"""T6 two-hour, explicitly segmented mixed workload with no automatic resume."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private
from maas_bench.contracts import Parameters
from maas_bench.__main__ import execute
from maas_bench.arrival_point import execute_arrivals


def planned(params,workload_sha,mu):
    common={'parameters':params,'workload_sha256':workload_sha};points=[{**common,'point_id':'T6-SOAK-qualify','mode':'async_saturation'}]
    points.append({**common,'point_id':'T6-SOAK-probe-before','mode':'bounded_burst','arrival_policy':{'count':1,'clients':1}})
    for n in range(24):
        mode=('async_saturation','independent_arrivals','mixed_20','independent_arrivals')[n%4]
        row={**common,'point_id':'T6-SOAK-segment-'+str(n).zfill(2),'mode':mode,'segment':n}
        if mode=='independent_arrivals':row['arrival_policy']={'seconds':300,'rate':mu*.5,'clients':16}
        points.append(row)
        if n%4==3:points.append({**common,'point_id':'T6-SOAK-burst-'+str(n),'mode':'bounded_burst','arrival_policy':{'count':min(200,4*params['project_queue']),'clients':16}})
    points.append({**common,'point_id':'T6-SOAK-probe-after','mode':'bounded_burst','arrival_policy':{'count':1,'clients':1}})
    return points


def storage_budget(phase,qualify_config,mu):
    observer=phase.observer;project=observer.scope[0]
    with observer.lock:
        row=observer.connection.execute("SELECT coalesce(sum(size),0)::bigint bytes FROM artifacts WHERE project=%s AND deleted_at IS NULL",(project,)).fetchone()
        sizes=observer.connection.execute("SELECT avg(a.size) mean_bytes FROM tasks t JOIN artifacts a ON a.id=t.result_artifact_id WHERE t.project=%s AND starts_with(t.idempotency_key,%s)",(project,'e3-'+qualify_config['run_id']+':')).fetchone()
    app=json.loads(subprocess.check_output(['docker','inspect','maas-tasks-api-1'],text=True,stderr=subprocess.PIPE,timeout=15))[0]
    env=dict(x.split('=',1) for x in app['Config']['Env'] if '=' in x)
    quota=int(env['MAAS_MAX_PROJECT_STORAGE_BYTES']);mean=float(sizes['mean_bytes'] or 0)
    if mean<=0:raise RuntimeError('Missing real mixed-workload result storage evidence')
    estimated=math.ceil((5400*mu+6*min(200,4*phase.controller.expected['parameters']['project_queue']))*mean*1.25)
    source=next(m['Source'] for m in app['Mounts'] if m['Destination']=='/var/lib/maas/artifacts')
    free=shutil.disk_usage(source).free
    if row['bytes']+estimated>quota*.9 or free-estimated<max(2*1024**3,int(env['MAAS_MIN_FREE_BYTES'])):
        raise RuntimeError('Two-hour storage reservation insufficient; no quota increase, cleanup or hidden load reduction')
    return {'current_project_bytes':row['bytes'],'project_quota_bytes':quota,'measured_mean_mixed_result_bytes':mean,
        'forecast_new_result_bytes':estimated,'forecast_margin':1.25,'reserve_fraction':.1,'disk_free_bytes':free,
        'limitation':'forecast from measured mix; not a hard bound, actual quota remains active'}


def run(config):
    template=read_private(config['template_file']);confirmed,_=proof_entry(config['confirmation_proof_file'],'T6-CONFIRM',template)
    claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',template)
    params=confirmed['selected_candidate']['parameters'];workload_sha=claim['mixed_workload_sha256']
    # A separate qualifying point determines the mixed completion rate. It is
    # not spliced into the following two-hour observation.
    qualify=[{'point_id':'T6-SOAK-qualify','parameters':params,'workload_sha256':workload_sha,'mode':'async_saturation'}]
    prerequisite=[('T6-CONFIRM',config['confirmation_proof_file'],('COMPLETE',))]
    extra={'workload_fixture_file':claim['mixed_workload_fixture_file'],'workload_sha256':workload_sha,
        'expected_text_file':claim['mixed_quality_file'],'raw_manifest_file':claim['mixed_raw_file']}
    qphase=Phase({**config,'output_directory':config['output_directory']+'-qualification'},'T6-SOAK',qualify,prerequisite)
    try:
        cfg=qphase.point_config(qualify[0],'t6-mixed-qualify-20261029',policy={'seconds':120,'min_successes':200,'max_seconds':300},**extra)
        value=execute(cfg)
        if value['state']!='COMPLETE':raise RuntimeError('Mixed-workload qualification invalid')
        mu=value['throughput_per_second'];budget=storage_budget(qphase,cfg,mu)
        qphase.completed({'point_id':qualify[0]['point_id'],'parameters':params,'state':'COMPLETE','mixed_throughput':mu,'storage_budget':budget})
        qphase.finish()
    except BaseException as exc:qphase.interrupted(exc);raise
    finally:qphase.close()
    points=planned(params,workload_sha,mu)[1:]
    phase=Phase(config,'T6-SOAK',points,prerequisite);started=time.monotonic();formal_seconds=0;maintenance=[]
    try:
        for number,p in enumerate(points):
            if p.get('segment')==12:
                a=time.monotonic();smaller={**params,'executors':max(1,params['executors']-1)}
                phase.controller.change(Parameters(**smaller));phase.controller.change(Parameters(**params))
                maintenance.append({'operation':'one executor drained shrink and restored with real resume probes','seconds':time.monotonic()-a,
                    'from_executors':params['executors'],'temporary_executors':smaller['executors'],'unknown_replayed':False})
            kwargs={**extra}
            if p['mode'] in ('async_saturation','mixed_20'):kwargs['policy']={'seconds':300,'min_successes':200,'max_seconds':300}
            if p['mode']=='mixed_20':kwargs['sync_rate']=mu*.2
            cfg=phase.point_config(p,'t6-soak-20261029-'+str(number).zfill(2),**kwargs)
            if p['mode'] in ('async_saturation','mixed_20'):
                v=execute(cfg)
                if v['state']!='COMPLETE':raise RuntimeError('Soak saturated segment invalid')
                raw=read_private(Path(cfg['output_parent'])/cfg['run_id']/'summary.json')
                row={k:raw[k] for k in ('state','parameters','measurement_seconds','throughput_per_second','supply','workload_sha256','workload_kind')}
                row['accepted']=len(raw['accepted_task_ids']);row['successful_usage_events']=sum(r['events'] for r in raw['reconciliation'])
                if 'auxiliary' in raw:row['sync']={k:raw['auxiliary'][k] for k in ('offered_rate','successes','capacity_rejections','usage_events')}
            else:
                raw=execute_arrivals(cfg);row={k:v for k,v in raw.items() if k not in ('accepted_task_ids','download_verification')}
            row.update(point_id=p['point_id'],mode=p['mode'])
            if 'segment' in p:formal_seconds+=row['measurement_seconds']
            phase.completed(row)
        elapsed=time.monotonic()-started
        if formal_seconds<7200 or elapsed<7200 or len(maintenance)!=1:raise RuntimeError('Two-hour soak coverage incomplete')
        phase.finish(formal_segment_seconds=formal_seconds,wall_seconds=elapsed,controlled_executor_drain=maintenance,
            storage_budget=budget,selected_parameters=params,production_promoted=False,
            saturation_scope='only explicitly marked saturated segments; business arrival, burst, drain and maintenance intervals are separate',
            sla_claim=False,vm_worker_docker_daemon_restarts=0)
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
