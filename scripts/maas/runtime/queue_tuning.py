"""T4 fixed arrivals and bounded bursts; no saturation ranking or deadline extension."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private
from maas_bench.arrival_point import execute_arrivals


def planned(candidate):
    base=candidate['parameters'];mu=candidate['screening_throughput'];points=[]
    for q in (10,25,50,100,200):
        params={**base,'global_queue':q,'project_queue':q}
        for count in (25,50,100,200):
            points.append({'point_id':'T4-Q'+str(q)+'-burst'+str(count),'parameters':params,'mode':'bounded_burst','arrival_policy':{'count':count,'clients':16}})
        for fraction in (.5,.8,1.,1.2):
            points.append({'point_id':'T4-Q'+str(q)+'-rate'+str(fraction),'parameters':params,'mode':'independent_arrivals',
                'arrival_policy':{'rate':mu*fraction,'seconds':180,'clients':16},'fraction':fraction})
        points.append({'point_id':'T4-Q'+str(q)+'-burst-rate2','parameters':params,'mode':'independent_arrivals','arrival_policy':{'rate':mu*2,'seconds':10,'clients':16}})
    return points


def run(config):
    template=read_private(config['template_file']);proof,_=proof_entry(config['extend_proof_file'],'T3-EXTEND',template,('COMPLETE','SKIPPED_CONDITION_NOT_MET'))
    # Lower resources win within the predeclared 5% screening band.
    candidate=min(proof['screened_candidates'],key=lambda c:(c['parameters']['replicas'],c['parameters']['executors']))
    points=planned(candidate)
    phase=Phase(config,'T4-QUEUE',points,[('T3-LIMITS',config['limits_proof_file'],('COMPLETE',)),('T3-EXTEND',config['extend_proof_file'],('COMPLETE','SKIPPED_CONDITION_NOT_MET'))])
    try:
        for n,p in enumerate(points):
            cfg=phase.point_config(p,'t4-queue-20261029-'+str(n).zfill(3));v=execute_arrivals(cfg)
            if v['state']!='COMPLETE':raise RuntimeError('Queue experiment invalid')
            phase.completed({k:v[k] for k in v if k not in ('accepted_task_ids','download_verification')})
        rows=phase.status['points'];eligible=[]
        for q in (10,25,50,100,200):
            normal=[v for v in rows if v['parameters']['project_queue']==q and '-rate0.' in v['point_id']]
            allq=[v for v in rows if v['parameters']['project_queue']==q]
            slots=min(candidate['parameters'][k] for k in ('executors','global_limit','project_limit'))
            if q>2*slots and len(normal)==2 and all(v['queue_full_rejections']==0 for v in normal) and max(v['wait_p95'] or 0 for v in allq)<=5:
                eligible.append(q)
        if not eligible:raise RuntimeError('No queue candidate meets predeclared five-second waiting budget')
        q=min(eligible);params={**candidate['parameters'],'global_queue':q,'project_queue':q}
        phase.finish(reference_mu=candidate['screening_throughput'],selected_candidate={'parameters':params,'waiting_budget_seconds':5},
            queue_rejections_are_expected=True,capacity_ranked=False,selection_rule='smallest tested Q above 2*execution_slots with no normal 0.5/0.8mu rejection and all-case wait P95 <=5s; reserve refill margin')
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
