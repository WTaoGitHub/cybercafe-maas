"""Export public request/response schemas without reading secrets or connecting to a DB."""
import json,sys
from pathlib import Path
from maas_platform.schemas import TaskCreate,TaskView,ApiError,MockInput,MockParameters
output={'protocol':'maas-task-v1','status':'P0 engineering baseline',
        'task_request':TaskCreate.model_json_schema(),'task_response':TaskView.model_json_schema(),
        'error':ApiError.model_json_schema(),'mock_input':MockInput.model_json_schema(),
        'mock_parameters':MockParameters.model_json_schema(),
        'idempotency':{'required_header':'Idempotency-Key','scope':'project','same_request_status':200,'different_request_status':409,'new_task_status':202},
        'artifact_reference':{'current_mock_shape':'input.artifact_id','other_models':'model-specific input schema; no forced universal payload'}}
Path(sys.argv[1]).write_text(json.dumps(output,indent=2)+'\n')
