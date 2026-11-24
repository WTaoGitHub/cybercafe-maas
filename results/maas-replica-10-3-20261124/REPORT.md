# 10→3 实机回落验收

2026-11-24，管理员关闭 7 台 worker，GPUStack 确认剩余 3 台 Ready。MaaS 派发额度随 Ready 副本从 27 降至 8。OvisOCR3、Unlimited-OCR-2 完成自动缩容，PP-OCRv4 的缩容观察计时被周期性原生重调度打断。因此本轮判定为**派发降额与健康副本保护通过，三模型期望副本完全收敛未通过**。 [REPLICA-INC-002](https://github.com/WTaoGitHub/cybercafe-maas/issues/73)

## 现场时间轴

| 北京时间 | 观察 |
| --- | --- |
| 14:05:59 | 仍有 10 台 Ready，三个模型合计 27 个 Ready，MaaS 额度为 27／7／20（总量／同步预留／异步上限）。 |
| 14:06:04 | 首次采到 3 台 Ready。原生模型 Ready 合计降至 8，19 个旧副本处于 `Unreachable`。同轮 MaaS 额度已降至 8／2／6。 |
| 14:10:14 | 首次采到原生重调度后出现明确的资源不足 Pending。 |
| 14:10:31 | Unlimited-OCR-2 进入资源不足观察。 |
| 14:10:51 | OvisOCR3、PP-OCRv4 分别进入资源不足观察。 |
| 14:13:31 | Unlimited-OCR-2 观测满 180 秒，进入收敛。PP-OCRv4 的 12 个 Pending 被原生调度短暂切换为 `Analyzing`，随后返回原有的资源不足 Pending。 |
| 14:13:51 | 首次采到 Unlimited-OCR-2 期望副本减少，OvisOCR3 观测满 181.40 秒。 |
| 14:14:51 | OvisOCR3 完成五次 `−1`，稳定在 3/3。 |
| 14:15:01 | Unlimited-OCR-2 完成两次 `−1`，稳定在 1/1。 |
| 14:16:32 | PP-OCRv4 的同批 Pending 再次转入 `Analyzing`，计时第二次重置。模型仍为 4/16。 |
| 14:19:32 | 第三次采到 PP-OCRv4 重新分析并重置计时。回到资源不足 Pending 后继续维持 4/16。 |

时间均为首次采到相应观测的时刻，典型间隔约 5 秒。GPUStack 先把失联实例记为 `Unreachable`，离线 worker 的实例清理和重调度从最后一次心跳起按原生 300 秒宽限计算。MaaS 的 180 秒从明确的资源不足 Pending 开始，按模型分别计时。 [原生清理逻辑](https://github.com/gpustack/gpustack/blob/v2.2.3/gpustack/server/worker_instance_cleaner.py) · [MaaS Pending 控制](../../maas_platform/replica_pending.py)

## 验收结论

| 模型 | 回落前 Ready／期望 | 回落后 Ready／期望 | 结果 |
| --- | ---: | ---: | --- |
| PP-OCRv4 | 16/16 | 4/16 | 12 个 Pending，观察计时反复重置 |
| OvisOCR3 | 8/8 | 3/3 | 自动收敛到实际 Ready |
| Unlimited-OCR-2 | 3/3 | 1/1 | 自动收敛到实际 Ready，保留最小副本 |

用原生实例 ID 核对缩减前后的 8 个 Ready 实例，集合完全一致。采样期间 MaaS 派发未暂停，`UNKNOWN=0`，调节器无规划超时和悬而未决的写入。此轮只做只读观测，业务请求成功率仍需用实际推理请求独立测量。

GPUStack 调度器默认每 180 秒重新将符合条件的 Pending 入队，与 MaaS 要求的连续 180 秒观察窗口形成冲突。现逻辑在一次 `Analyzing` 快照中丢弃此前观察记录。修复方向是在同一缺资源情形的短暂原生复算前后保留已核实的观察时长，写入前仍用新鲜快照核对 Pending、资源和健康实例。 [原生调度周期](https://github.com/gpustack/gpustack/blob/v2.2.3/gpustack/scheduler/scheduler.py#L85) · [故障追踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/73)
