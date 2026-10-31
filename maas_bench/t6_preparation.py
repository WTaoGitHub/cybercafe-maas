"""Content-preserving input rebinding and fail-closed T6 resource reservation.

Old manifests/proofs remain immutable. This module never renews artifacts,
changes quotas, deletes files, or treats a preparation probe as confirmation.
"""
import copy
import hashlib
import math
import re
from pathlib import Path
import time

from .snapshot import digest, read_private


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact_id(fixture):
    image=fixture.get('input',{}).get('image',{})
    value=image.get('artifact_id')
    if not isinstance(value,str) or not value:
        raise ValueError('Explicit uploaded image reference required')
    return value


def content_identity(manifest):
    value=copy.deepcopy(manifest)
    ids=set()
    for row in value['fixtures']:
        artifact_id(row)
        if row['id'] in ids:raise ValueError('Duplicate fixture identity')
        ids.add(row['id'])
        if not re.fullmatch(r'[a-f0-9]{64}',row.get('sha256','')):raise ValueError('Content SHA256 required')
        row['input']['image']['artifact_id']='<rebound-by-content>'
    return value


def validate_pair(old,new,old_quality,new_quality):
    if content_identity(old)!=content_identity(new):
        raise ValueError('Rebinding changed content, order, metadata or parameters')
    if old_quality['input_sha256']!=digest(old) or new_quality['input_sha256']!=digest(new):
        raise ValueError('Rebinding quality does not match manifest')
    a=copy.deepcopy(old_quality);b=copy.deepcopy(new_quality)
    a.pop('input_sha256');b.pop('input_sha256')
    if a!=b:raise ValueError('Rebinding changed quality reference')
    if len({artifact_id(x) for x in new['fixtures']})!=len(new['fixtures']):
        raise ValueError('Rebinding reused an uploaded identity')
    if {artifact_id(a) for a in old['fixtures']}&{artifact_id(b) for b in new['fixtures']}:
        raise ValueError('Rebinding requires fresh uploaded references')


def load_refresh(config,template,claim):
    path=config['fixture_refresh_file']
    if file_sha(path)!=config['fixture_refresh_sha256']:
        raise ValueError('Refresh receipt changed')
    receipt=read_private(path)
    if receipt['state']!='COMPLETE' or receipt['profile_id']!=template['profile_id']:
        raise ValueError('Incomplete or unrelated refresh')
    if receipt['claim_proof_sha256']!=file_sha(config['claim_proof_file']):
        raise ValueError('Refresh belongs to another T5 proof')
    manifests={}
    for kind,source,expected_sha in (
        ('ordinary',template['fixture_file'],digest(read_private(template['fixture_file']))),
        ('mixed',claim['mixed_workload_fixture_file'],claim['mixed_workload_sha256'])):
        entry=receipt[kind]
        old=read_private(source);new=read_private(entry['fixture_file'])
        if entry['source_sha256']!=expected_sha or digest(old)!=expected_sha or digest(new)!=entry['sha256']:
            raise ValueError('Refresh manifest binding changed')
        oldq=read_private(template['expected_text_file'] if kind=='ordinary' else claim['mixed_quality_file'])
        newq=read_private(entry['quality_file'])
        if file_sha(entry['quality_file'])!=entry['quality_file_sha256']:
            raise ValueError('Refresh quality file changed')
        validate_pair(old,new,oldq,newq);manifests[kind]=new
    ordinary=manifests['ordinary']['fixtures'];mixed=manifests['mixed']['fixtures']
    if len(ordinary)!=40 or len(mixed)!=50 or mixed[:40]!=ordinary:
        raise ValueError('Expected identical ordinary subset and fixed 80/20 mix')
    return receipt,manifests


def workload_extra(config,template,claim,kind):
    receipt,manifests=load_refresh(config,template,claim);entry=receipt[kind]
    return {'workload_fixture_file':entry['fixture_file'],'workload_sha256':entry['sha256'],
        'expected_text_file':entry['quality_file'],
        'raw_manifest_file':template['raw_manifest_file'] if kind=='ordinary' else claim['mixed_raw_file'],
        'fixture_refresh_file':config['fixture_refresh_file'],
        'fixture_refresh_sha256':config['fixture_refresh_sha256'],
        'claim_proof_file':config['claim_proof_file'],'refresh_template_file':config['template_file']}


