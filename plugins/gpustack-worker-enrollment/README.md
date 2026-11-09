# GPUStack 无盘 worker 预登记

P1 插件面向 GPUStack v2.2.3、PostgreSQL 和一个指定的 Docker 集群。安装 wheel 后，同时设置以下两项才能启用入口。

- `MAAS_WORKER_ENROLLMENT_CLUSTER_ID` 指定集群 ID。
- `MAAS_WORKER_ENROLLMENT_KEY_FILE` 指向 MaaS 与插件专用的 Bearer 密钥文件，建议使用随机生成的 32 字节十六进制密钥。

MaaS 通过内部链路调用 `POST /internal/maas/worker-enrollment`，发送 `cluster_id` 和完整的 NVIDIA `gpu_uuid`。成功响应包含 `cluster_id`、`worker_id`、`worker_name` 和 `external_id`。名称和 `external_id` 均使用标准化的 `GPU-…`，其中 UUID 的十六进制字母为小写。相同绑定重复请求返回同一个 ID。已有同名记录但绑定不同、绑定不唯一或记录正在退役时返回 `409`。

预登记行以 `initializing` 状态建档，GPU 清单为空，等待原生 worker 注册。worker 的本次启动需要配置响应中的名称和 `external_id`，并通过目标集群的原生凭据注册。该端点的 Bearer 凭据只用于服务端预登记。实例授权、旧实例退出确认和原生注册入口的访问控制属于 P2 交接。

`verify_p1.py` 在全新、仅容器回环可达的 GPUStack v2.2.3 实例中运行。Server 启动前装入 wheel，使用临时 PostgreSQL、首次创建的 Docker 集群、禁用内嵌 worker，并在容器环境中设置 `GPUSTACK_BOOTSTRAP_PASSWORD` 和上述插件配置。脚本使用合成 GPU 清单，覆盖权限、并发建档、客户端断连后的重试、同名冲突及原生 worker 接续。

[E5.F3 跟踪记录](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)
