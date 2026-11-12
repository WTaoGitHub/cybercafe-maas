"""Operator-only controller-host proof builder. Stdlib only; no worker SSH."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run_json(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def build(native, controller, output):
    source = Path(__file__).resolve().parents[2]/'maas_platform/replica_native_proof.py'
    tree = ast.parse(source.read_text())
    expected = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'SOURCES' for target in node.targets))
    program = '''import json,pathlib,hashlib,gpustack
from gpustack import envs
root=pathlib.Path(gpustack.__file__).parent
files=FILES
print(json.dumps({'source_sha256':{f:hashlib.sha256((root.parent/f).read_bytes()).hexdigest() for f in files},
 'scores':{'status':envs.SCHEDULER_SCALE_DOWN_STATUS_MAX_SCORE,
           'offload':envs.SCHEDULER_SCALE_DOWN_OFFLOAD_MAX_SCORE,
           'placement':envs.SCHEDULER_SCALE_DOWN_PLACEMENT_MAX_SCORE}}))
'''.replace('FILES', repr(list(expected)))
    proof = run_json(['docker', 'exec', native, 'python', '-c', program])
    if proof['source_sha256'] != expected or proof['scores']['status'] <= 0:
        raise ValueError('Native scale-down sources/configuration are not approved')
    program = '''import os,pathlib,json,hashlib,urllib.request
base=os.environ['MAAS_REPLICA_MANAGEMENT_URL'].rstrip('/')
key=pathlib.Path(os.environ['MAAS_REPLICA_MANAGEMENT_KEY_FILE']).read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
value=json.load(opener.open(urllib.request.Request(base+'/version',headers={'Authorization':'Bearer '+key}),timeout=8))
print(json.dumps({'version':value['version'],'endpoint_sha256':hashlib.sha256(base.encode()).hexdigest()}))
'''
    proof.update(run_json(['docker', 'exec', controller, 'python', '-c', program]))
    if proof['version'] != 'v2.2.3':
        raise ValueError('Native version is not approved')
    proof['native_image'] = run_json(['docker', 'inspect', native])[0]['Image']
    output = Path(output)
    if not output.is_absolute():
        raise ValueError('Use a private absolute output path')
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if output.parent.is_symlink() or output.parent.stat().st_mode & 0o077:
        raise ValueError('Proof directory must be private')
    with tempfile.NamedTemporaryFile(mode='w', dir=output.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            json.dump(proof, handle, sort_keys=True, allow_nan=False)
            handle.flush(); os.fsync(handle.fileno())
            if os.geteuid() == 0:
                os.chown(staged, 10001, 10001)
            staged.replace(output)
            fd = os.open(output.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            staged.unlink(missing_ok=True)
    return {'verified': True, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-container', required=True)
    parser.add_argument('--controller-container', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.native_container, args.controller_container, args.output)))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        parser.exit(2, 'Native proof generation failed; no control permission granted\n')
