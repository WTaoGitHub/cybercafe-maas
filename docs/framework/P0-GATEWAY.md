# P0：入口、重试、原生代理与用量验收

2026-10-20 · GPUStack v2.2.3 (`1cfcbbf`)；测试证据：[gateway-acceptance.json](../../results/maas-p0/gateway-acceptance.json)、[路由策略](../../results/maas-p0/maas-gateway-policy.json)。

## 已确认的运行事实

现有18080入口经过容器内Higress；原OCR ingress指向两个实例，实际配置包含`error,timeout,http_503,http_502,non_idempotent`及`proxy-next-upstream-tries=2`。**该配置下实测一个一直503的POST引发3次后端调用，而非2次总调用。** 正常成功请求为一次调用。

为避免干扰原模型，使用独立的CPU-only合成后端、临时backend/model/route及非管理员key。后端登记所需128MiB显存预算只是调度声明，未加载GPU模型。正常JSON、无usage JSON、SSE、原生HTTP和503均经真实入口测试；OCR仅做两次正常调用，不注入故障。

| 请求 | 返回 | 实际后端调用 | GPUStack api_requests 增量 |
|---|---|---:|---:|
| 合成chat JSON / usage为0 | 200 | 1 | 1 |
| 合成chat JSON / 不带usage | 200 | 1 | 1 |
| 合成SSE | 200 | 1 | 1 |
| generic原生HTTP | 200 | 1 | 0 |
| 合成chat持续503 | 503 | 3 | 1 |
| 现有OCR JSON | 200 | 未加仪表，不冒充精确计数 | 1 |
| 现有OCR SSE | 200 | 未加仪表，不冒充精确计数 | 1 |

usage查询按临时用户完整身份过滤，每步等待12秒跨过10秒落库周期；warmup单独从基线扣除。此表是现网当前配置的覆盖结果，不是计费账本保证。所有token均为0，未给真实OCR填造token。

## 本期决策

- 保留GPUStack/Higress作为默认模型入口；不重复建设通用推理代理。
- P2网络型模型调用禁止默认叠加业务层重试；模糊的超时/断连/503需进入未知执行或明确失败，不能因为`retryable`就再次发POST。只有能证明未派发的故障才允许受总预算约束的重试。
- 3次是本次配置及故障用例的观测，不是所有fallback/外部后端的全局上界。版本目标必须登记其路由/fallback与重试策略，不能沿用未检查的全局假设。
- generic proxy已用独立route验证`/model/proxy/<id>/native/echo`→`/native/echo`及未鉴权401。原生产OCR的generic_proxy保持false；它是可选协议，不是P2接入前置。
- generic可能向payload补写model字段，适配器不得假设原生body逐字不变。
- 现有OCR JSON/SSE的请求数可复用；503也会产生api_requests，而generic没有相同覆盖。客户成功任务数必须使用任务层唯一事件，不能直接拿GPUStack api_requests结算；按图片/任务单位记录，不按token计费。

## 清理与复现

脚本：scripts/maas/p0/probe_gateway.py；依赖common.py、probe_backend.py及deploy/maas/p0/Dockerfile。代码运行于控制端，使用私有管理凭据；不输出key、Cookie或kubeconfig。

临时backend、model、route、用户和key已删除，原OCR配置逐项对比不变。探针镜像保留供复现。当前证据覆盖该路由样本，不声称所有模型或HTTP内容类型均已验收。
