"""Explicit, hashed experiment matrix and immutable application image binding."""
import hashlib
import json
from pathlib import Path
import re

from .snapshot import digest


def matrix_for(config):
    from . import matrix
    file=config.get('matrix_file');sha=config.get('matrix_sha256')
    if not file and not sha:
        return matrix.load()
    if not file or not re.fullmatch(r'[a-f0-9]{64}',sha or ''):
        raise ValueError('Explicit matrix file and SHA256 required together')
    raw=Path(file).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Experiment matrix changed')
    value=json.loads(raw)
    if value['profile_id']!=config['profile_id']:raise ValueError('Matrix profile mismatch')
    # A profile override cannot remove dependencies from an existing stage.
    original={s['case_id']:s for s in matrix.load()['stages']}
    stages={s['case_id']:s for s in value['stages']}
    if len(stages)!=len(value['stages']):raise ValueError('Duplicate matrix stage')
    for key,old in original.items():
        if key not in stages:raise ValueError('Missing required stage')
        new=stages[key]
        if not set(old['depends_on']).issubset(new['depends_on']) or old['kind']!=new['kind']:
            raise ValueError('Matrix weakens stage prerequisites')
    binding=value.get('runtime_binding',{})
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',binding.get('application_image_id','')):
        raise ValueError('Explicit matrix requires immutable application image binding')
    return value


def stamp(config):
    if not config.get('matrix_file') and not config.get('matrix_sha256'):return {}
    m=matrix_for(config)
    return {'matrix_sha256':config['matrix_sha256'],'runtime_binding_sha256':digest(m['runtime_binding'])}


def check_proof(value,config):
    for key,expected in stamp(config).items():
        if value.get(key)!=expected:raise ValueError('Proof belongs to another matrix/framework binding')


def check_images(config,images):
    if not stamp(config):return
    expected=matrix_for(config)['runtime_binding']['application_image_id']
    if {s for s,_ in images}!={'api','executor','observer'} or any(i!=expected for _,i in images):
        raise ValueError('Running application image does not match experiment binding')


def check_runtime(config,snapshot):
    if not stamp(config):return
    if snapshot['profile_id']!=config['profile_id']:raise ValueError('Runtime profile mismatch')
    from .controller import inspect_image_ids
    # Read-only Docker inspect on the controller, never SSH to inference workers.
    check_images(config,inspect_image_ids([a['id'] for a in snapshot['app_containers']]))


def fresh_namespace(template,observer,run_id,paths):
    if not re.fullmatch(r'[a-z0-9-]{8,64}',run_id):raise ValueError('Fresh bounded run ID required')
    if any(Path(p).exists() for p in paths):raise FileExistsError('Point evidence exists; no mutation')
    with observer.lock:
        count=observer.connection.execute('SELECT count(*)::int n FROM tasks WHERE project=%s AND starts_with(idempotency_key,%s)',
            (template['project'],'e3-'+run_id+':')).fetchone()['n']
    if count:raise ValueError('Point namespace exists; no mutation or replay')
