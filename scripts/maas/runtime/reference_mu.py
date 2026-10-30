"""Three independent saturation windows per screened tuple, no old rate reuse."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,digest
from maas_bench.controller import fixed_profile
from maas_bench.queue_policy import conservative_mu
from maas_bench.__main__ import execute


def run(config):
    template=read_private(config['template_file'])
    proof,_=proof_entry(config['extend_proof_file'],'T3-EXTEND',template,('COMPLETE','SKIPPED_CONDITION_NOT_MET'))
    candidates=proof['screened_candidates']
    if not 1<=len(candidates)<=2:raise ValueError('One or two screened candidate tuples required')
    points=[{'point_id':f'T3-MU-c{i}-r{r}','parameters':c['parameters'],'mode':'async_saturation'}
        for i,c in enumerate(candidates) for r in (1,2,3)]
    phase=Phase(config,'T3-MU',points,[('T3-LIMITS',config['limits_proof_file'],('COMPLETE',)),
        ('T3-EXTEND',config['extend_proof_file'],('COMPLETE','SKIPPED_CONDITION_NOT_MET'))])
    try:
        for i,p in enumerate(points):
            cfg=phase.point_config(p,'t3-mu-'+str(i).zfill(3)+'-capacity')
            result=execute(cfg)
            if result['state']!='COMPLETE':raise RuntimeError('Invalid capacity reference window')
            path=Path(cfg['output_parent'])/cfg['run_id']/'summary.json';value=read_private(path)
            row={k:value[k] for k in ('point_id','state','parameters','measurement_seconds','measured_successes','throughput_per_second','supply')}
            row.update(run_id=cfg['run_id'],source_summary_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            phase.completed(row)
        refs=[]
        for i,c in enumerate(candidates):
            rows=[p for p in phase.status['points'] if p['point_id'].startswith(f'T3-MU-c{i}-')]
            refs.append({'parameters':c['parameters'],'conservative_mu':conservative_mu(rows,c['parameters'])})
        phase.finish(reference_candidates=refs,fixed_profile_sha256=digest(phase.controller.fixed),rate_kind='minimum of three independently verified successful completion windows')
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if not a.execute:print(json.dumps({'state':'DRY_RUN','configuration_validated':False,'execution':False}))
    else:
        try:run(read_private(a.config))
        except Exception as exc:print(json.dumps({'state':'STOPPED','error_class':type(exc).__name__}));raise SystemExit(2)
