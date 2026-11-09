# GPUStack 无盘 worker 预登记

P1 插件面向 GPUStack v2.2.3、PostgreSQL 和一个指定的 Docker 集群。安装 wheel 后，同时设置以下两项才能启用入口。

- `MAAS_WORKER_ENROLLMENT_CLUSTER_ID` 指定集群 ID。
- `MAAS_WORKER_ENROLLMENT_KEY_FILE` 指向 MaaS 与插件专用的 Bearer 密钥文件，建议使用随机生成的 32 字节十六进制密钥。

MaaS 通过内部链路调用 `POST /internal/maas/worker-enrollment`，发送 `cluster_id` 和完整的 NVIDIA `gpu_uuid`。成功响应包含 `cluster_id`、`worker_id`、`worker_name` 和 `external_id`。名称和 `external_id` 均使用标准化的 `GPU-…`，其中 UUID 的十六进制字母为小写。相同绑定重复请求返回同一个 ID。已有同名记录但绑定不同、绑定不唯一或记录正在退役时返回 `409`。

预登记行以 `initializing` 状态建档，GPU 清单为空，等待原生 worker 注册。worker 的本次启动需要配置响应中的名称和 `external_id`。预登记的 Bearer 凭据只用于 MaaS 服务与插件之间的调用。

## P2 接续许可

设置 `MAAS_WORKER_HANDOFF_MODE=required` 启用 PostgreSQL 会话状态和原生注册入口的许可检查。安装或启用这项策略时，应在任何受管 worker 首次注册之前启用。只有预登记时使用 `GPU-…` 外部 ID 的配置集群 worker 进入此策略。

MaaS 使用同一服务凭据调用 `POST /internal/maas/worker-handoff`，提交 `worker_id` 和本次启动的 UUID `session_id`。后续接续额外提交 `previous_session_id` 和 `fence_receipt`。MaaS 负责确认旧实例已经退出或已隔离，并签发对应凭据编号。插件核对当前会话，并要求曾注册的旧 worker 为 `not_ready` 且心跳已经过期。许可过期同样需要显式核对旧会话后处理。

插件撤销旧 worker 的服务身份和 API key，返回有效期五分钟的 `registration_token`。引导程序将许可传给原生 `GPUSTACK_TOKEN`。插件只允许它用于匹配的原生注册请求，在交给原生处理前持久化消耗状态。注册中断时保留 `registering` 或 `uncertain` 供人工核对。受管 worker 的心跳和状态通过同一行锁校验并写入，避免旧请求排队后覆盖新会话的记录。

P2 验收覆盖新发起的 HTTP 注册和状态请求。系统凭据对受管 worker 和目标集群的原生修改入口受限，目标集群的加入凭据及 manifests 仅供有权限的管理用户获取。受管 worker 也不能读取其他集群的加入凭据。MaaS 确认退出或隔离时，还需核对旧进程、已建立的隧道和在途推理。受管记录应保持许可检查开启，直到完成受控退役或迁移。

`verify_p1.py` 和 `verify_p2.py` 分别在全新、仅容器回环可达的 GPUStack v2.2.3 实例中运行。Server 启动前装入 wheel，使用临时 PostgreSQL、首次创建的 Docker 集群、禁用内嵌 worker，并在容器环境中设置 `GPUSTACK_BOOTSTRAP_PASSWORD` 和上述插件配置。P1 使用默认模式。P2 需要将两个验证脚本放在同一目录，并启用 `required` 模式。脚本使用合成 GPU 清单，P2 仅通过临时数据库模拟心跳和许可超时。

[E5.F3 跟踪记录](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)
