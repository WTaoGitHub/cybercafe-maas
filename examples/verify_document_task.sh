#!/bin/sh
# Same operation: retain the generated request and key; never resubmit unknown work with a new key.
set -eu
: "${MAAS_BASE_URL:?set the document pilot base URL}"
: "${MAAS_API_KEY:?load the registered inference key from its controlled source}"
document=${1:?usage: verify_document_task.sh document.pdf}
umask 077
work=$(mktemp -d)
echo "Request evidence directory: $work"
request() {
    # Keep the key out of curl's argument list and response evidence.
    printf 'header = "Authorization: Bearer %s"\n' "$MAAS_API_KEY" |
        curl --config - --silent --show-error --fail-with-body --max-time 35 "$@"
}
request -H 'Content-Type: application/pdf' --data-binary "@$document" \
    "$MAAS_BASE_URL/maas/v1/artifacts" > "$work/upload.json"
python3 - "$work" <<'PY'
import json,sys,uuid
from pathlib import Path
p=Path(sys.argv[1]);ident=json.loads((p/'upload.json').read_text())['id']
(p/'key').write_text(str(uuid.uuid4()))
(p/'request.json').write_text(json.dumps({'model':'document.unlimited-ocr-2',
    'input':{'document':{'artifact_id':ident}},'parameters':{'dpi':150},
    'timeout_seconds':900,'max_attempts':1}))
PY
request -H 'Content-Type: application/json' -H "Idempotency-Key: $(cat "$work/key")" \
    --data-binary "@$work/request.json" "$MAAS_BASE_URL/maas/v1/tasks" > "$work/task.json"
task=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["id"])' "$work/task.json")
echo "Task: $task"
for attempt in $(seq 1 460); do
    request "$MAAS_BASE_URL/maas/v1/tasks/$task" > "$work/status.json"
    state=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["status"])' "$work/status.json")
    case "$state" in
        SUCCEEDED) break ;;
        FAILED|CANCELLED) cat "$work/status.json"; exit 1 ;;
    esac
    sleep 2
done
[ "$state" = SUCCEEDED ] || { echo "Polling expired; query task $task again. Do not resubmit."; exit 1; }
result=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["result_artifact_id"])' "$work/status.json")
request "$MAAS_BASE_URL/maas/v1/artifacts/$result" > "$work/result.json"
request "$MAAS_BASE_URL/maas/v1/tasks/$task/usage" > "$work/usage.json"
python3 - "$work" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);r=json.loads((p/'result.json').read_text());u=json.loads((p/'usage.json').read_text())
assert r['outputs']['schema_version']=='unlimited-ocr-2.document.v1'
assert r['outputs']['page_count']==u['successful_pages']==len(r['outputs']['pages'])
assert r['metering']['id']==u['id']
print('SUCCEEDED; successful_pages='+str(u['successful_pages'])+'; usage='+u['id'])
PY