def forecast_bytes(rate,ordinary_mean,mixed_mean):
    if not all(math.isfinite(x) and x>0 for x in (rate,ordinary_mean,mixed_mean)):
        raise ValueError('Positive measured rates and result sizes required')
    # 9x300s capacity; 4x180s at common .5/.8 rates. All capacity
    # windows conservatively use the fastest observed ordinary rate.
    confirm=math.ceil(((2700+468)*rate+1000)*ordinary_mean*1.25)
    # Includes separate 120s qualification, 24 formal segments, tails,
    # bursts/probes and 25% forecast margin; this is not a hard bound.
    soak=math.ceil((5520*rate+1000)*mixed_mean*1.25)
    return {'confirmation_bytes':confirm,'soak_bytes':soak,'total_bytes':confirm+soak,
        'reference_rate':rate,'forecast_margin':1.25,'future_expiry_credit_bytes':0}


def require_budget(current,forecast,quota,disk_free,min_free=2*1024**3):
    if current+forecast>quota*.9 or disk_free-forecast<min_free:
        raise RuntimeError('T6 storage reservation insufficient; no automatic quota change or cleanup')


def require_inputs(observer,manifests,finish_by):
    rows=manifests['mixed']['fixtures'];ids=[artifact_id(x) for x in rows]
    with observer.lock:
        values=observer.connection.execute('SELECT id,sha256,extract(epoch FROM expires_at)::float8 expiry,deleted_at FROM artifacts WHERE project=%s AND kind=\'input\' AND id=ANY(%s)',(observer.scope[0],ids)).fetchall()
    by_id={r['id']:r for r in values}
    for row in rows:
        found=by_id.get(artifact_id(row))
        if not found or found['deleted_at'] or found['sha256']!=row['sha256'] or found['expiry']<finish_by:
            raise RuntimeError('T6 input cannot cover the complete reserved lifetime')


def live_storage(observer):
    import json,shutil,subprocess
    with observer.lock:
        current=observer.connection.execute('SELECT coalesce(sum(size),0)::bigint bytes FROM artifacts WHERE project=%s AND deleted_at IS NULL',(observer.scope[0],)).fetchone()['bytes']
    app=json.loads(subprocess.check_output(['docker','inspect','maas-tasks-api-1'],text=True,stderr=subprocess.PIPE,timeout=15))[0]
    env=dict(x.split('=',1) for x in app['Config']['Env'] if '=' in x)
    source=next(x['Source'] for x in app['Mounts'] if x['Destination']=='/var/lib/maas/artifacts')
    with observer.lock:
        global_current=observer.connection.execute('SELECT coalesce(sum(size),0)::bigint bytes FROM artifacts WHERE deleted_at IS NULL').fetchone()['bytes']
    return {'global_current_bytes':global_current,'global_quota_bytes':int(env['MAAS_MAX_STORAGE_BYTES']),'current_bytes':current,'quota_bytes':int(env['MAAS_MAX_PROJECT_STORAGE_BYTES']),
        'disk_free_bytes':shutil.disk_usage(source).free,'min_free_bytes':max(2*1024**3,int(env['MAAS_MIN_FREE_BYTES']))}


def validate_reservation(config,template,claim,observer,kind):
    receipt,manifests=load_refresh(config,template,claim)
    if file_sha(config['preparation_file'])!=config['preparation_sha256']:
        raise ValueError('Preparation evidence changed')
    p=read_private(config['preparation_file'])
    from .runtime import ControllerProbe
    from .controller import fixed_profile
    current=ControllerProbe(template['source_root'],template['model_id'],template['profile_id'],claim['input_sha256']).capture()
    if digest(fixed_profile(current))!=p['runtime_fixed_profile_sha256']:raise RuntimeError('Runtime differs from preparation profile')
    if p['state']!='READY' or p['fixture_refresh_sha256']!=config['fixture_refresh_sha256']:
        raise ValueError('Preparation not ready or belongs to other inputs')
    remaining=12600 if kind=='confirmation' else 9000
    if p['finish_by_epoch']-time.time()<remaining:
        raise RuntimeError('Insufficient remaining T6 reserved time; prepare again')
    require_inputs(observer,manifests,p['finish_by_epoch'])
    storage=live_storage(observer)
    reserve=p['forecast']['total_bytes'] if kind=='confirmation' else p['forecast']['soak_bytes']
    require_budget(storage['current_bytes'],reserve,storage['quota_bytes'],storage['disk_free_bytes'],storage['min_free_bytes'])
    require_budget(storage['global_current_bytes'],reserve,storage['global_quota_bytes'],storage['disk_free_bytes'],storage['min_free_bytes'])
    return {'fixture_refresh_sha256':config['fixture_refresh_sha256'],'preparation_sha256':config['preparation_sha256']}


def install_stop_handlers():
    """Background shells may inherit SIGINT=IGNORE; stop only this driver."""
    import signal
    def stop(signum,frame):
        raise KeyboardInterrupt('Operator stopped T6 driver')
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
