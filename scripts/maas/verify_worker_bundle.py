#!/usr/bin/env python3
"""Verify an already extracted public bundle. Does not install or execute its contents."""
import argparse,hashlib,json
from pathlib import Path


def verify(root):
    root=Path(root).resolve();manifest_path=root/'bundle-manifest.json'
    if manifest_path.is_symlink():raise ValueError('Manifest symlink rejected')
    m=json.loads(manifest_path.read_text())
    if m['schema_version']!=1 or m['contains_secrets'] is not False:raise ValueError('Invalid manifest')
    expected=set(m['files'])|{'bundle-manifest.json'}
    for name,entry in m['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Invalid member path')
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('Symlink/path escape')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('Hash mismatch: '+name)
    actual={str(p.relative_to(root)) for p in root.rglob('*') if not p.is_dir() or p.is_symlink()}
    if actual!=expected:raise ValueError('Unexpected or missing member')
    return {'ok':True,'files_verified':len(m['files']),'source_commit':m['source_commit'],
            'deployed_to_tower':False,'note':'Hashes verify integrity; compare archive hash through a trusted channel.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);a=p.parse_args()
    print(json.dumps(verify(a.root),indent=2))
