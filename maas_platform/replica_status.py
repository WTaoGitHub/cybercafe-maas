"""Private, read-only contract and pressure diagnostics; never changes replicas."""
import argparse
import json
from pathlib import Path

from maas_replica_pressure import PressureGate
from .replica_contract import ControlPolicyFile


def inspect(policy_file, pressure_file=None, worker_ids=(), *, clock=None):
    policy = ControlPolicyFile(policy_file, allow_apply=True).refresh()
    if any(type(ident) is not int or ident < 1 for ident in worker_ids):
        raise ValueError('Expected positive worker IDs')
    if policy.pressure_mode == 'disabled':
        diagnostic = {'state': 'disabled', 'reasons': ['configured_disabled']}
    else:
        gate = PressureGate(pressure_file, **({'clock': clock} if clock else {}))
        gate.evaluate(policy.pressure, worker_ids=set(worker_ids))
        diagnostic = gate.diagnostic
    return {'schema_version': policy.schema_version, 'revision': policy.revision,
            'mode': policy.execution.get('mode', 'observe'), 'cluster_id': policy.cluster,
            'model_caps': {key: value['max_replicas'] for key, value in policy.models.items()},
            'pressure_mode': policy.pressure_mode, 'pressure': diagnostic,
            'read_only': True, 'runtime_authorization_checked': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--pressure-file', type=Path)
    parser.add_argument('--worker-id', type=int, action='append', default=[])
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.policy, args.pressure_file, args.worker_id), allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError):
        # No paths, malformed source contents, or credentials in a public error.
        parser.exit(2, 'Replica diagnostic input is invalid or unavailable\n')


if __name__ == '__main__':
    main()
