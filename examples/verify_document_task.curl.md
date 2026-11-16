# 仅使用 shell 和 curl 验证文档异步任务

适用于没有 Python/jq 的下游容器。使用运维提供的文档试点 `MAAS_BASE_URL`、受控来源的 `MAAS_API_KEY`，以及本地 `document.pdf`。密钥不写入请求JSON或响应文件。以下 `request` 函数将认证配置通过stdin传给curl。

```sh
umask 077
request() {
  printf 'header = "Authorization: Bearer %s"\n' "$MAAS_API_KEY" |
    curl --config - --silent --show-error --fail-with-body --max-time 35 "$@"
}
request -H 'Content-Type: application/pdf' --data-binary @document.pdf \
  "$MAAS_BASE_URL/maas/v1/artifacts"
```

将上传响应的 `id` 填入 [document-task.json](document-task.json) 的 `input.document.artifact_id`。为这次操作生成并保存一个幂等键；同次重试保留原键及原JSON。

```sh
IDEMPOTENCY_KEY=$(cat /proc/sys/kernel/random/uuid)
printf '%s\n' "$IDEMPOTENCY_KEY" > document-task.idempotency-key
request -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" --data-binary @document-task.json \
  "$MAAS_BASE_URL/maas/v1/tasks"
```

新任务返回HTTP202，幂等复用返回200。将响应 `id` 保存为 `TASK_ID`，重复查询同一个任务，直到 `status` 为 `SUCCEEDED`；`FAILED`、`CANCELLED` 或连接结果不明时不要换键重做推理。

```sh
TASK_ID=task_REPLACE_WITH_ACCEPTED_ID
request "$MAAS_BASE_URL/maas/v1/tasks/$TASK_ID"
```

成功后，将查询响应的 `result_artifact_id` 保存为 `RESULT_ID`，下载结果并查询最终页收据：

```sh
RESULT_ID=art_REPLACE_WITH_RESULT_ID
request "$MAAS_BASE_URL/maas/v1/artifacts/$RESULT_ID" > document-result.json
request "$MAAS_BASE_URL/maas/v1/tasks/$TASK_ID/usage"
request "$MAAS_BASE_URL/maas/v1/tasks/$TASK_ID/usage?usage_type=pages"
```

结果的 `outputs.page_count`、`outputs.pages` 数量和收据的 `successful_pages` 应一致；只有完整成功且已提交的任务才有最终页收据。文档token用量当前明确不可用。
