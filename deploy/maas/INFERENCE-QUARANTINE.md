# MaaS 不确定推理的局部隔离

候选实现跟踪 [Issue #2](https://github.com/WTaoGitHub/cybercafe-maas/issues/2) 与 [Issue #62](https://github.com/WTaoGitHub/cybercafe-maas/issues/62)。

MaaS 在发送推理前记录派发 token、逻辑模型和实际配置的上游路由。动态准入模式还固定记录策略中对应的 GPUStack 部署 ID。连接中断、响应结果不确定或发送后的进程丢失仍进入 `UNKNOWN`。同一库中，以下请求被隔离：

- 同一逻辑模型，包括目录条目换了修订或路由后到达的请求。
- 通过同一上游路由的其他逻辑名称。
- 动态模式下同一部署池的其他路由或别名。静态模式按逻辑模型和上游路由识别范围，无法识别不同路由背后的同一部署。此类路由共库时应使用经过验证的同一动态池绑定。

同步在该范围返回 `503 BACKEND_QUARANTINED`。异步队列跳过该范围。若任务已领取但尚未发送，二次检查会将其放回队列，保留原任务、期限和执行重试预算。其他范围可使用剩余容量。未知执行仍占一份全局及项目额度，已被计入活跃任务的相同 attempt 只计一次；没有剩余额度时其他模型也会等候。全局维护暂停和排空的行为沿用原有契约。

### PP-OCRv4 执行保护镜像

在包含 `server.py`、`execution_guard.py`、`image_decode.py` 和 [Dockerfile.execution-guard](../../gpu-ocr/Dockerfile.execution-guard) 的构建上下文中，以固定基础镜像的完整引用作为 `BASE_IMAGE`。已验证的基础镜像摘要为 `sha256:9ec6c93fd35ecf2bfd1a9d842cf7afab4276cff6475e5afe8d56f43acbdca9d9`，先前发布的受保护镜像索引摘要为 `sha256:4a1ba7fa63408f475bcee4e1a7927b165c1ed5333e7cb27c3283e47f22a3a99c`。GPUStack 沿用 `python3 /app/server.py` 入口，按实际部署配置 `MAAS_PP_OCR_EXECUTION_ORIGIN`、`MAAS_PP_OCR_EXECUTION_ROUTE`、`MAAS_PP_OCR_EXECUTION_KEY_FILE` 和 `MAAS_PP_OCR_EXECUTION_CA_FILE`。推理进程读取按 GPU 部署的私有凭据文件。

保留同一 GPUStack 部署和网关路由时，池预算可随其中合格副本数自动变化。先在全部可达副本验证回执再启用目录保护。共享路由的 `UNKNOWN` 按模型池隔离。需逐 GPU 隔离时使用下文的固定路由配置。

## 逐 GPU 隔离候选

动态模式下，容量池可使用 `replica_routes` 将实际网关路由名映射到固定 GPU 的单副本部署。下面是加入一个池的字段示例。池的 `model_id` 为其中一个部署，`routes` 仍保存客户端逻辑名称到目录默认路由 ID 的映射。

```json
"replica_routes": {
  "example-gpu-a": {"route_id": 4, "model_id": 2, "gpu_uuid": "GPU-11111111-1111-1111-1111-111111111111"},
  "example-gpu-b": {"route_id": 5, "model_id": 6, "gpu_uuid": "GPU-22222222-2222-2222-2222-222222222222"}
}
```

- 确认每条路由保留唯一原生部署目标和唯一 GPU，使用同一模型来源、后端版本和推理配置。每个副本都须启用执行保护，所有可达路径都须执行回执检查。核对实际 Higress 目标、重试落点和无回退，再设置 `gateway_verified: true`。为池内所有 MaaS 目录别名设置 `execution_guard: true`，并授权相应的上游凭据访问池内每条路由。改绑或替换路由时先排空，并重新验证网关配置。共享 GPU 仍须登记已联合验证的布局。
- 发送前，MaaS 从新鲜预算中选择可用 GPU，并在原活动记录中写入路由和 `dispatch_gpu_uuid`。执行端启动回执须匹配该 GPU，且提交时仍有执行资格。原承载进程的可信终态回执可以结清该 token。观测器自动增减已登记 GPU 的额度，新增固定 GPU 路由须加入策略后验证。
- 已获权的请求进入 `UNKNOWN` 时，任何绑定到同一 GPU 的路由都停止新派发。其他已验证 GPU 按剩余全局、项目、模型及异步额度接单。仅有发送意图且无可信启动回执时，沿用原模型和部署池保护。缺少路由证据时保持整库保护。无法证明避开受影响 GPU 的路由走保守准入。

## 自动解封的证据

原派发收到该 token 的完整终态响应，或执行保护收到同一 GPU 进程的可信终态回执，可以自动关闭对应的不确定记录。异步传输终态还核对 task ID。数据库记录 `Transport confirmed completion` 或 `GPU confirmed execution ended`。该范围在所有相关 `UNKNOWN` 都结清后自然恢复。已经终结的任务继续保留原状态，解封本身不会重跑任务或补写成功收据。普通成功响应的结果提交和计量仍走既有事务。

健康检查、worker 上下线、关机通知、队列空闲、计时到期及新请求的成功，都不能证明原 token 的执行已经结束。当前没有可靠的上游按请求查询接口。原连接已彻底丢失时，继续保留隔离，由运维根据实际终止或完成证据通过 `resolve-backend` 或 `resolve-sync` 处理。查看 `drain-status` 可核对 token、模型、发送时的路由及部署 ID。

## 共库部署与回退

迁移 `012` 保留旧账本。旧版本写入的记录没有派发路由证据，若仍为 `UNKNOWN` 则保持整库隔离，直至明确解决。候选 API 与全部执行器在一次排空窗口内升级，先暂停、检查在途及不确定活动，再迁移数据库、部署进程、验证双模型探针并恢复。正式共库前还需验证旧入口过渡、现网授权及实际 GPU 布局。不能用换路由或删任务清理不确定活动。

回退时保留 `012` 和活动账本，在维护窗口确认执行状态后恢复。回退到旧准入代码会恢复整库隔离规则，须在隔离环境演练进程版本切换。
