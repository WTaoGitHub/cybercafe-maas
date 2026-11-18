# MaaS 多模型配置接入与共库验证

2026-11-18 · [统一接入与迁移跟踪 #62](https://github.com/WTaoGitHub/cybercafe-maas/issues/62)

一套支持文档依赖的 MaaS API、同版本执行器和一个 PostgreSQL 任务库可以使用同一份模型目录承载图片与文档 OCR。模型在 GPUStack 部署后，优先从现有适配器中选择。输入预处理、推理协议、结果解析或计量语义有实际差异时，再为对应协议开发可复用适配器。

| 兼容范围 | 目录 driver | 请求及计量 |
| --- | --- | --- |
| PP-OCRv4 兼容服务 | `gpustack.ocr` | 支持原同步 Chat 入口和异步任务；成功按图片计量 |
| 已固定版本的 Unlimited-OCR-2 文档服务 | `gpustack.document_ocr` | 异步 PDF/图片 artifact；成功按页计量；token 用量保持 unavailable |

## 接入步骤

1. 在 GPUStack 验证固定部署版本、健康副本、有效路由名称及调用凭据的上游权限。用最小真实样本验证输入、返回格式和结果边界。MaaS 通过同一个配置的 GPUStack 网关访问这些路由。
2. 在 `MAAS_CATALOG_FILE` 的 JSON 对象中，以唯一逻辑模型名为键，登记 `driver`、`upstream_model`、`revision`、`max_runtime_s`。图片和文档可分别复用 [图片目录示例](catalog.ocr.example.json) 与 [文档目录示例](catalog.document.example.json)。共库时合并两份对象的条目并核对逻辑名称没有重复。文档条目还需要模型、运行镜像摘要、后处理器和渲染 profile 四项固定值，API 与执行器均需安装对应 PDFium 依赖。若使用已验收的 NFS 副本，先核对它仍运行这些固定版本，再将候选条目的 `upstream_model` 指向 `unlimited-ocr-2-nfs`。
3. 在 `MAAS_BINDINGS_FILE` 的对应 credential `models` 清单中显式授权逻辑名，保留 `credential_id` 对应的项目归属。核对 MaaS 本地授权与 GPUStack 路由权限。API 与执行器读取相同版本的目录及绑定，发布前用候选进程检查文件权限、JSON、版本和依赖。目录条目全量校验，文档依赖缺失也会影响读取其他模型配置。同一逻辑名的配置变更先排空依赖旧定义的任务，再发布新 `revision`。需要并行过渡时使用不同逻辑名。执行器会核对提交时保存的模型指纹，不能通过覆盖条目将排队任务改发给新路由。
4. 按已验证吞吐设置容量。静态模式使用库级和项目级上限。旧 M2 通过独立库的静态上限将文档并发限制为 1，共库提高全局上限后须另行保证文档模型仍受该限制。动态模式在同一份 `MAAS_CAPACITY_POLICY_FILE` 中覆盖所有有上游的条目，填写真正的 cluster/model/route/owner ID、许可的 GPU 型号、每副本并发、池硬上限和队列上限。共享同一部署的名称归同一池。策略变化先观测，再按暂停、排空、发出匹配的新鲜预算、应用策略、探针和恢复的流程生效。细节见 [容量配置与启停](README.md#隔离环境启用动态准入)及 [Issue #60](https://github.com/WTaoGitHub/cybercafe-maas/issues/60)。
5. 验证逻辑名的 capabilities、凭据授权和一次真实推理。图片检查 `/gpustack/v1/chat/completions`、异步 `/maas/v1/ocr` 或 `/maas/v1/tasks` 和图片收据；文档检查上传、异步任务、结果 artifact 与页收据。确认只生成预期的一份收据。恢复探针使用独立受控凭据，须取得目录内全部网络模型的本地和上游权限。

## 隔离验证

以下测试使用仓库的真实 API、两个适配器、PDF 渲染、执行器、PostgreSQL 及文件存储。GPUStack 请求和容量观察值来自固定测试样本。只指向可销毁的 `_test` 库，测试夹具会重建其 schema。

```sh
MAAS_TEST_DATABASE_URL='<独立 PostgreSQL 测试库 URL>' \
  python -m pytest tests/maas/test_model_onboarding_integration.py -q
```

用同一 API 和执行器验证 `ocr.default` 同步/异步与图片收据、`document.unlimited-ocr-2` 两页 PDF 异步、下载和页收据。只调整目录和权限，新增复用 `gpustack.ocr` 的 `ocr.config-test`，核对新路由、同步/异步和未获授权时的拒绝。动态试验为三个模型配置池，检查文档单槽和全局同步/异步共享上限。另模拟文档任务的不确定上游返回，验证跨模型及跨项目的派发保护、历史读取、证据确认后的恢复和无重放。

2026-11-18 使用 PostgreSQL 16.16 验证了以上三个场景。包含目录、原同步和异步链路、计量、文档及容量的相关回归为 **147 通过、1 跳过**。广泛回归排除会残留 OCR mock 的旧 HTTP 实验后为 900 通过、2 跳过、5 失败。余下失败来自旧 `/ocr` 压测调用和网络边界断言。分别见 [HTTP 实验 #63](https://github.com/WTaoGitHub/cybercafe-maas/issues/63)、[入口命名 #25](https://github.com/WTaoGitHub/cybercafe-maas/issues/25) 和 [网络边界 #55](https://github.com/WTaoGitHub/cybercafe-maas/issues/55)。

## 正式迁移前需决定的事项

目前 `ocr.default` 由原 API 写入 `maas`，文档试点写入 `maas_document_m2`。建议先在隔离环境恢复一致的数据库和 artifact 备份，选择保留 `maas` 为共同任务库的候选方案。正式切换需另行确认下列事项。

| 事项 | 建议与验收 |
| --- | --- |
| 历史任务、文件和用量 | 初期保留文档试点的历史查询、下载及用量入口，关闭该入口上的新上传和任务派发。沿用原 project、凭据绑定、artifact 文件及 TTL，核对历史成功、失败、过期、跨项目不可见。若以后合并历史，先检查 `(project, idempotency_key)` 冲突、任务和 artifact 引用、收据来源唯一性及文件摘要，保留原 ID 和期限。 |
| 入口与幂等过渡 | 新任务使用统一入口，明确通知旧文档入口上的已受理任务继续在原入口查询。切换前冻结新提交并排空，核对已上传未提交的 artifact、队列、在途及客户端重试窗口。两个库各自去重，旧请求不得因为切换而跨库重放。同一路径若必须服务新旧任务，需要经验证的历史归属路由和去重方案。 |
| 故障影响范围 | 当前一个库内的 async 或 sync `UNKNOWN` 会阻止其他模型取得新的派发权限，同步返回 `BACKEND_QUARANTINED`。在未暂停入口时，新异步任务仍可能排队，历史查询仍可服务。需要用户接受这种全库影响，或先另行设计并验收更细的隔离。检查后端实际终止或已完成证据，再走受控 resolve 和探针，禁止自动解除或重新发送结果不明的任务。 |
| 容量与回退 | 用混合负载验收共用 GPU 的确切布局、短图延迟和文档并发上限。观察动态策略或用经批准的保守静态预算验证。正式切换及回退都先冻结新提交、排空在途、核对两端账本；回退恢复入口与版本，并保留切换后已写入的任务、文件和收据供原位置查询。 |

本轮验证限于隔离任务库和模拟上游。正式迁移、GPU 混合负载、历史跨库合并及下游切换待单独验收。
