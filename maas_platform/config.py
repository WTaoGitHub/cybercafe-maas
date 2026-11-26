from dataclasses import dataclass
from pathlib import Path
import json, math, os, stat
from .adapters import adapter_for


def private_text(path):
    p = Path(path)
    if not p.is_file() or stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise ValueError('Secret file must be a regular private file (mode 600 or stricter)')
    return p.read_text().strip()


@dataclass
class Settings:
    database_url: str
    artifact_root: Path
    gpustack_url: str
    bindings_file: Path
    catalog_file: Path
    pepper_file: Path
    max_input_bytes: int = 10 * 1024 * 1024
    max_output_bytes: int = 20 * 1024 * 1024
    max_queued: int = 1000
    max_project_queued: int = 100
    max_running: int = 2
    max_project_running: int = 1
    lease_seconds: int = 30
    heartbeat_seconds: float = 5
    result_ttl_seconds: int = 86400
    input_ttl_seconds: int = 86400
    max_storage_bytes: int = 10 * 1024**3
    max_project_storage_bytes: int = 2 * 1024**3
    orphan_grace_seconds: int = 3600
    cleanup_batch_size: int = 200
    cleanup_time_budget_ms: int = 100
    cleanup_interval_seconds: int = 5
    min_free_bytes: int = 2 * 1024 * 1024 * 1024
    capacity_policy_file: Path | None = None
    shutdown_event_key_file: Path | None = None
    executor_slots: int = 1
    pp_ocr_worker_keys_file: Path | None = None
    runtime_proof_keys_file: Path | None = None
    runtime_boot_keys_dir: Path | None = None
    require_model_recovery: bool = False
    runtime_proof_apply_routes: frozenset[str] = frozenset()
    retry_unstarted_models: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls):
        values = {'retry_unstarted_models': frozenset(name.strip() for name in
                  os.getenv('MAAS_RETRY_UNSTARTED_MODELS', '').split(',') if name.strip())}
        recovery = os.getenv('MAAS_REQUIRE_MODEL_RECOVERY', '0')
        if recovery not in ('0', '1'):
            raise ValueError('MAAS_REQUIRE_MODEL_RECOVERY must be 0 or 1')
        values['require_model_recovery'] = recovery == '1'
        if directory := os.getenv('MAAS_RUNTIME_BOOT_KEYS_DIR'):
            values['runtime_boot_keys_dir'] = Path(directory)
        if keys := os.getenv('MAAS_RUNTIME_PROOF_KEYS_FILE'):
            values['runtime_proof_keys_file'] = Path(keys)
            values['runtime_proof_apply_routes'] = frozenset(
                route.strip() for route in os.getenv('MAAS_RUNTIME_PROOF_APPLY_ROUTES', '').split(',')
                if route.strip())
        if policy := os.getenv('MAAS_CAPACITY_POLICY_FILE'):
            values['capacity_policy_file'] = Path(policy)
        if key := os.getenv('MAAS_SHUTDOWN_EVENT_KEY_FILE'):
            values['shutdown_event_key_file'] = Path(key)
        if keys := os.getenv('MAAS_PP_OCR_WORKER_KEYS_FILE'):
            values['pp_ocr_worker_keys_file'] = Path(keys)
        integers = ('max_input_bytes','max_output_bytes','max_queued','max_project_queued',
                    'max_running','max_project_running','lease_seconds','result_ttl_seconds',
                    'input_ttl_seconds','min_free_bytes','max_storage_bytes','max_project_storage_bytes','orphan_grace_seconds','cleanup_batch_size',
                    'cleanup_time_budget_ms','cleanup_interval_seconds','executor_slots')
        for name in integers:
            value = os.getenv('MAAS_' + name.upper())
            if value is not None:
                try: values[name] = int(value)
                except ValueError: raise ValueError('Invalid integer configuration: ' + name) from None
        heartbeat = os.getenv('MAAS_HEARTBEAT_SECONDS')
        if heartbeat is not None:
            try: values['heartbeat_seconds'] = float(heartbeat)
            except ValueError: raise ValueError('Invalid heartbeat configuration') from None
        database_url = os.environ['MAAS_DATABASE_URL']
        if not database_url.startswith(('postgresql://','postgres://')):
            raise ValueError('Task service requires a separate PostgreSQL database')
        return cls(database_url, Path(os.environ['MAAS_ARTIFACT_ROOT']),
                   os.environ['MAAS_GPUSTACK_URL'].rstrip('/'), Path(os.environ['MAAS_BINDINGS_FILE']),
                   Path(os.environ['MAAS_CATALOG_FILE']), Path(os.environ['MAAS_PEPPER_FILE']), **values)

    def __post_init__(self):
        positive = ('max_input_bytes','max_output_bytes','max_queued','max_project_queued',
                    'max_running','max_project_running','lease_seconds','result_ttl_seconds','input_ttl_seconds','max_storage_bytes','max_project_storage_bytes','orphan_grace_seconds','cleanup_batch_size',
                    'cleanup_time_budget_ms','cleanup_interval_seconds','executor_slots')
        if any(not isinstance(getattr(self,name),int) or getattr(self,name)<=0 for name in positive):
            raise ValueError('Limits and retention periods must be positive integers')
        if not math.isfinite(self.heartbeat_seconds) or not 0<self.heartbeat_seconds<self.lease_seconds/2:
            raise ValueError('Heartbeat must be positive and less than half of the lease duration')
        if self.max_project_queued>self.max_queued or self.max_project_running>self.max_running:
            raise ValueError('Project limits must not exceed global limits')
        if self.max_project_storage_bytes>self.max_storage_bytes:
            raise ValueError('Project storage must not exceed global storage')
        if self.min_free_bytes<0:
            raise ValueError('Minimum free storage cannot be negative')

    def catalog(self):
        data = json.loads(self.catalog_file.read_text())
        guarded_routes = set()
        recovery_routes = set()
        for name, model in data.items():
            adapter = adapter_for(model['driver'])
            if self.require_model_recovery and adapter.requires_upstream:
                for option in ('execution_guard', 'runtime_recovery'):
                    if model.setdefault(option, True) is not True:
                        raise ValueError('Deployment requires execution guard and runtime recovery for every upstream')
            if adapter.requires_upstream and any(not isinstance(model.get(key),str) or not model[key] for key in ('upstream_model',)):
                raise ValueError('Network adapters require a registered upstream model')
            if (not isinstance(model.get('revision'), str) or not model['revision']
                    or not isinstance(model.get('max_runtime_s'), (int,float))
                    or not math.isfinite(model['max_runtime_s']) or not 0<model['max_runtime_s']<=3600):
                raise ValueError('Each model requires a revision and runtime limit')
            if model['driver'] == 'gpustack.document_ocr':
                adapter.validate_model(model)
            if 'execution_guard' in model and (type(model['execution_guard']) is not bool or
                    (model['execution_guard'] and not adapter.supports_execution_guard)):
                raise ValueError('Execution guard requires a supported connector')
            if model.get('execution_guard'):
                guarded_routes.add(model['upstream_model'])
            if 'runtime_recovery' in model:
                if type(model['runtime_recovery']) is not bool or (model['runtime_recovery'] and not model.get('execution_guard')):
                    raise ValueError('Runtime recovery requires an execution guard')
                if model['runtime_recovery']:
                    recovery_routes.add(model['upstream_model'])
        if any(model.get('upstream_model') in guarded_routes and not model.get('execution_guard')
               for model in data.values()):
            raise ValueError('Every MaaS alias on a guarded route must use the execution guard')
        if any(model.get('upstream_model') in recovery_routes and not model.get('runtime_recovery') for model in data.values()):
            raise ValueError('Every alias on a recovery route must require runtime evidence')
        return data

    def required_runtime_routes(self):
        required = {name: model['upstream_model'] for name, model in self.catalog().items()
                    if model.get('runtime_recovery')}
        routes = set(required.values())
        if required and self.capacity_policy_file:
            from .capacity import Policy
            policy = Policy.model_validate_json(private_text(self.capacity_policy_file))
            for pool in policy.pools.values():
                if pool.routes.keys() <= required.keys():
                    routes.update(pool.replica_routes)
        return routes

    def automatic_runtime_routes(self):
        return self.runtime_proof_apply_routes | self.required_runtime_routes()
