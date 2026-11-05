# GPUStack E5 worker 自动退役

插件运行在单进程、单实例 GPUStack Server v2.2.3。按配置的 E5 集群处理
`e5-<24位十六进制>` worker。集群是同卡匹配的站点边界，部署时应选择单站点集群。
策略依赖 GPUStack 心跳和设备清单。

| 原因 | 条件 |
| --- | --- |
| `same_gpu_successor` | 旧单卡 worker 的最后心跳至少 30 分钟前。同集群唯一上报同一完整 NVIDIA GPU UUID 的 ready worker 每分钟采样，至少持续 15 分钟，且最新心跳不超过 2 分钟。 |
| `long_offline` | 最后心跳至少 7 天前，即使 GPU 未再次出现。 |

两条规则均要求旧记录为 `not_ready` 且有有效心跳。跳过受保护 ID、维护中的
worker、供应商托管记录和有多个 ready GPU 所有者的情况。采样断档超过两分钟或
Server 重启会重新累计同卡稳定期。删除前重新读取记录并检查队列内的心跳和状态、
分布式或普通模型实例引用及显式 GPU 选择器。每轮最多删除一条，调用 GPUStack
原生 WorkerService 并记录 ID 和原因。

## 安装与启用

构建 wheel 后安装进与现有 GPUStack Server 一致的 v2.2.3 镜像。

```sh
python -m pip wheel --no-deps ./plugins/gpustack-worker-retirement -w /tmp/worker-retirement-wheel
```

- `MAAS_WORKER_RETIREMENT_MODE=off` 为安装后的默认状态。
- 设置 `MAAS_WORKER_RETIREMENT_CLUSTER_ID` 为目标集群数字 ID，并通过
  `MAAS_WORKER_RETIREMENT_PROTECTED_IDS` 传入逗号分隔的受保护 worker ID，
  包括维护配置和外部运维流程仍绑定的 ID。
- 以 `MAAS_WORKER_RETIREMENT_MODE=preview` 启动 Server 容器并核对
  `Worker retirement preview` 日志。核对后改为 `apply` 启用自动退役。
  改回 `off` 并更新 Server 容器可停止后续退役。
- 应用会永久删除旧 GPUStack worker 记录。超过期限又上线的机器需要按 E5
  重新注册流程处理。E17 历史采样保存在 MaaS 数据库，按原采样留存策略保留。
  单实例是队列检查的前提。检查与断线 worker 的未来重连之间仍存在竞态，
  离线期限是退役策略的授权边界。
