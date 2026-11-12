"""Private v2 ownership operations; never sends a native model update."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from .replica_contract import ControlPolicyFile
from .replica_writer_v2 import ReconcileWriter


def targets(values):
    result = {}
    for value in values:
        ident, separator, count = value.partition('=')
        if (separator != '=' or not ident.isascii() or not ident.isdecimal() or int(ident) < 1
                or not count.isascii() or not count.isdecimal() or int(ident) in result):
            raise ValueError('Expected unique MODEL_ID=NONNEGATIVE_TARGET')
        result[int(ident)] = int(count)
    return result


async def control(command, writer, policy, expected, *, refresh):
    reason = writer.authorized(policy)
    if reason:
        raise ValueError(reason)
    if command != 'adopt' and expected:
        raise ValueError('Targets are only meaningful for adoption')
    if command == 'pause':
        writer.state.pause()
    elif command == 'resume':
        owned = writer.state.read()
        if (owned is None or (owned['cluster_id'], owned['owner_id']) != (policy.cluster, policy.owner)
                or set(owned['models']) != {str(key) for key in policy.models}):
            raise ValueError('Resume requires an adopted policy scope')
        writer.state.set_paused(False, pending_guard=writer.journal.read)
    elif command == 'adopt':
        if writer.journal.read() is not None:
            raise ValueError('Resolve the pending write before adoption')
        async with asyncio.timeout(12):
            if (await writer.request('GET', '/version')).get('version') != 'v2.2.3':
                raise ValueError('Unapproved GPUStack version')
            models = {ident: await writer.request('GET', f'/v2/models/{ident}') for ident in policy.models}
        if refresh() is not policy or writer.journal.read() is not None:
            raise ValueError('Policy or pending write changed')
        writer.state.adopt(policy, models, expected_targets=expected, pending_guard=writer.journal.read)
    elif command != 'status':
        raise ValueError('Unknown operation')
    state = writer.state.read()
    pending = writer.journal.read()
    return {'operation': command, 'state_updated': command != 'status',
            'native_models_updated': False, 'scope_config_checked': True,
            'cluster_id': policy.cluster, 'mode': policy.execution.get('mode'),
            'generation': state['generation'] if state else None,
            'paused': state['paused'] if state else None,
            'adopted_targets': {key: row['target'] for key, row in state['models'].items()} if state else {},
            'pending_write': bool(pending), 'read_only': command == 'status'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'adopt', 'pause', 'resume'))
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--expected-target', action='append', default=[])
    args = parser.parse_args()
    try:
        if os.getenv('MAAS_REPLICA_ENABLE_APPLY') != '1':
            raise ValueError('Runtime management authorization is not enabled')
        loader = ControlPolicyFile(args.policy, allow_apply=True)
        policy = loader.refresh()
        if policy.schema_version != 2:
            raise ValueError('Only v2 ownership operations are supported')
        writer = ReconcileWriter.from_environment()
        result = asyncio.run(control(args.command, writer, policy, targets(args.expected_target),
                                     refresh=loader.refresh))
        print(json.dumps(result, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, TimeoutError):
        parser.exit(2, 'Replica control input is invalid or operation is currently blocked\n')


if __name__ == '__main__':
    main()
