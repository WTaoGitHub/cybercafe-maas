# 模型服务规模调节器发布与处置

第一阶段处理 GPU 上线导致的只增扩容。GPUStack Server 内插件订阅原生 Worker 事件，定期复核真实可调度 GPU 和模型实例。在基础策略中维护模型清单、显存、优先级、权重与副本范围，通过事件覆盖调整当前规则。首个正式部署仅纳管已上线的 OCR 模型。

## 容器配置

使用已验证的 GPUStack v2.2.3 镜像构建插件 wheel，再叠加到含现有退役插件的 Server 镜像。发布前在镜像内运行插件目录的 `python -m unittest`，核对两个插件入口。保留原容器、数据挂载、端口、退役策略和压力采集输入。切换期间通过 MaaS 运维命令暂停接单、排空请求并使用真实 OCR 恢复探针。整个过程操作容器。

| 变量 | 用途 |
| --- | --- |
| `MAAS_REPLICA_POLICY_PATH` | 私有策略文件，容器只读挂载 |
| `MAAS_REPLICA_PRESSURE_FILE` | 原子替换的压力快照，容器只读挂载 |
| `MAAS_REPLICA_APPLY_CLUSTER` | 允许写入的集群 ID |
| `MAAS_REPLICA_APPLY_MODEL_CAPS` | 固定的部署 ID 到副本上限 JSON 映射 |
| `MAAS_REPLICA_WRITE_JOURNAL` | 持久化写入日志，使用独立的 0700 可写目录 |
| `MAAS_REPLICA_MANAGEMENT_URL` | 同容器回环管理 API |
| `MAAS_REPLICA_MANAGEMENT_KEY_FILE` | 独立管理凭据，文件权限 0600，容器只读 |

启动先用 `observe`。核对部署归属、实际可用 GPU、压力覆盖和在途计数后，通过原子替换文件递增策略修订号并设 `execution.mode=apply`。每次切换模式会清除旧上线事件。管理凭据可通过原子替换文件轮换，插件每次发请求重新读取凭据。轮换时验证新凭据可读后撤销旧凭据。正式首发采用 30 天有效期，提前 7 天由状态检查报告。

## 监控和恢复

- 压力采样用 `--source WORKER_ID=SSH_HOST` 指定来源。添加或替换克隆时，从管理 API 重新核对 worker ID，再通过受信通道更新 SSH 主机公钥、限权采样公钥和采集服务。可调度 worker 未覆盖时停写。维护采集清单时核对 Worker 的 ID、地址和主机公钥，避免沿用旧身份。
- 状态检查运行 `python maas_replica_status.py --policy … --pressure … --journal … --credential-expiry … --worker-id …`，每个采集节点传入一个 `--worker-id`。凭据到期文件存 Unix 秒，权限 0600。非零退出及 `alerts` 数组可供 systemd 和运维监控读取。该检查验证输入和旧日志，插件日志反映实际门槛恢复和在途状态。
- 需要停写时先热更新 `observe` 并核对生效修订。此操作保留已经发出的请求及其对账。持续不可用、请求结果未知、实例长时间未就绪时核对原生部署、实例和 worker 状态。保留写入日志，核清原生副本目标及请求状态后再决定处置。
- 容器级回滚沿用原有暂停、排空和恢复探针。若持久化日志仍有未确认请求，先处理对账。切回原 Observe 镜像后核对旧容器身份、策略、常驻副本和 OCR，再撤销新凭据。
- 删除测试部署后先等待并核对 GPUStack 路由、实例及目标 worker 的容器。路由不存在即完成该项，仍存在才显式删除。若有残留容器，核对唯一试点名称和实例归属后仅清理相应容器。

设计和试验证据见 [副本策略插件](../../docs/framework/REPLICA-POLICY-PLUGIN.md)。
