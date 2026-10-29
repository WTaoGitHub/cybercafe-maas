"""T3 paired extension gate followed by bounded twelve-slot experiments."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.phase import Phase,proof_entry
from maas_bench.snapshot import read_private,replace_private
from maas_bench.__main__ import execute


def extension_condition(rows):
    gains=[]
    if {r['repeat'] for r in rows}!={1,2}:raise ValueError('Two independent paired repeats required')
    for repeat in (1,2):
        pair=[r for r in rows if r['repeat']==repeat]
        if len(pair)!=2 or {r['parameters']['executors'] for r in pair}!={6,8}:raise ValueError('Complete six/eight pairs required')
        by={r['parameters']['executors']:r['throughput_per_second'] for r in pair}
        if min(by.values())<=0 or any(r['state']!='COMPLETE' for r in pair):raise ValueError('Only complete positive capacity windows')
        gains.append(by[8]/by[6]-1)
    return {'trigger_met':min(gains)>=.05,'paired_gains_fraction':gains,'resource_guards_passed':True,
        'comparison':'same R; E/G/P=8 vs 6, two interleaved independent windows'}


def run(config):
    template=read_private(config['template_file']);limits,_=proof_entry(config['limits_proof_file'],'T3-LIMITS',template)
    old,_=proof_entry(config['async_proof_file'],'T2-ASYNC',template)
    base=max((p for p in limits['points'] if p['mode']=='async_saturation' and p['parameters']['global_limit']==8 and p['parameters']['project_limit']==8),key=lambda p:p['throughput_per_second'])
    params=base['parameters'];pairs=[]
    for repeat in (1,2):
        for n in ((6,8) if repeat==1 else (8,6)):
            pairs.append({'point_id':'T3-GATE-r'+str(repeat)+'-E'+str(n),'parameters':{**params,'executors':n,'global_limit':n,'project_limit':n},'mode':'async_saturation','repeat':repeat})
    gate=Phase({**config,'output_directory':config['output_directory']+'-gate'},'T3-LIMITS',pairs,[('T2-ASYNC',config['async_proof_file'],('COMPLETE',))])
    try:
        for n,p in enumerate(pairs):
            cfg=gate.point_config(p,'t3-gate-20261029-'+str(n));v=execute(cfg)
            if v['state']!='COMPLETE':raise RuntimeError('Extension gate point invalid')
            gate.completed({'point_id':p['point_id'],'parameters':p['parameters'],'repeat':p['repeat'],'throughput_per_second':v['throughput_per_second'],'state':'COMPLETE'})
        condition=extension_condition(gate.status['points'])
        gate.finish(condition=condition)
    except BaseException as exc:gate.interrupted(exc);raise
    finally:gate.close()
    if not condition['trigger_met']:
        folder=Path(config['output_directory']);folder.mkdir(mode=0o700,exist_ok=False)
        result={k:gate.status[k] for k in ('profile_id','input_sha256')}
        result.update(case_id='T3-EXTEND',state='SKIPPED_CONDITION_NOT_MET',condition=condition,
            screened_candidates=[{'parameters':params,'screening_throughput':base['throughput_per_second']}])
        replace_private(folder/'status.json',result);return
    points=[{'point_id':'T3-E12-R'+str(r),'parameters':{**params,'replicas':r,'executors':12,'global_limit':12,'project_limit':12},'mode':'async_saturation'} for r in (8,10,12)]
    phase=Phase(config,'T3-EXTEND',points,[('T3-LIMITS',config['limits_proof_file'],('COMPLETE',))],condition)
    try:
        candidates=[{'parameters':params,'screening_throughput':base['throughput_per_second']}]
        for n,p in enumerate(points):
            cfg=phase.point_config(p,'t3-extend-20261029-'+str(n));v=execute(cfg)
            if v['state']!='COMPLETE':raise RuntimeError('Conditional extension invalid')
            raw=read_private(Path(cfg['output_parent'])/cfg['run_id']/'summary.json')
            row={'point_id':p['point_id'],'parameters':p['parameters'],'throughput_per_second':v['throughput_per_second'],
                'state':'COMPLETE','supply':raw['supply'],'measurement_seconds':raw['measurement_seconds']}
            phase.completed(row);candidates.append({'parameters':p['parameters'],'screening_throughput':v['throughput_per_second']})
        best=max(c['screening_throughput'] for c in candidates)
        selected=sorted((c for c in candidates if c['screening_throughput']>=best*.95),key=lambda c:(c['parameters']['replicas'],c['parameters']['executors']))[:2]
        if not selected:raise RuntimeError('No extension candidate')
        phase.finish(screened_candidates=selected)
    except BaseException as exc:phase.interrupted(exc);raise
    finally:phase.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
