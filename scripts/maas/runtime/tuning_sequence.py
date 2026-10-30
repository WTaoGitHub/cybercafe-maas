"""Continue T3-T6 once the existing T3 run finishes; stop at first anomaly."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from maas_bench.snapshot import read_private,save_private,replace_private


def run(config):
    from maas_bench.experiment import matrix_for
    if matrix_for(read_private(config['template_file'])).get('queue_policy'):
        raise ValueError('Use explicit v2 MU/queue phases; legacy automatic continuation disabled')
    root=Path(config['output_directory']);root.mkdir(mode=0o700,exist_ok=False)
    source=Path(config['source_root']);template=read_private(config['template_file'])
    template['source_root']=str(source);template_file=root/'template-private.json';save_private(template_file,template)
    state={'state':'WAITING_EXISTING_T3','completed':[],'automatic_retries':0,'vm_restarts':0};replace_private(root/'status.json',state)
    try:
        while True:
            limits=read_private(config['limits_status_file'])
            if limits['state']=='COMPLETE':break
            if limits['state'] not in ('RUNNING','RESTORING'):raise RuntimeError('Existing T3 interrupted; do not continue')
            time.sleep(5)
        limits_file=root/'t3-limits-proof.json';save_private(limits_file,limits)
        paths={'limits_proof_file':str(limits_file),'async_proof_file':config['async_proof_file'],
            'test_env_file':config['test_env_file'],'boundary_manifest_file':config['boundary_manifest_file'],
            'boundary_manifest_sha256':config['boundary_manifest_sha256'],'baseline_parameters_file':config['baseline_parameters_file']}
        stages=[('T3-EXTEND','extended_tuning.py','extend'),('T4-QUEUE','queue_tuning.py','queue'),
            ('T4-HTTP','http_ingress_tuning.py','http'),('T5-CLAIM','claim_tuning.py','claim'),
            ('T6-CONFIRM','confirmation_tuning.py','confirmation'),('T6-SOAK','soak_tuning.py','soak')]
        for stage,script,short in stages:
            cfg={**paths,'template_file':str(template_file),'output_directory':str(root/short),'run_tag':config.get('run_tag','r1')}
            cfgfile=root/(short+'-config-private.json');save_private(cfgfile,cfg)
            state.update(state='RUNNING',stage=stage);replace_private(root/'status.json',state);print(json.dumps({'stage':stage,'state':'RUNNING'}),flush=True)
            with (root/(short+'-output-private.log')).open('w') as log:
                Path(log.name).chmod(0o600)
                result=subprocess.run([sys.executable,str(source/'scripts/maas/runtime'/script),'--config',str(cfgfile),'--execute'],stdout=log,stderr=subprocess.STDOUT)
            if result.returncode:raise RuntimeError('Stage failed; inspect private evidence; no automatic replay/restore')
            proof=read_private(root/short/'status.json')
            if proof['state']!='COMPLETE' and not (stage=='T3-EXTEND' and proof['state']=='SKIPPED_CONDITION_NOT_MET'):raise RuntimeError('Stage proof incomplete')
            state['completed'].append({'case_id':stage,'state':proof['state']});replace_private(root/'status.json',state)
            print(json.dumps({'stage':stage,'state':proof['state']}),flush=True)
            if short=='extend':paths['extend_proof_file']=str(root/short/'status.json')
            elif short=='queue':paths['queue_proof_file']=str(root/short/'status.json')
            elif short=='http':paths['http_recovery_proof_file']=next(p['proof_file'] for p in proof['real_cases'] if p['case_id']=='T4-HTTP-RECOVER')
            elif short=='claim':paths['claim_proof_file']=str(root/short/'status.json')
            elif short=='confirmation':paths['confirmation_proof_file']=str(root/short/'status.json')
        state.update(state='COMPLETE');replace_private(root/'status.json',state)
    except BaseException as exc:
        state.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);replace_private(root/'status.json',state);raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:run(read_private(a.config))
    else:print(json.dumps({'state':'DRY_RUN','execution':False}))
