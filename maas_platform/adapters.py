"""Explicit local adapter registry. Network connectors require separate P2 validation."""
import hashlib
import time
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field
from .errors import ServiceError


class MockInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(default='', max_length=10000)
    artifact_id: str | None = Field(default=None, max_length=80)


class MockParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delay_seconds: float = Field(default=0, ge=0, le=30)
    fail_first_attempts: int = Field(default=0, ge=0, le=3)


class DigestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_artifact: str = Field(min_length=1, max_length=80)


class DigestParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')


@dataclass
class ExecutionContext:
    task: dict
    service: object
    stop: object
    deadline: float

    def check(self):
        if time.monotonic() >= self.deadline:
            raise ServiceError('RUNTIME_EXCEEDED', 'Model execution runtime limit reached')
        if self.service.cancelled(self.task):
            raise ServiceError('INTERRUPTED', 'Execution interrupted')

    def read_input(self):
        self.check()
        return self.service.read_task_input(self.task)


class LocalAdapter:
    # Only deterministic local work is replayable after lease loss in P1.
    replay_safe = True
    requires_upstream = False

    def validate(self, payload, parameters):
        inp = self.input_schema.model_validate(payload).model_dump()
        params = self.parameter_schema.model_validate(parameters).model_dump()
        return inp, params, inp.get(self.artifact_field)


class EchoAdapter(LocalAdapter):
    input_schema = MockInput
    parameter_schema = MockParameters
    artifact_field = 'artifact_id'

    def execute(self, ctx):
        task = ctx.task
        end = time.monotonic() + task['parameters']['delay_seconds']
        while time.monotonic() < end:
            ctx.check()
            ctx.stop.wait(min(.1, max(0, end-time.monotonic())))
        ctx.check()
        if task['attempts'] <= task['parameters']['fail_first_attempts']:
            raise ServiceError('MOCK_TRANSIENT', 'Injected recoverable failure', 503, True)
        artifact = None
        if task['input_artifact_id']:
            data, media = ctx.read_input()
            artifact = {'bytes': len(data), 'media_type': media}
        return {'text': task['input']['text'], 'input_artifact': artifact}


class DigestAdapter(LocalAdapter):
    input_schema = DigestInput
    parameter_schema = DigestParameters
    artifact_field = 'source_artifact'

    def execute(self, ctx):
        data, media = ctx.read_input()
        return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'media_type': media}


_REGISTRY = {'mock': EchoAdapter(), 'local.sha256': DigestAdapter()}


def adapter_for(driver):
    try:
        return _REGISTRY[driver]
    except KeyError:
        raise ValueError('Unverified adapter driver; real connectors require P2 verification') from None
