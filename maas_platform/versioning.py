"""Version target registration/preflight for GPUStack 2.2.3. No deployment mutation.
P2 must call this with fresh trusted metadata and per-worker image inventory.
"""
import hashlib,json,re,time
from dataclasses import asdict,dataclass
from .errors import ServiceError

MODEL_FIELDS=('id','name','owner_principal_id','source','local_path','huggingface_repo_id','huggingface_filename','model_scope_model_id','model_scope_file_path','backend','backend_version','backend_parameters','env','worker_selector','gpu_selector','cpu_offloading','distributed_inference_across_workers')
ROUTE_FIELDS=('id','name','owner_principal_id','generic_proxy','access_policy')
BACKEND_FIELDS=('backend_name','health_check_path','default_entrypoint','default_run_command','default_env','default_backend_param','parameter_format')
TARGET_FIELDS=('model_id','provider_id','weight','overridden_model_name','fallback_status_codes')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def deployment_spec(route,targets,model,backend):
    if route.get('access_policy') not in ('authed','allowed_principals','allowed_users'):
        raise ServiceError('UNSAFE_MODEL_ROUTE','Task targets must require authentication',409)
    if len(targets)!=1 or targets[0].get('model_id')!=model['id'] or targets[0].get('provider_id') is not None:
        raise ServiceError('AMBIGUOUS_MODEL_TARGET','Use one explicit local model target',409)
    if targets[0].get('weight',0)<=0 or targets[0].get('fallback_status_codes'):
        raise ServiceError('UNSAFE_MODEL_FALLBACK','Version target must have positive weight and no fallback',409)
    if model['backend']!=backend['backend_name']:
        raise ServiceError('MODEL_REVISION_CHANGED','Backend binding does not match',409)
    version=model['backend_version'];config=backend['version_configs'].get(version)
    if not config or not config.get('image_name'):
        raise ServiceError('MODEL_REVISION_CHANGED','Backend image is not specified',409)
    return {'route':{k:route.get(k) for k in ROUTE_FIELDS},
            'targets':[{k:t.get(k) for k in TARGET_FIELDS} for t in targets],
            'model':{k:model.get(k) for k in MODEL_FIELDS},
            'backend':{k:backend.get(k) for k in BACKEND_FIELDS},'version_config':config}


@dataclass(frozen=True)
class VersionPin:
    schema_version:int
    revision:str
    gpustack_version:str
    route_id:int
    route_name:str
    model_id:int
    backend_name:str
    backend_version:str
    image_name:str
    image_id:str
    worker_names:tuple[str,...]
    weights_policy:str
    configuration_sha256:str

    def public(self):return asdict(self)


def register_pin(revision,route,targets,model,backend,image_id,workers,weights_policy='image-contained'):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',revision):
        raise ServiceError('PIN_INVALID','Invalid immutable revision label')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image_id) or not workers or len(set(workers))!=len(workers):
        raise ServiceError('PIN_INVALID','Register a full image ID and unique eligible worker names')
    if weights_policy!='image-contained':
        raise ServiceError('UNSUPPORTED_WEIGHT_POLICY','External mutable weights require a separate checksum contract',409)
    spec=deployment_spec(route,targets,model,backend)
    # Only a fingerprint is retained for env/run parameters, not potential secret values.
    return VersionPin(1,revision,'v2.2.3',route['id'],route['name'],model['id'],model['backend'],model['backend_version'],spec['version_config']['image_name'],image_id,tuple(sorted(workers)),weights_policy,digest(spec))


def verify_pin(pin,version,route,targets,model,backend,inventory,now=None,max_inventory_age=60):
    now=time.time() if now is None else now
    if version.get('version') not in ('2.2.3','v2.2.3'):
        raise ServiceError('GPUSTACK_VERSION_CHANGED','Runtime is not the approved GPUStack version',409)
    if digest(deployment_spec(route,targets,model,backend))!=pin.configuration_sha256:
        raise ServiceError('MODEL_REVISION_CHANGED','Registered deployment configuration changed',409)
    if set(inventory)!=set(pin.worker_names):
        raise ServiceError('IMAGE_INVENTORY_INCOMPLETE','Eligible worker inventory changed or is incomplete',503)
    for row in inventory.values():
        if now-row['observed_at']>max_inventory_age or row['observed_at']>now+5:
            raise ServiceError('IMAGE_INVENTORY_STALE','Image inventory requires refresh',503)
        if row.get('image_name')!=pin.image_name or row.get('image_id')!=pin.image_id:
            raise ServiceError('MODEL_IMAGE_CHANGED','Worker image does not match the registered content',409)
    return {'verified':True,'revision':pin.revision,'configuration_sha256':pin.configuration_sha256}
