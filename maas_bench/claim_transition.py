"""Owned, container-only claim transitions for evidence-bound pilot experiments."""
import copy
import fcntl
import json
from pathlib import Path
import time
import uuid
from .snapshot import RuntimeFence,digest,save_private,replace_private
from .controller import fixed_profile
from .instance_memory import candidate_claim
from .resources import ResourceGuard,observe_resources
from .contracts import Parameters
from maas_platform.cluster_maintenance import model_hash


def model_without_claim(model):
    value=copy.deepcopy(model);value.get('env',{}).pop('GPUSTACK_MODEL_VRAM_CLAIM',None)
    return model_hash(value)


def validate_evidence(receipt,fit,source,current):
    if (receipt.get('state')!='PROFILE_VERIFIED' or not receipt.get('measurement_window_verified')
        or receipt.get('covered_instances')!=8 or len(source['workers'])!=2):
        raise ValueError('Complete pilot instance measurement required')
    amount=receipt['candidate_claim_bytes']
    if amount!=candidate_claim(receipt['max_sampled_container_bytes']) or amount!=int(3.5*1024**3):
        raise ValueError('Candidate must follow frozen measured budget rule')
    if (fit.get('state')!='COMPLETE' or fit.get('native_ready_instances')!=8
        or fit.get('placement')!=[4,4] or fit.get('computed_claim_bytes_per_instance')!=amount):
        raise ValueError('Matching successful native placement required')
    for key in ('profile_id','input_sha256','workers','catalog_sha256','app_fixed_env_sha256','gpustack_version'):
        if current[key]!=source[key]:raise ValueError('Resource evidence hardware/profile drift')
    if model_without_claim(current['model'])!=model_without_claim(source['model']):
        raise ValueError('Resource evidence model drift')
    if {tuple((r['service'],r['image'])) for r in current['app_containers']}!={tuple((r['service'],r['image'])) for r in source['app_containers']}:
        raise ValueError('Resource evidence application image drift')
    return Parameters(8,8,8,8,25,25,amount)


def validate_target(controller,claim,replicas,evidence,restoring):
    if restoring:
        p=controller.original['parameters']
        if claim!=p['vram_claim_bytes'] or replicas!=p['replicas']:
            raise ValueError('Restore only captured original claim and replicas')
    elif evidence is None or claim!=evidence.vram_claim_bytes or replicas!=evidence.replicas:
        raise ValueError('Only the evidence-bound claim target is allowed')


def change_claim(controller,claim,replicas,*,evidence=None,restoring=False,emit=lambda row:None):
    validate_target(controller,claim,replicas,evidence,restoring)
    before=controller.probe.capture();RuntimeFence(controller.expected).check(before)
    controller.check_fixed(before);controller.quiet()
    probe=controller.probe;pool=controller.pool;api=controller.api;path='/v2/models/'+str(before['model']['id'])
    model=copy.deepcopy(before['model']);guarder=ResourceGuard(before['workers'])
    op={'state':'PREPARED','claim_bytes':claim,'replicas':replicas,'restoring':restoring};journal=controller.output/(uuid.uuid4().hex+'-claim.json');save_private(journal,op)
    def record(stage):op.update(state=stage);replace_private(journal,op)
    def owned():
        state=pool.cli('drain-status')
        if state['generation']!=generation or not state['managed_requests_drained']:raise RuntimeError('Claim maintenance ownership lost')
        controller.quiet()
    def wait(count,amount):
        until=time.monotonic()+180
        while True:
            owned();m=api(path)
            if m['replicas']!=count or model_hash(m)!=model_hash(model):raise RuntimeError('External claim/model drift')
            rows=api(path+'/instances')['items']
            if any(x['state']=='error' for x in rows):raise RuntimeError('Instance error during claim transition')
            observe_resources(guarder,probe,before,emit)
            if len(rows)==count and all(x['state']=='running' and sum((x.get('computed_resource_claim') or {}).get('vram',{}).values())==amount for x in rows):
                if count and [sum(x['worker_id']==w['id'] for x in rows) for w in before['workers']]!=[count//len(before['workers'])]*len(before['workers']):
                    raise RuntimeError('Unbalanced native claim placement')
                return rows
            if time.monotonic()>until:raise RuntimeError('Claim convergence deadline; keep paused')
            time.sleep(.5)
    def scale(count,amount):
        nonlocal model
        from replica_capacity_matrix import change_model
        owned();model=change_model(api,model,count,model['worker_selector']);return wait(count,amount)
    try:
        with open(pool.LOCK,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);RuntimeFence(before).check(probe.capture());controller.quiet()
            active=json.loads((pool.LIVE/'maintenance/active.json').read_text());cfg=json.loads(Path(active['config']).read_text())
            if json.loads(Path(cfg['journal']).read_text())['phase']!='RESUMED':raise RuntimeError('Unfinished maintenance')
            pending=pool.LIVE/'executors/pending.json'
            if pending.exists() and json.loads(pending.read_text())['phase']!='COMPLETE':raise RuntimeError('Unfinished executor transition')
            generation=pool.cli('pause','--reason','E3 evidence-bound claim qualification')['generation'];record('PAUSED');owned()
            record('SCALING_TO_ZERO');scale(0,before['parameters']['vram_claim_bytes'])
            observed=api(path)
            if observed['replicas']!=0 or model_hash(observed)!=model_hash(model):raise RuntimeError('Claim update ownership changed')
            desired=copy.deepcopy(observed);desired['env']['GPUSTACK_MODEL_VRAM_CLAIM']=str(claim)
            record('CHANGING_CLAIM');model=api(path,desired,'PUT')
            if model['replicas']!=0 or model_hash(model)!=model_hash(desired):raise RuntimeError('Unconfirmed claim update; no replay')
            wait(0,claim);record('STARTING_TARGET');rows=scale(replicas,claim);save_private(journal.with_name(journal.stem+'-native-private.json'),rows)
            owned();record('RESUMING');pool.cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')
        after=probe.capture();controller.quiet()
        if after['generation']!=before['generation']+2:raise RuntimeError('Unexpected claim operation generation')
        target={**before['parameters'],'replicas':replicas,'vram_claim_bytes':claim}
        if after['parameters']!=target:raise RuntimeError('Claim operation not confirmed')
        fence=RuntimeFence(before);fence.acknowledge(before,after,{'parameters','generation','model','instances'})
        controller.expected=after;controller.fixed=fixed_profile(after);controller.check_fixed(after);record('COMPLETE');return after
    except BaseException:
        record('INTERRUPTED_REQUIRES_INSPECTION');raise
