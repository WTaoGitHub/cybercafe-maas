from pathlib import Path
import hashlib, os, shutil, uuid
from .errors import ServiceError


class ArtifactStore:
    def __init__(self, settings):
        self.root = Path(settings.artifact_root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.min_free_bytes = settings.min_free_bytes

    def ensure_space(self, size=0):
        if shutil.disk_usage(self.root).free < self.min_free_bytes + size:
            raise ServiceError('STORAGE_FULL', 'Insufficient artifact storage', 503, True)

    def path(self, ident):
        if not ident.startswith('art_') or len(ident) != 36 or any(c not in '0123456789abcdef' for c in ident[4:]):
            raise ServiceError('NOT_FOUND', 'Artifact not found', 404)
        return self.root / ident

    def put(self, data):
        self.ensure_space(len(data)); ident = 'art_' + uuid.uuid4().hex
        dest = self.path(ident); temp = self.root / ('.' + ident)
        try:
            with temp.open('xb') as f:
                os.chmod(temp, 0o600); f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(temp, dest)
            fd = os.open(self.root, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            temp.unlink(missing_ok=True)
        return {'id': ident, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def remove(self, ident):
        self.path(ident).unlink(missing_ok=True)
