"""Explicit operator pause/resume gates; no GPUStack write credentials or transport."""
from hmac import compare_digest
import os
from pathlib import Path
import stat
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from maas_replica_execution import PendingWrite
from .replica_contract import ControlPolicyFile
from .replica_state import ControlState, atomic_private


class ControlResponse(BaseModel):
    paused: bool = Field(description='Persistent global pause; survives process recreation.')
    generation: int = Field(description='Control generation; only a changed gate increments it.')
    cluster_id: int = Field(description='GPUStack cluster ID from the current policy.')
    model_ids: list[int] = Field(description='GPUStack deployment IDs in the current policy.')
    policy_mode: str = Field(description='observe or apply; resume does not change policy mode.')
    pending_write: bool = Field(description='A previously dispatched native write still requires read-only confirmation.')
    state_updated: bool = Field(description='Whether this operation changed the persistent pause gate.')
    native_models_updated: bool = Field(description='Always false; the HTTP operation itself never changes native replicas.')
    scope: str = Field(description='Always global in this release.')
    pause_gate_open: bool = Field(description='Inverse of paused; other reconciliation checks can still block dispatch.')
    reconcile_checks_required: bool = Field(description='Always true; fresh policy, capacity and write checks remain mandatory.')


class ControlEndpoint:
    def __init__(self, policy_path, state_directory, journal_path):
        self.policy = ControlPolicyFile(Path(policy_path), allow_apply=True)
        self.state = ControlState(state_directory)
        self.journal = PendingWrite(Path(journal_path))
        self.lock = threading.Lock()

    def operate(self, command):
        with self.lock, self.state.locked():
            policy = self.policy.refresh()
            if policy.schema_version != 2:
                raise HTTPException(409, {'code': 'CONTROL_V2_REQUIRED', 'message': 'Requires a v2 controller'})
            data = self.state.read()
            if data is None:
                raise HTTPException(409, {'code': 'ADOPTION_REQUIRED', 'message': 'Adopt the managed scope first'})
            if command == 'resume' and (
                    (data['cluster_id'], data['owner_id']) != (policy.cluster, policy.owner)
                    or set(data['models']) != {str(key) for key in policy.models}):
                raise HTTPException(409, {'code': 'CONTROL_SCOPE_CHANGED', 'message': 'Managed scope requires adoption'})
            pending = self.journal.read() is not None
            if command == 'resume' and pending:
                raise HTTPException(409, {'code': 'PENDING_WRITE', 'message': 'Resolve the previous write before resuming'})
            changed = command != 'status' and data['paused'] != (command == 'pause')
            if changed:
                data['paused'] = command == 'pause'
                data['generation'] += 1
                atomic_private(self.state.path, data)
            # This is the durable requested gate, not a claim of GPUStack health
            # or readiness to dispatch before the writer completes its checks.
            return {'paused': data['paused'], 'generation': data['generation'],
                    'cluster_id': policy.cluster, 'model_ids': sorted(policy.models),
                    'policy_mode': policy.execution['mode'], 'pending_write': pending,
                    'state_updated': changed, 'native_models_updated': False,
                    'scope': 'global', 'pause_gate_open': not data['paused'],
                    'reconcile_checks_required': True}


def private_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('Expected a private operator key file')
        key = handle.read(4097).strip()
    if not 32 <= len(key) <= 4096 or any(value < 33 or value > 126 for value in key):
        raise ValueError('Invalid operator key')
    return key


def router(endpoint=None, *, key_file=None):
    routes = APIRouter(prefix='/maas/ops/replica-regulator', tags=['Replica regulator administration'])
    auth = HTTPBearer(auto_error=False, scheme_name='ReplicaOperatorBearer')
    key_file = key_file or os.getenv('MAAS_REPLICA_CONTROL_KEY_FILE')
    # Disabled by default. Customer keys and the E17 read-only report key never
    # authorize writes to the control gate.
    if endpoint is None and key_file:
        endpoint = ControlEndpoint(os.environ['MAAS_REPLICA_CONTROL_POLICY_PATH'],
                                   os.environ['MAAS_REPLICA_CONTROL_STATE_DIR'],
                                   os.environ['MAAS_REPLICA_CONTROL_JOURNAL_PATH'])

    def operator(credentials: HTTPAuthorizationCredentials | None = Depends(auth)):
        if endpoint is None or not key_file:
            raise HTTPException(404)
        try:
            key = private_key(key_file)
        except (OSError, ValueError):
            raise HTTPException(503, {'code': 'CONTROL_UNAVAILABLE', 'message': 'Operator authentication unavailable'}) from None
        if credentials is None or not compare_digest(credentials.credentials.encode(), key):
            raise HTTPException(401, headers={'WWW-Authenticate': 'Bearer'})

    def operate(command):
        try:
            result = endpoint.operate(command)
        except BlockingIOError:
            raise HTTPException(409, {'code': 'CONTROL_BUSY', 'message': 'Dispatch or control change in progress; query and retry'}) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(503, {'code': 'CONTROL_UNAVAILABLE', 'message': 'Private control state unavailable'}) from None
        from fastapi.responses import JSONResponse
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @routes.get('', dependencies=[Depends(operator)], response_model=ControlResponse,
                summary='Read the persistent global control gate')
    def status():
        """Read pause state, managed scope and unresolved-write presence. Does not query GPUStack."""
        return operate('status')

    @routes.post('/pause', dependencies=[Depends(operator)], response_model=ControlResponse,
                 summary='Pause automatic replica adjustments')
    def pause():
        """Persist a global pause. After success no new scale request may be dispatched.

        Requests sent before pause may finish; their read-only confirmation remains enabled.
        A busy response does not acknowledge a pause. Repeating a successful pause is safe.
        """
        return operate('pause')

    @routes.post('/resume', dependencies=[Depends(operator)], response_model=ControlResponse,
                 summary='Resume automatic replica adjustments')
    def resume():
        """Clear the global pause without modifying native replica counts.

        An unresolved write blocks resume. Observe mode and all writer safety checks still apply.
        Repeating a successful resume is safe. No request body is needed.
        """
        return operate('resume')

    return routes
