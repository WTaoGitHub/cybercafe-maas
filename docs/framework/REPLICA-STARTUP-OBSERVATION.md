# 3→10→3 启动压力观测手册

在现有 3 台 worker 稳定运行时启动观测，再由管理员上线 7 台新 VPC。等扩容达到可行目标并稳定后，由管理员随机关闭 7 台，继续观测到剩余 3 台的服务稳定。全程使用当前启动策略。扩容结果包括容量不足时的 Pending 及其原因。 [测试与后续调优](DYNAMIC-CONCURRENCY-IMPLEMENTATION.md#3103-的模型启动压力验收)

## 开始前

1. 记录采集机、控制端、仓库、NFS 和参考 worker 的 UTC 时间及时间同步状态。若时钟偏差接近采样间隔，记录偏差和来源，以校正后的时间比较。管理员标记上线和关闭动作的时刻。节点名单使用当轮 GPUStack 记录。
2. 在受信运维端使用 `umask 077`，将各路输出存放到同一私有临时目录。清理清单登记本次有界采集命令、采样文件与计划使用的探针。采集期间保留原始记录供核验，结束后归档脱敏结果并清理临时进程与文件。有授权探针时，沿用测试项目及请求前缀登记、核对和回收数据。
3. 在已有 3 台时先采集至少 2 分钟。记录部署 ID、模型名、当前策略版本、期望和 Ready 副本数。选一台已有 NFS 挂载的参考 worker。在首个加载 NFS 权重的新 worker 上也可启动一次限时采集，以覆盖冷读。仅访问 `/proc/self/mountstats`，避免提前打开权重而改变缓存条件。

## 同一时间轴的指标

| 位置 | 采集 | 判读 |
| --- | --- | --- |
| MaaS 调节器与 GPUStack | 使用下文的脚本记录 worker Ready、每个模型期望及 Ready 数、每个原生副本状态、调节器在途数、有效上限及最近确认写入时间 | 按副本 ID 连接 Pending、Scheduled、下载、初始化、启动和 Running。写入确认、原生调度和副本 Ready 是独立时间点。短于采样间隔且没捕获的阶段标记为未观测。已存在的 Pending 后续启动单独统计。 |
| 共享网卡 | 在控制端及独立仓库或 NFS 主机运行 `sar -n DEV,EDEV 5 120`，筛选去往 worker 的实际接口 | 记录收发速率、丢包及错误，保留峰值和持续时间。共享接口包含其他业务流量。 |
| 镜像仓库 | 从实际服务 worker 的仓库实例读取窗口内 Docker 日志，使用 `docker logs --timestamps --since "$START_UTC" --until "$END_UTC" "$REGISTRY_CONTAINER"` | 分别汇总 blob／manifest 的 HTTP 状态、耗时和样本量。区分缓存命中和冷拉取。共享流量不能直接归为镜像下载，先用仓库请求和原生副本事件交叉核对。 |
| NFS 服务端与磁盘 | 每 5 秒保存 `/proc/net/rpc/nfsd` 的 `io`、`rpc`、`proc4ops` 行。在权重服务端运行 `iostat -d -x -t -y 5 120`。用 `findmnt -n -o MAJ:MIN,FSTYPE --target "$EXPORT_ROOT"` 和 `lsblk` 确认权重盘 | `io` 相邻差值除以间隔为读写吞吐，`r_await` 为磁盘读等待，结合读请求量、队列和 `%util` 评估。磁盘等待包含同盘其他读请求。 |
| NFS 客户端 | 在参考 worker 采集目标挂载的 `/proc/self/mountstats`，命令见下文 | 每区间 `READ` 累计往返毫秒差除以操作数差，得到平均 RPC RTT。记录重传和调用超时；无 READ 或计数重置时标作无样本。 |
| MaaS 推理与派发 | 复用 API 容器产物根目录的 `.observability/metrics.prom` 和常驻监控状态。结合已有访问记录，记录实际同步请求、异步排队／执行时长、错误、`UNKNOWN` 和运行中额度 | 同步平均处理耗时可用 `maas_sync_completed_duration_seconds_sum` 差值除以其 `_count` 差值。该耗时来自服务内活动记录，端到端延迟从已有请求记录或经授权的低频探针单独取。保留指标更新时刻，切分阶段报样本量，空样本不计算延迟。 |

`sar` 和 `iostat` 使用 `TZ=UTC LC_ALL=C S_TIME_FORMAT=ISO`。示例窗口 10 分钟，按管理员操作窗口调整次数。NFS 服务端和参考 worker 的示例采样均从已登录的对应主机执行，输出接入同一私有记录目录。

```sh
# NFS 服务端
for i in $(seq 1 121); do
  date -u +%Y-%m-%dT%H:%M:%SZ
  awk '$1 ~ /^(io|rpc|proc4ops)$/' /proc/net/rpc/nfsd
  [ "$i" -eq 121 ] || sleep 5
done

# NFS 客户端，使用现有模型挂载点
for i in $(seq 1 121); do
  date -u +%Y-%m-%dT%H:%M:%SZ
  awk -v target=/srv/maas/models '
    /^device / { selected = ($5 == target && $8 ~ /^nfs/) }
    selected && $1 == "READ:" { print; found = 1 }
    END { if (!found) print "mount_read_counters_unavailable" }
  ' /proc/self/mountstats
  [ "$i" -eq 121 ] || sleep 5
done
```

首次客户端记录只作为累计计数基点。服务端和客户端均用实际时间间隔计算速率，缺失和计数归零时保留缺口。仓库原始日志仅供内部核对，归档时保留路径分类、状态、耗时及汇总字节数。

在有仓库工作区及控制节点运维入口的环境中，将 [采集脚本](../../scripts/maas/runtime/observe_replica_startup.py) 通过标准输入传给控制端现有调节器容器内的 Python，输出留在私有目录。脚本端只做 GPUStack GET 和本机状态文件读取。

```sh
# CONTROLLER_SSH 为现有 SSH 别名，REPLICA_CONTAINER 为现有调节器容器。
ssh "$CONTROLLER_SSH" docker exec -i "$REPLICA_CONTAINER" python -B - \
  --samples 121 --interval 5 \
  < scripts/maas/runtime/observe_replica_startup.py > "$OBS_DIR/replicas.jsonl"
```

## 收尾

- 按「3 台稳定 → 7 台上线 → 10 台稳定 → 7 台下线 → 3 台稳定」输出各模型首次新增 Ready 和达到可行目标的耗时、调节器在途峰值、原生启动峰值，以及每路资源指标与推理状态。按模型分别注明镜像冷拉取、NFS 权重读取和缓存未知的情况。
- 先用本轮记录判断瓶颈和现有策略的投入效果。后续对比启动档位时复用同一采集方法、同一业务样本和可比的缓存条件。 [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)
