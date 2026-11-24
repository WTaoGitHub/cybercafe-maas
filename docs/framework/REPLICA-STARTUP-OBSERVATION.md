# 3→10→3 启动压力观测手册

在现有 3 台 worker 稳定运行时启动观测，再由管理员上线 7 台新 VPC。等扩容达到可行目标并稳定后，由管理员随机关闭 7 台，继续观测到剩余 3 台的服务稳定。全程使用当前启动策略。扩容结果包括容量不足时的 Pending 及其原因。 [测试与后续调优](DYNAMIC-CONCURRENCY-IMPLEMENTATION.md#3103-的模型启动压力验收)

## 第二轮准备单（2026-11-24，等待管理员窗口）

本轮只验收模型副本调节器。管理员负责新增和随机关闭 VPC；测试方从控制端只读观测。模型期望数由已启用的调节器写入。

| 首轮实际发生的事情 | 修复和已取得的证据 | 第二轮要回答的问题 |
| --- | --- | --- |
| 9、10 台 worker 已 Ready，目标仍停在 8 个副本。读取 GPUStack 仅用几十毫秒，布局搜索触及 1 秒时限，将时限放宽到 2 秒仍超时。 | 增量规划只为本轮启动名额证明可放置的副本。现场 10 台最终达到 PP-OCRv4 16/16、OvisOCR3 8/8、Unlimited-OCR-2 3/3。本地另覆盖 13 模型、100–1000 台模拟节点。 [扩容故障 #71](https://github.com/WTaoGitHub/cybercafe-maas/issues/71) · [修复验证](../../results/maas-replica-incremental-20261124/REPORT.md) | 从 3 台开始即运行新版，新增 worker 是否解除模型原有的 holding，并在本轮实际布局下自动扩到可行规模？记录规划阻塞原因、每模型首次新增 Ready 及完全稳定时间。 |
| 10→3 后，另外两模型已收敛，PP-OCRv4 因同批资源不足 Pending 被 GPUStack 短暂切回 Analyzing，180 秒计时反复归零，卡在 4/16。 | 对短暂且符合条件的重新分析暂停计时，重新确认缺资源后接续。首轮现场修复后，调节器连续 12 次各减 1，最终 4/4，保留原有 4 个健康实例。 [缩容故障 #73，已关闭](https://github.com/WTaoGitHub/cybercafe-maas/issues/73) · [分段实测](../../results/maas-replica-10-3-20261124/REPORT.md) | 随机关闭 7 台后，在新一轮实例和落点上观察三模型自然收敛。按部署核对观察时间、健康实例、缩容下限和每次减少的目标。 |
| 自动缩容失败后，能否安全重试也需要证据。 | 新版已部署。同一镜像的隔离回放验证了未发出的临时失败退避后重算，以及已送达但响应丢失时只读核对。新控制卷偶发的规划状态误报另行跟踪。 [重试 #75](https://github.com/WTaoGitHub/cybercafe-maas/issues/75) · [诊断 #76](https://github.com/WTaoGitHub/cybercafe-maas/issues/76) | 取一次正式自然缩容的读回。如出现临时故障，分别核对重算或只读恢复；没有发生的故障分支标记为「未触发」。保留现有控制卷，不注入故障。 |

**准备时的三节点基线。** 北京时间 15:22:42–15:24:42、每 5 秒采样，共 25 个有效样本。常驻镜像版本 `f38506d`，策略修订 8，Apply 已接管。三模型按 PP／Ovis／Unlimited 排列为 4/4、3/3、1/1，8 个运行实例的 ID 集合保持一致。完整快照、启动在途 0、待确认写入 0。三个部署均处于 holding；新的可用资源会触发重新评估。有效启动上限为 1，压力自适应关闭。控制端网卡、NFS 服务端字节／RPC 计数和磁盘计数可读，已验证至少一个镜像仓库的日志可读取。正式窗口按实际镜像来源选择仓库并确认新 worker 的缓存情况。

| 操作时点 | 谁操作、要留下什么证据 |
| --- | --- |
| 收到开测通知 | 测试方先从独立于本轮随机关机名单的管理路径接入控制端。用下文采集命令在新的私有目录开始记录，现场再次确认 3 台 Ready、版本、写入授权及空日志。保留至少 2 分钟起始样本，确认采集连续后通知管理员启动 7 台。 |
| 3→10 | 管理员记录启动时间；按本轮实际 Ready worker 和实例 ID 识别新增资源。测试方记录首次观察到 worker Ready、调节器目标增加、引擎下载／启动及 Ready 的时刻，统计每模型耗时和实际在途峰值。单次新增 1、调节器发起的启动在途最多 1。GPUStack 自行重调度已有 Pending 时单列。 |
| 10 台稳定 | 10 台 Ready、三模型就绪与期望相符、可放置的新增名额已用完、在途和待确认写入均为 0；连续观察至少 2 分钟后通知管理员随机关闭 7 台。Unlimited／Ovis／PP 的策略权重为 1:2:4；首轮按 PP／Ovis／Unlimited 排列的 16/8/3 用作对照，实际数量取决于本轮 GPU 容量及落点。若出现持续阻塞，先保存现场，再决定是否进入回落阶段。 |
| 10→3 | 分别记录管理员操作、GPUStack 首次观察到失联、资源不足 Pending 首次确认、首次减量、最后收敛的时刻。原生清理宽限约 300 秒（自末次心跳起），MaaS 满 180 秒已确认的资源不足才逐一缩减。对幸存节点上的健康实例取 ID 对照，核对目标下限 `max(健康副本数, 策略最小值, 自动缩容下限, 1)`。 |
| 收尾或异常 | 剩余 3 台稳定至少 2 分钟，记录每模型实际 Ready／期望、在途和待确认写入。已到下限仍 Pending 时记录资源证据及 holding。如出现健康实例被意外删除、持续规划阻塞、未确认写入或证据中断，先保留时间线并停止推进下一项管理员操作。对未确认写入只核对，保留日志。 |

采集器按模型记录 `pending_control` 的阶段和有效观察秒数，以及可获得的 `downscale_retry`、`write_attempted` 和待确认动作的模型与副本数。5 秒轮询可能跨过一次完整写入，不能从缺少样本推断没有重发。遇到 #75 情形时，再与时间对齐的调节器记录、原生管理端请求记录（如可取得）和目标／实例读回交叉核验，注明缺口。

已在常驻镜像内通过标准输入运行更新后的只读脚本，连续三次读到三个模型的 holding 状态与对应目标。采集脚本按模型筛选及缺失字段的回归均通过；调节器与插件本地回归共 225 项通过、2 项因需要固定版本的 GPUStack 运行环境而跳过。

本轮固定当前启动档位，采集网络、镜像仓库和 NFS 的共享压力。冷读需要实际读流量证据。启动档位 1/2/4 的独立对比、根据共享压力自动调批、千节点真机容量另行验证。业务 API、计费和请求并发不在本轮范围。 [启动压力任务 #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

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
