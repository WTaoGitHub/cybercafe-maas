# E17 GPU 日报下游接入手册

已部署到 001。首份日报在采集启用后的次日 00:10 起生成。联调前请向 MaaS 运维取得服务 Base URL。能够访问该 MaaS 入口的服务均可查询日报，无需发送凭据。下游按需主动查询。

## 1. 主动查询

`GET /maas/v1/gpu-daily-reports/{day}`。日期格式为 `YYYY-MM-DD`，按北京时间（Asia/Shanghai）解释。日报最早于次日 00:10 生成，可用于漏收补查和对账。

```bash
curl --fail-with-body --show-error \
  -H 'Accept: application/json' \
  "${MAAS_BASE_URL}/maas/v1/gpu-daily-reports/2026-11-05"
```

| HTTP 状态 | 下游处理 |
|---|---|
| 200 | 处理已封存日报。同一日期重复查询返回同一份封存内容。 |
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
  "coverage": {
    "covered_seconds": 86400,
    "total_seconds": 86400,
    "percent": 100.0
  },
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
- `records: []` 表示日报已生成，但没有可交付的在线区间。`models: []` 表示该区间没有观测到运行中的模型。采集断档时，空档、空数组均不足以证明 GPU 离线或没有部署。整日无有效观测时日报不可查询。

- `coverage.covered_seconds` 是该自然日内有效观测的累计秒数。同一会话中相邻观测间隔不超过 120 秒才计入，断线或跨会话空档不计。即使没有 GPU 在线，有效观测仍计入覆盖。
- `coverage.total_seconds` 是北京时间自然日的总秒数。`coverage.percent` 是两者的百分比，保留最多两位小数。覆盖率衡量采集时长，单个模型清单的完整性需另行判断。
- 如已有旧版封存日报，其 JSON 可能没有 `coverage`。新字段随新封存批次生效，旧批次维持原内容以便重复查询时保持一致。

## 3. 联调检查

- 在北京时间次日 00:10 后按日期查询，核对区间、模型和覆盖率。
- 重复查询同一日期，确认返回相同的 report_id 和内容。下游可按 report_id 去重入库。
- 核对空日报、日期无效和日报不可用的处理。
