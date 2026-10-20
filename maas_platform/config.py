from dataclasses import dataclass
from pathlib import Path
import json, os, stat


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
    min_free_bytes: int = 256 * 1024 * 1024

    @classmethod
    def from_env(cls):
        return cls(os.environ['MAAS_DATABASE_URL'], Path(os.environ['MAAS_ARTIFACT_ROOT']),
                   os.environ['MAAS_GPUSTACK_URL'].rstrip('/'), Path(os.environ['MAAS_BINDINGS_FILE']),
                   Path(os.environ['MAAS_CATALOG_FILE']), Path(os.environ['MAAS_PEPPER_FILE']))

    def catalog(self):
        data = json.loads(self.catalog_file.read_text())
        for name, model in data.items():
            if model['driver'] not in ('mock',):
                raise ValueError('Only the mock driver is enabled in P1; real connectors require P2 verification')
            if not model.get('revision') or not model.get('max_runtime_s'):
                raise ValueError('Each model requires a revision and runtime limit')
        return data
