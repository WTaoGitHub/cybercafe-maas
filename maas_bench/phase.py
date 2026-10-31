"""Shared stage journal and explicit parameter transitions for the pilot only."""
import hashlib
import json
import re
from pathlib import Path

from .backend import DatabaseObserver
from .contracts import Parameters
from .controller import PilotController
from .runtime import ControllerProbe
from .snapshot import digest,read_private,read_secret,save_private,replace_private
from .experiment import stamp,check_proof,fresh_namespace
from . import matrix


def proof_entry(path,case_id,template,states=('COMPLETE',)):
    p=Path(path);raw=p.read_bytes();value=json.loads(raw)
    if value['case_id']!=case_id or value['state'] not in states:raise ValueError('Unresolved phase prerequisite')
    if value['profile_id']!=template['profile_id'] or value['input_sha256']!=digest(read_private(template['fixture_file'])):
        raise ValueError('Phase evidence belongs to another model or inputs')
    check_proof(value,template)
    return value,{'state':value['state'],'proof_file':str(p),'proof_sha256':hashlib.sha256(raw).hexdigest()}


class Phase:
    def __init__(self,config,case_id,points,prerequisites,condition=None,selection_metadata=None):
        self.config=config;self.case_id=case_id;self.template=read_private(config['template_file'])
        self.context=stamp(self.template)
        self.output=Path(config['output_directory']);self.output.mkdir(mode=0o700,exist_ok=False)
        self.input_sha=digest(read_private(self.template['fixture_file']));self.prerequisites={}
        for dependency,path,states in prerequisites:
            _,entry=proof_entry(path,dependency,self.template,states);self.prerequisites[dependency]=entry
        selection={'case_id':case_id,'profile_id':self.template['profile_id'],'input_sha256':self.input_sha,'points':points,**self.context}
        if selection_metadata:
            if set(selection_metadata)-{'qos_policy_id','qos_policy_sha256','reference_mu_sha256','fixture_refresh_sha256','preparation_sha256'}:raise ValueError('Unsupported selection metadata')
            selection.update(selection_metadata)
        if condition:selection['condition']=condition
        self.selection_file=self.output/'selection-private.json';save_private(self.selection_file,selection)
        self.selection_sha=hashlib.sha256(self.selection_file.read_bytes()).hexdigest()
        self.observer=DatabaseObserver(read_secret(self.template['database_dsn_file']),self.template['project'],self.template['logical_model'],self.template['revision'],'e3-phase-controller-20261029:')
        self.probe=ControllerProbe(self.template['source_root'],self.template['model_id'],self.template['profile_id'],self.input_sha)
        self.controller=PilotController(self.probe,self.observer,self.output/'operations',experiment=self.template)
        self.status={'case_id':case_id,'profile_id':self.template['profile_id'],'input_sha256':self.input_sha,
            'state':'RUNNING','planned_points':len(points),'points':[],**self.context}
        if selection_metadata:self.status.update(selection_metadata)
        if condition:self.status['condition']=condition
        self.record()

    def record(self):replace_private(self.output/'status.json',self.status)

    def point_config(self,point,run_id,**extra):
        tag=self.config.get('run_tag','r1')
        if not re.fullmatch(r'[a-z0-9-]{1,10}',tag):raise ValueError('Bounded fresh phase attempt tag required')
        run_id=run_id+'-'+tag
        if not re.fullmatch(r'[a-z0-9-]{8,64}',run_id):raise ValueError('Bounded fresh point run ID required')
        snap=self.output/(run_id+'-before-private.json');cfgpath=self.output/(run_id+'-config-private.json')
        if snap.exists() or cfgpath.exists() or (Path(self.template['output_parent'])/run_id).exists():raise FileExistsError('Point evidence already exists; no parameter change')
        with self.observer.lock:
            count=self.observer.connection.execute('SELECT count(*)::int n FROM tasks WHERE project=%s AND starts_with(idempotency_key,%s)',
                (self.template['project'],'e3-'+run_id+':')).fetchone()['n']
        if count:raise ValueError('Point task namespace already exists; no parameter change or replay')
        if self.config.get('fixture_refresh_file'):
            from .t6_preparation import load_refresh,require_inputs,file_sha
            import time
            claim=read_private(self.config['claim_proof_file'])
            _,manifests=load_refresh(self.config,self.template,claim)
            if file_sha(self.config['preparation_file'])!=self.config['preparation_sha256']:raise ValueError('Preparation changed')
            reservation=read_private(self.config['preparation_file'])
            if reservation['state']!='READY' or time.time()+600>reservation['finish_by_epoch']:raise RuntimeError('T6 reservation expired')
            require_inputs(self.observer,manifests,reservation['finish_by_epoch'])
        target=Parameters(**point['parameters'])
        cfg={**self.template,'case_id':self.case_id,'point_id':point['point_id'],'run_id':run_id,
            'parameters':target.to_dict(),'snapshot_file':str(snap),'selection_file':str(self.selection_file),
            'selection_sha256':self.selection_sha,'completed_stages':self.prerequisites,'load_mode':point.get('mode','async_saturation'),**extra}
        if 'arrival_policy' in point:cfg['arrival_policy']=point['arrival_policy']
        for key in ('qos_class','qos_repeat'):
            if key in point:cfg[key]=point[key]
        if self.template.get('matrix_file'):
            matrix.validate_execution(cfg,self.input_sha)
        snapshot=self.controller.change(target)
        save_private(snap,snapshot)
        save_private(cfgpath,cfg);return cfg

    def completed(self,row):
        self.controller.check_fixed(self.probe.capture());self.controller.quiet()
        self.status['points'].append(row);self.record();print(json.dumps(row),flush=True)

    def finish(self,**extra):
        self.status['state']='RESTORING';self.record();restored=self.controller.restore()
        self.status.update(state='COMPLETE',restored_parameters=restored['parameters'],restored_generation=restored['generation'],**extra);self.record()

    def reject(self,reason,**extra):
        # Only a fully reconciled QoS rejection reaches here, never an UNKNOWN
        # or transport failure. restore() still checks ownership and exact state.
        self.status.update(state='RESTORING_REJECTED',reason=reason);self.record()
        restored=self.controller.restore()
        self.status.update(state='REJECTED_NO_QUALIFIED_CANDIDATE',selection_passed=False,
            restored_parameters=restored['parameters'],restored_generation=restored['generation'],**extra);self.record()

    def interrupted(self,exc):
        self.status.update(state='INTERRUPTED_REQUIRES_INSPECTION',error_class=type(exc).__name__);self.record()

    def close(self):self.observer.close()
