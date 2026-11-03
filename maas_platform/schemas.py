from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=128)
    input: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=3)


# Public task envelope is independent from each model's payload schema.
from datetime import datetime
from typing import Literal


class TaskView(BaseModel):
    id: str
    model: str
    revision: str
    state: Literal['QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED','CANCELLED','SUCCEEDED','FAILED']
    attempts: int = Field(ge=0)
    result_artifact_id: str | None
    error_code: str | None
    deadline: datetime
    created_at: datetime
    updated_at: datetime


class TaskDetail(TaskView):
    usage: dict[str, Any] | None


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
    request_id: str


class ApiError(BaseModel):
    error: ApiErrorDetail
