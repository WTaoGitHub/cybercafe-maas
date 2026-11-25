"""Read-only rollout check on a GPUStack worker with the MaaS bootstrap client installed.

Run as a Docker/systemd operator with PYTHONPATH=/opt/maas/lib:
    python3 -B verify_worker_receipts.py --guard-sha256 <released execution_guard.py SHA-256>
The current PP, Ovis and Unlimited entrypoints all load the guard from their script directory.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from maas_bootstrap import execution
from maas_bootstrap.client import LocalSettings, LocalState, ReleaseVerifier


def output(*args):
    return subprocess.check_output(args, text=True, timeout=20).strip()


def verify(settings, guard_sha256):
    state = LocalState(settings.data['state_dir'])
    identity = state.read('identity.json')
    fingerprint = identity['fingerprint']
    if not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ValueError('Invalid applied fingerprint')
    envelope = state.read(f'generations/{fingerprint}/config.json')
    metadata = ReleaseVerifier().verify(settings, envelope)
    if metadata['fingerprint'] != fingerprint:
        raise ValueError('Applied release does not match the worker identity')
    rules = envelope['manifest']['worker_runtime']['images']
    root = execution.local_directory(settings)
    unit = 'maas-worker-receipts'
    if (output('systemctl', 'is-enabled', unit + '.timer') != 'enabled'
            or output('systemctl', 'is-active', unit + '.timer') != 'active'
            or output('systemctl', 'show', '-p', 'Result', '--value', unit + '.service') != 'success'):
        raise ValueError('Worker receipt timer or last activation is unhealthy')

    counts = {}
    for name in output('docker', 'ps', '--format', '{{.Names}}').splitlines():
        if not name.endswith('-run-0'):
            continue
        container = json.loads(output('docker', 'inspect', name))[0]
        config = container['Config']
        image = json.loads(output('docker', 'image', 'inspect', container['Image']))[0]
        digests = {ref.rpartition('@')[2] for ref in image.get('RepoDigests') or []}
        env = dict(item.split('=', 1) for item in config['Env'])
        rule = next((r for r in rules if r['digest'] in digests
                     and r['entrypoint'] == config['Entrypoint'] and 'execution_env_prefix' in r), None)
        if rule is None:
            if any(key.startswith('MAAS_') and '_EXECUTION_' in key for key in env):
                raise ValueError(f'{name} uses an execution guard outside the signed image rules')
            continue
        if 'receipt_uid' not in rule:
            raise ValueError(f'{name} is running a guard without local receipts')
        prefix = rule['execution_env_prefix']
        mounts = {mount['Destination']: mount for mount in container['Mounts']}
        reader = execution.credential_directory(root, rule['receipt_uid'])
        for field, target, source, writable in (
                ('RECEIPT_DIR', execution.RECEIPT_TARGET, execution.receipt_directory(root, rule), True),
                ('KEY_FILE', execution.SERVING_DIRECTORY + '/' + execution.KEY_NAME, reader / execution.KEY_NAME, False),
                ('CA_FILE', execution.SERVING_DIRECTORY + '/' + execution.CA_NAME, reader / execution.CA_NAME, False)):
            mount = mounts.get(target, {})
            if (env.get(prefix + field) != target or mount.get('Type') != 'bind'
                    or mount.get('Source') != str(source) or mount.get('RW') is not writable):
                raise ValueError(f'{name} has an unexpected {field} mount')
        guard = PurePosixPath(rule['entrypoint'][-1]).with_name('execution_guard.py')
        if output('docker', 'exec', name, 'sha256sum', str(guard)).split()[0] != guard_sha256:
            raise ValueError(f'{name} has a different guard than the released source')
        counts[prefix] = counts.get(prefix, 0) + 1
    return {'worker_id': identity['worker_id'], 'revision': metadata['revision'], 'serving': counts}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/maas/bootstrap.json'))
    parser.add_argument('--guard-sha256', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{64}', args.guard_sha256):
        parser.error('Supply the lowercase SHA-256 of the released guard source')
    print(json.dumps(verify(LocalSettings(args.config), args.guard_sha256), sort_keys=True))
