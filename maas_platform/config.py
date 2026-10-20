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
    min_free_bytes: int = 2 * 1024 * 1024 * 1024

    @classmethod
    def from_env(cls):
        values = {}
        integers = ('max_input_bytes','max_output_bytes','max_queued','max_project_queued',
                    'max_running','max_project_running','lease_seconds','result_ttl_seconds',
                    'input_ttl_seconds','min_free_bytes')
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
                    'max_running','max_project_running','lease_seconds','result_ttl_seconds','input_ttl_seconds')
        if any(not isinstance(getattr(self,name),int) or getattr(self,name)<=0 for name in positive):
            raise ValueError('Limits and retention periods must be positive integers')
        if not math.isfinite(self.heartbeat_seconds) or not 0<self.heartbeat_seconds<self.lease_seconds/2:
            raise ValueError('Heartbeat must be positive and less than half of the lease duration')
        if self.max_project_queued>self.max_queued or self.max_project_running>self.max_running:
            raise ValueError('Project limits must not exceed global limits')
        if self.min_free_bytes<0:
            raise ValueError('Minimum free storage cannot be negative')

    def catalog(self):
        data = json.loads(self.catalog_file.read_text())
        for name, model in data.items():
            adapter_for(model['driver'])
            if (not isinstance(model.get('revision'), str) or not model['revision']
                    or not isinstance(model.get('max_runtime_s'), (int,float))
                    or not math.isfinite(model['max_runtime_s']) or not 0<model['max_runtime_s']<=3600):
                raise ValueError('Each model requires a revision and runtime limit')
        return data
