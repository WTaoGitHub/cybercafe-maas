"""Real OCR rejection -> drain -> recovery without intervening reconfiguration."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,save_private,digest,RuntimeFence
from maas_bench.arrival_point import execute_arrivals


def check_point(row, recovery=False):
    if (row['state']!='COMPLETE' or row['accepted']<=0
        or row['accepted']+row['queue_full_rejections']!=row['target_arrivals']
        or row['actual_completed_calls']!=row['target_arrivals']
        or row['unsent'] or row['late_sent_calls'] or row['not_scheduled_after_stop']):
        raise RuntimeError('Incomplete recovery experiment')
    if recovery and row['queue_full_rejections']:raise RuntimeError('Fresh recovery request rejected')
    if not recovery and not row['queue_full_rejections']:raise RuntimeError('Queue-full precondition not proven')


def run(config):
    template=read_private(config['template_file'])
    queue,_=proof_entry(config['queue_proof_file'],'T4-QUEUE',template)
    reject,_=proof_entry(config['reject_proof_file'],'T4-HTTP-REJECT',template)
    if not reject.get('queue_full_observed'):raise ValueError('Measured rejection prerequisite required')
    params=queue['selected_candidate']['parameters'];q=min(params['global_queue'],params['project_queue'])
    points=[{'point_id':'T4-HTTP-RECOVER-CONTINUOUS-PRESSURE','parameters':params,'mode':'bounded_burst','arrival_policy':{'count':min(200,q*4),'clients':16}},
            {'point_id':'T4-HTTP-RECOVER-CONTINUOUS-FRESH','parameters':params,'mode':'bounded_burst','arrival_policy':{'count':min(3,q),'clients':1}}]
    phase=Phase(config,'T4-HTTP-RECOVER',points,[('T4-QUEUE',config['queue_proof_file'],('COMPLETE',)),('T4-HTTP-REJECT',config['reject_proof_file'],('COMPLETE',))])
    try:
        cfg=phase.point_config(points[0],'http-recovery-pressure')
        row=execute_arrivals(cfg);check_point(row)
        phase.completed({k:v for k,v in row.items() if k not in ('accepted_task_ids','download_verification')})
        boundary=phase.probe.capture();save_private(phase.output/'after-pressure-private.json',boundary)
        cfg=phase.point_config(points[1],'http-recovery-fresh')
        current=phase.probe.capture();RuntimeFence(boundary).check(current)
        save_private(phase.output/'before-fresh-private.json',current)
        row=execute_arrivals(cfg);check_point(row,recovery=True)
        phase.completed({k:v for k,v in row.items() if k not in ('accepted_task_ids','download_verification')})
        after=phase.probe.capture();RuntimeFence(boundary).check(after)
        save_private(phase.output/'after-fresh-private.json',after)
        phase.finish(continuity_verified=True,continuity_generation=boundary['generation'],
                     boundary_snapshot_sha256=digest(boundary),queue_full_observed=True,
                     scope='same configuration/model instances/application containers/generation between drained rejection and fresh requests; restoration only after both')
        return phase.status
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print('DRY_RUN; execution disabled')
