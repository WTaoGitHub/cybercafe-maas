# E17 GPU 日报下游接入手册

当前为待部署的代码契约。联调前请向 MaaS 运维取得服务 Base URL 和日报查询专用 Bearer 凭据。如需主动推送，请向 MaaS 运维提供 HTTPS 接收地址及专用推送凭据。两类凭据分别配置。

## 1. 主动查询

`GET /maas/v1/gpu-daily-reports/{day}`。日期格式为 `YYYY-MM-DD`，按北京时间（Asia/Shanghai）解释。日报最早于次日 00:10 生成，可用于漏收补查和对账。

```bash
curl --fail-with-body --show-error \
  -H "Authorization: Bearer ${E17_QUERY_TOKEN}" \
  -H 'Accept: application/json' \
  "${MAAS_BASE_URL}/maas/v1/gpu-daily-reports/2026-11-05"
```

| HTTP 状态 | 下游处理 |
|---|---|
| 200 | 处理已封存日报。查询和推送使用同一份内容，查询操作与推送状态独立。 |
| 401 | 核对日报专用 Bearer 凭据。 |
| 404 | 指定日报不可用时，响应为 `{"detail":{"code":"GPU_DAILY_REPORT_UNAVAILABLE"}}`。若仅收到通用 404，核对入口及查询功能是否已启用。 |
| 422 | 检查日期是否合法。 |
| 5xx 或超时 | 记录故障，稍后按原日期重新查询。 |

## 2. 日报格式

示例包含两张 GPU。第一张全天在线，第二张分两段在线。模型清单按区间独立记录，同一区间可包含多个部署。`image.default` 和部署 ID `42` 为示例数据。时间使用带 `+08:00` 偏移的 ISO 8601 字符串。

```json
{
  "report_id": "gpu-daily-2026-11-05",
  "date": "2026-11-05",
  "timezone": "Asia/Shanghai",
  "records": [
    {
      "gpu_uuid": "GPU-47acf347-08f2-15cf-d2f0-8b58e7a79f33",
      "online_from": "2026-11-05T00:00:00+08:00",
      "online_until": "2026-11-06T00:00:00+08:00",
      "models": [
        {
          "logical_model_name": "ocr.default",
          "gpustack_deployment_id": "18",
          "gpustack_deployment_name": "maas-ocr-registry-r1"
        },
        {
          "logical_model_name": "image.default",
          "gpustack_deployment_id": "42",
          "gpustack_deployment_name": "example-image-r1"
        }
      ]
    },
    {
      "gpu_uuid": "GPU-50b437cb-59db-64a9-05a9-ca0cdb8b356a",
      "online_from": "2026-11-05T00:00:00+08:00",
      "online_until": "2026-11-05T09:18:00+08:00",
      "models": [
        {
          "logical_model_name": "ocr.default",
          "gpustack_deployment_id": "18",
          "gpustack_deployment_name": "maas-ocr-registry-r1"
        }
      ]
    },
    {
      "gpu_uuid": "GPU-50b437cb-59db-64a9-05a9-ca0cdb8b356a",
      "online_from": "2026-11-05T14:32:00+08:00",
      "online_until": "2026-11-06T00:00:00+08:00",
      "models": [
        {
          "logical_model_name": "ocr.default",
          "gpustack_deployment_id": "18",
          "gpustack_deployment_name": "maas-ocr-registry-r1"
        },
        {
          "logical_model_name": "image.default",
          "gpustack_deployment_id": "42",
          "gpustack_deployment_name": "example-image-r1"
        }
      ]
    }
  ]
}
```

- 在线以 GPUStack 的 `ready` 状态为准。区间包含起点，结束时间为区间边界。全天持续在线且观测覆盖整天时，区间为当天 00:00 至次日 00:00。
- 模型清单表示区间内观测到在该 GPU 上运行过的部署。同一部署的多个副本合并。部署 ID 是字符串。逻辑名未映射时为 `null`，仍保留部署 ID 和部署名。
- `records: []` 表示日报已生成，但没有可交付的在线区间。`models: []` 表示该区间没有观测到运行中的模型。采集断档时日报可能只包含部分区间，首版载荷没有覆盖率字段，因此空档、空数组均不足以证明 GPU 离线或没有部署。整日无有效观测时日报不可查询。

## 3. 接收主动推送

下游提供一个 HTTPS POST 接口。路径由双方约定，例如 `/internal/v1/gpu-daily-reports`。MaaS 发送上节的 JSON，并附带以下请求头。

```http
Authorization: Bearer <专用推送凭据>
Content-Type: application/json
Idempotency-Key: gpu-daily-2026-11-05
```

1. 校验凭据和载荷。校验 `Idempotency-Key` 等于正文 `report_id`。
2. 以 `report_id` 为唯一键保存日报和明细，并在持久化成功后返回 `200` 或 `204`。
3. 如果相同批次已通过查询入库，或已收到过推送，直接返回成功。比较 JSON 内容时允许对象键顺序不同。遇到同 ID 异常内容时，保留原批次并告警，交由双方核对。

MaaS 对超时、连接错误以及非 200/204 响应进行退避重试，复用同一 ID 和载荷。收到重复推送属于正常情况。下游可在漏收时按日期主动查询，并走同一套入库去重逻辑。首版日报封存后保持原批次，更正机制待双方另行约定。

## 4. 联调检查

- 用查询凭据取一份已封存日报，核对北京时间、区间边界和模型字段。
- 模拟下游已保存但响应丢失，确认重复推送只入库一次。
- 用同一日报先查询入库、再接收推送，确认合并为同一批。
- 核对空日报、日报不可用及错误凭据的处理。
