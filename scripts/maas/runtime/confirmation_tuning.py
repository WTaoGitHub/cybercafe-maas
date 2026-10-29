"""T6 repeated five-minute capacity and common-arrival latency confirmation."""
import argparse
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,replace_private
from maas_bench.__main__ import execute
from maas_bench.arrival_point import execute_arrivals


def summarize(candidates,rows):
    groups=[]
    for candidate in candidates:
        matching=[r['throughput_per_second'] for r in rows if r['parameters']==candidate['parameters']]
        if len(matching)!=3:raise ValueError('Three complete independent windows required')
        groups.append({**candidate,'repeated_throughputs':matching,'mean_throughput':statistics.mean(matching),
            'min_throughput':min(matching),'sample_stdev':statistics.stdev(matching)})
    best=max(g['mean_throughput'] for g in groups)
    band=[g for g in groups if g['mean_throughput']>=best*.95]
    return groups,min(band,key=lambda g:(g['parameters']['replicas'],g['parameters']['executors']))


def run(config):
    template=read_private(config['template_file']);claim,_=proof_entry(config['claim_proof_file'],'T5-CLAIM',template)
    candidates=claim['final_candidates']
    if not 1<=len(candidates)<=2:raise ValueError('One or two joint tuples required')
    baseline=read_private(config['baseline_parameters_file'])['parameters'];points=[]
    for repeat in (1,2,3):
        order=[('candidate'+str(i),c['parameters']) for i,c in enumerate(candidates)]
        order.insert(repeat%len(order),('baseline',baseline))
        for role,params in order:points.append({'point_id':'T6-r'+str(repeat)+'-'+role,'parameters':params,'mode':'async_saturation','role':role,'repeat':repeat})
    phase=Phase(config,'T6-CONFIRM',points,[('T5-CLAIM',config['claim_proof_file'],('COMPLETE',))])
    try:
        for n,p in enumerate(points):
            cfg=phase.point_config(p,'t6-confirm-20261029-'+str(n),policy={'seconds':300,'min_successes':200,'max_seconds':300})
            value=execute(cfg)
            if value['state']!='COMPLETE':raise RuntimeError('Five-minute confirmation incomplete')
            raw=read_private(Path(cfg['output_parent'])/cfg['run_id']/'summary.json')
            phase.completed({'point_id':p['point_id'],'parameters':p['parameters'],'repeat':p['repeat'],'role':p['role'],'state':'COMPLETE',
                'measurement_seconds':raw['measurement_seconds'],'throughput_per_second':raw['throughput_per_second'],'supply':raw['supply']})
        groups,selected=summarize(candidates,[r for r in phase.status['points'] if r['role']!='baseline'])
        phase.finish(candidate_repeats=groups,provisional_selected=selected,within_five_percent_rule='unresolved small differences; prefer lower R/E, no claim of statistical superiority')
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()
    minimum=min(g['min_throughput'] for g in groups);latency_points=[]
    for fraction in (.5,.8):
        for i,c in enumerate(candidates):latency_points.append({'point_id':'T6-common-'+str(fraction)+'-candidate'+str(i),'parameters':c['parameters'],
            'mode':'independent_arrivals','arrival_policy':{'rate':minimum*fraction,'seconds':180,'clients':16},'fraction':fraction})
    phase.status['state']='LATENCY_RUNNING';phase.record()
    latency=Phase({**config,'output_directory':config['output_directory']+'-latency'},'T6-CONFIRM',latency_points,[('T5-CLAIM',config['claim_proof_file'],('COMPLETE',))])
    try:
        for n,p in enumerate(latency_points):
            cfg=latency.point_config(p,'t6-latency-20261029-'+str(n));v=execute_arrivals(cfg)
            latency.completed({k:v[k] for k in v if k not in ('accepted_task_ids','download_verification')})
        eligible=[]
        for group in groups:
            values=[r for r in latency.status['points'] if r['parameters']==group['parameters'] and r['queue_full_rejections']==0 and r['total_p95']<=1.5 and r['total_p99']<=3]
            if values:eligible.append({**group,'validated_arrival_rate':max(r['target_rate'] for r in values)})
        if not eligible:raise RuntimeError('No candidate meets predeclared latency budget at either common arrival rate')
        best=max(g['mean_throughput'] for g in eligible);chosen=min((g for g in eligible if g['mean_throughput']>=best*.95),key=lambda g:(g['parameters']['replicas'],g['parameters']['executors']))
        latency.finish(common_rate_reference=minimum,selected_candidate=chosen)
        phase.status.update(state='COMPLETE',selected_candidate=chosen,latency_proof_file=str(latency.output/'status.json'));phase.record()
    except BaseException as exc:latency.interrupted(exc);phase.status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);phase.record();raise
    finally:latency.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
