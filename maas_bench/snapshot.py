"""Runtime provenance and compare-before-restore checks, never blind rollback."""
import copy
import hashlib
import json
import os
from pathlib import Path

from maas_platform.cluster_maintenance import model_hash


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def save_private(path,value):
    p=Path(path)
    if p.parent.stat().st_mode & 0o077:raise ValueError('Private output directory required')
    # A run is immutable: no overwrite of either successful or failed evidence.
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())


def read_private(path):
    p=Path(path)
    if p.is_symlink() or p.stat().st_mode & 0o077:raise ValueError('Regular private file required')
    return json.loads(p.read_text())


class RuntimeFence:
    def __init__(self,snapshot):
        self.original=copy.deepcopy(snapshot);self.expected=copy.deepcopy(snapshot)

    def check(self,current):
        if digest(current)!=digest(self.expected):raise RuntimeError('Runtime/profile drift; stop without overwrite')

    def acknowledge(self,before,after,allowed_fields):
        """Called only after a lower-level guarded change was confirmed.

        Volatile timestamps must be normalized by the read-only runtime probe.
        A model patch can only alter replicas/selector and the one claim key;
        image, command, CPU threads, weights etc remain covered by model_hash.
        """
        self.check(before)
        if set(allowed_fields)-{'parameters','generation','model','instances','app_containers','app_env_sha256'}:
            raise ValueError('Unapproved snapshot change scope')
        for key in set(before)|set(after):
            if key not in allowed_fields and before.get(key)!=after.get(key):raise RuntimeError('Unapproved runtime field changed')
        if 'model' in allowed_fields:
            old=copy.deepcopy(before['model']);new=copy.deepcopy(after['model'])
            for row in (old,new):
                row.pop('replicas',None);row.pop('worker_selector',None)
                row.get('env',{}).pop('GPUSTACK_MODEL_VRAM_CLAIM',None)
            if model_hash(old)!=model_hash(new):raise RuntimeError('Unapproved model change')
        self.expected=copy.deepcopy(after)

    def restoration(self,current,*,paused,drained,unknown,generation):
        self.check(current)
        if not paused or not drained or unknown or generation!=self.expected['generation']:
            raise RuntimeError('No owned drained maintenance window; keep paused for inspection')
        return copy.deepcopy(self.original)
