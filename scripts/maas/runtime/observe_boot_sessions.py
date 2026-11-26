"""Run on the staging controller. Read native registrations and Bootstrap commitments.

Usage: observe_boot_sessions.py --cluster 1 --approval-root <private broker root>
The native SQL view and the MaaS observation migration must already be installed.
No permit, credential or local path is sent to the MaaS database.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
from uuid import UUID


def bootstrap_boot(root, cluster, row):
    gpu, session = row['gpu_uuid'], row['session_id']
    if not re.fullmatch(r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', gpu) or str(UUID(session)) != session:
        raise ValueError('Invalid native registration')
    automatic = root / f'auto-{cluster}-{gpu}'
    dirs = [root/session]
    if automatic.is_dir():
        dirs.extend(p for p in automatic.iterdir() if p.is_dir() and not p.is_symlink())
    matches = []
    for directory in dirs:
        issue = directory/'issue.json'
        binding = directory/'witness-binding.json'
        if not (issue.is_file() and binding.is_file()):
            continue
        issued = json.loads(issue.read_bytes())
        permit = issued.get('permit',{})
        if (issued.get('phase') != 'issued' or permit.get('cluster_id') != cluster or permit.get('external_id') != gpu
                or permit.get('worker_id') != row['worker_id'] or permit.get('session_id') != session):
            continue
        witness = json.loads(binding.read_bytes())
        boot = witness['boot']
        if str(UUID(boot)) != boot or not re.fullmatch(r'[0-9a-f]{64}',witness['commitment']):
            raise ValueError('Invalid Bootstrap witness binding')
        matches.append((boot,witness['commitment']))
    return matches[0] if len(set(matches)) == 1 else (None,None)


def collect(args):
    if args.cluster <= 0:
        raise ValueError('Invalid cluster')
    select = f'''SELECT coalesce(json_agg(row_to_json(r)), '[]') FROM (
        SELECT seq,worker_id,session_id,gpu_uuid FROM maas_current_boot_registrations
        WHERE cluster_id={args.cluster}) r'''
    native = ['docker','exec','-u','postgres',args.native,'psql','-XAt','-v','ON_ERROR_STOP=1','-d','gpustack','-c',select]
    with subprocess.Popen(['docker','exec','-i',args.api,'python','-B','-m','maas_platform.boot_observation',str(args.cluster)],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) as child:
        try:
            if child.stdout.readline().strip() != 'snapshot':
                raise RuntimeError('Observation transaction unavailable')
            rows = json.loads(subprocess.check_output(native,timeout=5))
            for row in rows:
                row['boot_id'],row['commitment'] = bootstrap_boot(args.approval_root,args.cluster,row)
            output,_ = child.communicate(json.dumps(rows)+'\n',timeout=12)
            if child.returncode:
                raise RuntimeError('Observation transaction failed')
            print(output.strip())
        finally:
            if child.poll() is None:
                child.kill()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cluster',type=int,required=True)
    parser.add_argument('--approval-root',type=Path,required=True)
    parser.add_argument('--native',default='maas-control-gpustack-observe')
    parser.add_argument('--api',default='maas-control-api-1')
    collect(parser.parse_args())
