"""Pilot parameter transitions using existing maintenance and replica controls."""
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .contracts import Parameters
from .snapshot import RuntimeFence,digest,save_private,replace_private
from maas_platform.cluster_maintenance import model_hash


def fixed_profile(snapshot):
    return {'profile_id':snapshot['profile_id'],'input_sha256':snapshot['input_sha256'],
        'model_hash_without_replicas':model_hash(snapshot['model']),
        'gpustack_version':snapshot['gpustack_version'],'workers':snapshot['workers'],
        'catalog_sha256':snapshot['catalog_sha256'],'app_fixed_env_sha256':snapshot['app_fixed_env_sha256'],
        'service_images':sorted({(r['service'],r['image']) for r in snapshot['app_containers']})}


class PilotController:
    def __init__(self,probe,observer,output):
        self.probe=probe;self.observer=observer;self.pool=probe.pool;self.api=probe.api
        self.output=Path(output);self.output.mkdir(mode=0o700,exist_ok=False)
        self.original=probe.capture();self.expected=copy.deepcopy(self.original)
        self.fixed=fixed_profile(self.original);self.quiet()
        ids=[r['id'] for r in self.original['app_containers']]
        self.images=self.image_ids(ids)
        save_private(self.output/'original-private.json',self.original)

    def image_ids(self,ids):
        rows=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True,stderr=subprocess.PIPE,timeout=15))
        return sorted({(r['Config']['Labels']['com.docker.compose.service'],r['Image']) for r in rows})

    def quiet(self):
        row=self.observer.snapshot()
        if any(row[k] for k in ('global_queued','running','sync_running','unknown','sync_unknown')):
            raise RuntimeError('Active/queued/UNKNOWN traffic; no parameter transition')

    def check_fixed(self,snapshot):
        if digest(fixed_profile(snapshot))!=digest(self.fixed):raise RuntimeError('Fixed model/profile changed')
        if self.image_ids([r['id'] for r in snapshot['app_containers']])!=self.images:
            raise RuntimeError('Application image digest changed')

    def change(self,parameters):
        if not isinstance(parameters,Parameters):raise TypeError('Validated bounded parameters required')
        current=self.probe.capture();RuntimeFence(self.expected).check(current);self.check_fixed(current);self.quiet()
        if parameters.vram_claim_bytes!=current['parameters']['vram_claim_bytes']:
            raise ValueError('Claim needs separate peak-gated experiment')
        op={'id':uuid.uuid4().hex,'state':'PREPARED','before':current['parameters'],'target':parameters.to_dict()}
        model_changes=int(parameters.replicas!=current['parameters']['replicas'])
        pool_changes=int(any(parameters.to_dict()[k]!=current['parameters'][k] for k in ('executors','global_limit','project_limit','global_queue','project_queue')))
        expected_generation=current['generation']+2*(model_changes+pool_changes)
        path=self.output/(op['id']+'.json')
        def record(state):op.update(state=state,at=time.time());replace_private(path,op)
        record('PREPARED')
        try:
            if parameters.replicas!=current['parameters']['replicas']:
                record('CHANGING_MODEL')
                self._model_replicas(current,parameters.replicas)
                current=self.probe.capture();self.check_fixed(current)
                self.expected=current
            keys=('executors','global_limit','project_limit','global_queue','project_queue')
            if any(parameters.to_dict()[k]!=current['parameters'][k] for k in keys):
                record('CHANGING_POOL')
                script=self.probe.root/'scripts/maas/runtime/executor_pool.py'
                cmd=[sys.executable,str(script),'scale','--replicas',str(parameters.executors),
                    '--global-limit',str(parameters.global_limit),'--project-limit',str(parameters.project_limit),
                    '--global-queue',str(parameters.global_queue),'--project-queue',str(parameters.project_queue)]
                result=subprocess.run(cmd,capture_output=True,text=True,timeout=240)
                private=self.output/(op['id']+'-pool-private.log');private.write_text(result.stdout+result.stderr);private.chmod(0o600)
                if result.returncode:raise RuntimeError('Executor transition failed; inspect private journal')
            after=self.probe.capture();self.check_fixed(after);self.quiet()
            if after['generation']!=expected_generation:raise RuntimeError('Unexpected maintenance generation')
            if after['parameters']!=parameters.to_dict():raise RuntimeError('Parameter transition not confirmed')
            self.expected=after;record('COMPLETE');return after
        except BaseException:
            record('INTERRUPTED_REQUIRES_INSPECTION');raise

    def _model_replicas(self,current,count):
        from replica_capacity_matrix import change_model
        from executor_pool import LOCK,LIVE
        with open(LOCK,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.quiet();RuntimeFence(current).check(self.probe.capture())
            active=json.loads((LIVE/'maintenance/active.json').read_text());cfg=json.loads(Path(active['config']).read_text())
            if json.loads(Path(cfg['journal']).read_text())['phase']!='RESUMED':raise RuntimeError('Unfinished maintenance')
            pending=LIVE/'executors/pending.json'
            if pending.exists() and json.loads(pending.read_text()).get('phase')!='COMPLETE':raise RuntimeError('Unfinished executor operation')
            state=self.pool.cli('pause','--reason','E3 tuning model replicas');generation=state['generation']
            def owned():
                state=self.pool.cli('drain-status')
                if state['generation']!=generation or not state['managed_requests_drained']:raise RuntimeError('Pause lost or activity remains')
                self.quiet();observed=self.observer.snapshot()
                if not observed['paused'] or observed['generation']!=generation:raise RuntimeError('Pause changed during observation')
            owned()
            expected=current['model'];path='/v2/models/'+str(expected['id'])
            workers={w['id'] for w in current['workers']}
            def wait_ready(target):
                until=time.monotonic()+180
                while True:
                    owned();model=self.api(path)
                    if model['replicas']!=target or model_hash(model)!=model_hash(expected):raise RuntimeError('External model drift')
                    rows=self.api(path+'/instances')['items']
                    if any(r['state']=='error' for r in rows):raise RuntimeError('Instance error during convergence')
                    if len(rows)==target and all(r['state']=='running' and r['worker_id'] in workers for r in rows):
                        distribution=[sum(r['worker_id']==i for r in rows) for i in workers]
                        if len(set(distribution))!=1:raise RuntimeError('Unbalanced worker placement')
                        return
                    if time.monotonic()>=until:raise RuntimeError('Convergence deadline; keep paused')
                    time.sleep(.5)
            # GPUStack scale-down does not promise to retain an equal number
            # on each GPU. This experiment requires a balanced layout: rebuild
            # only this model inside the already owned, drained pause, as T1
            # does, instead of treating a normal native shrink as rebalance.
            if count<expected['replicas']:
                expected=change_model(self.api,expected,0,expected['worker_selector']);wait_ready(0)
            expected=change_model(self.api,expected,count,expected['worker_selector']);wait_ready(count)
            owned();self.pool.cli('resume','--credential-id','pilot-ocr','--probe-image','/run/maas-probe/upright.png')

    def restore(self):
        return self.change(Parameters(**self.original['parameters']))
