# 模型副本调节器第二轮扩缩容实测：3→8，部分完成

2026-11-24，北京时间。管理员计划新增 7 台 worker VPC，实际新增 5 台 Ready。管理员确认另外 2 台启动异常，本窗口无法加入集群，测试负责人决定结束观测。16:30:28 至 17:13:51 的正式窗口共 521 个有效拓扑样本，最大采样间隔 5.01 秒。更早的 3→9→3 误操作已单独归档。

| 部署 | 基线 Ready / 期望 | 收尾 Ready / 期望 | 调节器新增 | 从确认 +1 到首次观察到 Running |
| --- | ---: | ---: | ---: | --- |
| Unlimited-OCR-2 | 0/1 | 3/3 | 2 | 2 个样本，200.9–216.4 秒 |
| OvisOCR3 | 3/3 | 6/6 | 3 | 3 个样本，93.5–264.7 秒 |
| PP-OCRv4 | 6/6 | 11/12 | 6 | 6 个样本，8.7–97.0 秒；其中一例就绪后发生异常 |

## 已验证

- 16:38:58 首次出现新增 Ready，16:39:19 达到 8 台。配置服务记录了 5 个不同 GPU 的新启动会话，均取得许可并对应 Ready worker。另两台启动异常来自管理员确认，具体故障环节未定位。
- Unlimited 已有的 Pending 于 16:40:34 获得落点并启动，16:44:09 转为 Running。该实例未计入新增。正式窗口另外观察到 11 次期望数 +1；逐次核对同模型的新实例和调节器写入确认时间。
- 16:44:27 确认 PP +1，16:44:39 确认 Ovis +1。16:44:44 同时观察到这两个新实例已分配 worker 且正在启动。调节器记录的全局在途峰值和采样中同时启动的新增实例峰值均为 2。基线的 9 个运行实例全程保留。
- 本轮新增的一例 PP 于 16:47:39 首次 Running，16:54:54 出现 Error，16:55:04 回到 Scheduled。后续短暂出现 Pending 后又回到 Scheduled，截至收尾仍未恢复。故障前后 80 秒内的控制端 GPUStack 容器日志未匹配到该实例的原因。 [REPLICA-INC-004 / Issue #77](https://github.com/WTaoGitHub/cybercafe-maas/issues/77)

## 共享资源观测

控制端 2.5 Gb/s 网口的发送峰值为 2.36 Gb/s（94.3%）。按 520 个相邻采样区间计算，发送速率达到线速 90% 的累计时间约 250 秒，最长连续 75 秒。同期 NFS 服务端读峰值为 253 MiB/s，导出所在块设备的读峰值为 516 MiB/s，单个采样区间设备忙碌率最高 100%。这些是整机计数，各峰值可出现在不同时间。

控制机上两套仓库服务的可读日志中，一套记录到 20 次成功的 manifest GET 和 262 次成功的 blob GET，后者的响应字节合计约 86.9 GB。日志按整个窗口汇总，无法将流量或 PP 实例错误逐项归因于某个模型、单次并行启动或某台 worker 的缓存状态。

## 收尾

10 台稳定、随机关闭 7 台后的 10→3 自动缩容以及该阶段的重试分支均未验证。管理员未收到本轮关机放行通知。现有结果证明在 8 台 worker 的这次实测中可以双启动，带宽与磁盘出现高占用；尚无 10 台稳定运行时的性能结论。

关闭临时并行开关后，常驻调节器读回策略修订 11、有效启动上限 1、Apply 已接管、待确认写入 0。保留 Unlimited／Ovis／PP 的保底顺序 1／2／3 和扩容权重 1:2:4。结束本轮采样后，私有证据已归档到 Mac 并逐文件校验，远端测试采样进程和临时目录已清理。 [测试手册](../../docs/framework/REPLICA-STARTUP-OBSERVATION.md) · [跟踪 Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

## 19 时接续验证：两台接入通过，模型高位收敛受阻

2026-11-24 北京时间 19:11:55–19:17:15 另开采样窗口，共 65 个有效样本，最大间隔 5.06 秒。8 台起点稳定超过 2 分钟。现场使用修订 11、启动上限 1、逐卡新增显存余量和分发共享限速。与此前双并行窗口之间存在观测空档。

两台已开机克隆的原生 worker 记录均为不可达、未就绪，原有开机引导均因超时退出，未创建 worker 容器。核对两台的本次开机身份、各自唯一 GPU 和集群归属，均通过启动预检。在每台依次手动启动既有 E5 systemd 引导服务，两侧均返回 `APPLIED`。19:14:20 首次采到 9 台 Ready，19:14:55 首次采到 10 台 Ready。两台复用了各自的原 worker 记录，高位连续 140 秒，共 29 个样本。该结果验证同次开机手动恢复，自动重试仍需新版快照的真实首启验证。 [开机引导故障 #78](https://github.com/WTaoGitHub/cybercafe-maas/issues/78)

| 模型 | 8 台起点 Ready / 期望 | 10 台收尾 Ready / 期望 |
| --- | ---: | ---: |
| PP-OCRv4 | 11 / 11 | 11 / 11 |
| OvisOCR3 | 6 / 6 | 6 / 6 |
| Unlimited-OCR-2 | 2 / 3 | 2 / 3 |

原有 19 个 Running 实例全程保留，三模型期望数全程相同，没有待确认写入。两个新节点的 GPU 原生显存分配均为零，扣除预留后各有 14.393 GiB 可供新增。接入前已存在的 Unlimited 实例持续 Error，全局在途保持 1，调节器没有发出新增动作。此次没有实际的新模型冷加载，无法据此评价限速下的模型加载性能。

对同一现场快照做内存回放。串行设置等待已有受管实例就绪，切换并行开关的回放得到 `placement_unsettled`；仅在模拟中移去错误实例、把该模型的期望数对齐两个 Ready 并保留原生显存占用，规划器即给出 Unlimited +1。线上副本数由正常调节器管理。节点接入已通过，三模型高位收敛尚未通过，未放行 10→3。 [跨模型扩容阻塞 #82](https://github.com/WTaoGitHub/cybercafe-maas/issues/82) · [Unlimited 实例故障 #80](https://github.com/WTaoGitHub/cybercafe-maas/issues/80)

## 19:34 至 19:42，十节点验证跨模型 Error 隔离

**#82 通过。** Unlimited 的错误实例持续存在时，调节器自动发出四笔逐次 +1，PP 的两个新增副本分别在此次补入的两台 worker 上进入 Running。Ovis 的新实例随后也发生 Error，调节器识别并隔离它，继续向 PP 发出第四笔扩容。三模型全部就绪的高位门槛仍未通过。

| 模型 | 切换前 Ready / 期望 | 收尾 Ready / 期望 | 调节器动作与结果 |
| --- | ---: | ---: | --- |
| PP-OCRv4 | 11 / 11 | 13 / 14 | 19:34:14 和 19:35:59 分别观察到期望 +1，93.1 秒和 96.1 秒后首次观察到相应实例 Running。19:39:44 再次 +1，第三个新实例报推理进程退出或不健康。 |
| OvisOCR3 | 6 / 6 | 6 / 7 | 19:37:47 观察到期望 +1，随后工作负载异步创建失败，报连接中断 `IncompleteRead`。原有六个实例保持运行。 |
| Unlimited-OCR-2 | 1 / 3 | 1 / 3 | 部署切换前，旧版运行期间另有一个实例从 Running 转为 Error。两个 Error 均仍有单卡落点和原生显存申报，自动重启关闭。 |

本地回归 235 项通过，2 项因本地缺少固定版本 GPUStack 运行时跳过。分别以一个和两个 Unlimited Error 的现场快照回放，均只规划 PP +1。候选镜像在无网络的一次性容器中复核后，替换承载调节器的控制端服务。切换期间暂停调节并验证期望数及原 Running 实例集合，随后恢复原有 Apply 授权。部署版本 `6085fac`，策略修订 11，有效启动上限 1。

19:34:02 至 19:42:38 共 173 个有效样本，间隔约 3 秒。同一组 10 台 worker 持续 Ready，切换时的 18 个 Running 实例全程保留。四次期望数变化与四次调节器写入确认对应，在途峰值 1，未观察到未知写入。测试人员未手动修改副本数。

Ovis 关闭了自动重启，其新 Error 随即退出启动名额计数。PP 开启了原生自动重启，末次读取时新实例的重启计数为 5，仍占一个启动名额。该 PP 实例由 GPUStack 调度到一张已有 Unlimited Error 的卡；MaaS 将这张卡排除在容量预算之外，写入副本数时没有指定实际落卡。现有证据不足以归因于显存或 NFS。调节器继续运行，10→3 仍待高位验收条件满足。 [Ovis 创建失败 #83](https://github.com/WTaoGitHub/cybercafe-maas/issues/83) · [PP 恢复跟踪 #77](https://github.com/WTaoGitHub/cybercafe-maas/issues/77) · [调节器验收 #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

## 故障定位补录（2026-11-24，北京时间）

本次仅查询 GPUStack v2.2.3 管理 API、其留存的实例日志以及可访问 worker 的系统日志。19:52 复核时，10 台 worker 仍 Ready，PP、Ovis、Unlimited 分别为 13/14、6/7、1/3。以下数字沿用该轮 GPUStack 的 worker ID 与实例 ID。

| 实例 | 核实的现象 | 待核实事项 |
| --- | --- | --- |
| PP 381，worker 24 | 归档采样四次捕获 Starting 后约 3 秒转 Error。单独观测第 11 次原生重试时，20:10:03 为 Scheduled，20:10:04 为 Starting，20:10:06 为 Error。原生重试计数和启动 PID 都发生变化，模型期望数保持 14。 | 当前及上一次实例日志为空，日志索引无条目。缺容器退出码、OOMKilled、故障时显存峰值及内核记录，仍需主机取证。 |
| Ovis 380，worker 19 | 本机镜像检查未命中，随后拉取到 95%。19:39:30 Docker 报 `panic: bolt.DB.meta(): invalid meta pages` 并退出。同一秒，worker 读取本机 Docker 拉取进度流时返回 `IncompleteRead`。 | 已定位工作负载创建中断的直接原因。无盘系统的底层存储为何损坏，需平台侧证据。 |
| Unlimited 373，worker 24 | 日志有应用启动完成、健康检查和模型列表 HTTP 200，随后出现 `Fatal Python error: Bus error`。19:30:40 前后，SGLang 报调度子进程退出码 -7（SIGBUS）。清理期间，Python 导入代码又遇到 `[Errno 5] Input/output error`。 | -7 是子进程状态。文件映射、共享内存、根盘和 NFS 均不能仅由该条日志定因；清理时的 I/O 错误也不能倒推为第一个故障。 |
| Unlimited 377，worker 20 | 原生状态为 Error，自动重启关闭，重启计数 0。当前和上一次日志均返回空，日志索引无条目。 | 退出码、信号及底层原因均未知。不可沿用实例 373 或已被替换的早前实例的诊断。 |

**Ovis 宿主证据。** 已用实际 GPU UUID 核对 worker 身份。根目录和 Docker 数据目录位于同一 ext4 设备。本次开机的内核日志最早在 17:31:32 记录 ext4 错误，早于 Ovis 本次扩容；19:34:23 记录 inode 位图损坏，19:39:33 起多次记录目录块校验失败。Docker 随后多次启动失败，19:39:39 触发 systemd 启动频率限制。现场 `docker.service` 为 failed，`containerd` 为 active，根盘使用率约 13%，ext4 错误计数为 25。GPUStack 仍收到该 worker 的心跳。镜像传输中断有明确的本机故障时间线，尚无证据确认镜像仓库、共享权重或某个模型导致根盘损坏。

**PP 显存证据。** 在 worker 24 上，两个 Error 的原生显存申报仍为 12 GiB 和 3.5 GiB。19:59 的 GPUStack 遥测显示该卡总量 16,376 MiB、已申报 15,872 MiB、实际使用 433 MiB。这是故障后的快照。GPUStack v2.2.3 的 worker 对工作负载不存在及多种异常状态共用 `Inference server exited or unhealthy.`，该文本不能区分容器退出、健康检查失败或 OOM。

**下一份证据。** 已从指定跳板按用户给出的地址复核 worker 24 和 20；两台的 SSH 默认端口均在认证前返回 `Connection refused`。请通过 VPC 控制台保留现场，先取 SSH 监听、Docker/containerd 服务状态、失败容器的 ExitCode/OOMKilled/FinishedAt 和相邻内核日志，再决定修复方式。本次没有手动修改副本、触发重试、重启服务或节点。 [PP #77](https://github.com/WTaoGitHub/cybercafe-maas/issues/77) · [Unlimited #80](https://github.com/WTaoGitHub/cybercafe-maas/issues/80) · [Ovis #83](https://github.com/WTaoGitHub/cybercafe-maas/issues/83)

## 2026-11-25 处置决定

用户确认目前无法访问相关 worker VPC，本轮宿主排查暂停。同类问题复发时，按新实例 ID、worker 和故障时间关联 GPUStack 状态、worker 管理日志、Docker 退出状态及内核事件。沿用以下两条 Issue 记录后续证据。

- **Unlimited-OCR-2（UOCR2-INC-024）**。旧实例 366 和另一实例 373 均有 SIGBUS 记录，具体触发源未知。补建实例 377 的单卡显存预算满足声明，启动约 3 秒后进入 Error，日志接口返回 HTTP 200、0 字节。worker 的标准日志目录中缺少该实例的启动日志和容器日志。该实例的退出原因仍待取证。 [Issue #80](https://github.com/WTaoGitHub/cybercafe-maas/issues/80)
- **PP-OCRv4（REPLICA-INC-004）**。实例 369 首次退出 Running 的原因未知。18:28 的自动重试已确认受显存调度额度限制，单卡最多可分配 2.99 GiB，模型需要 3.5 GiB。后续实例 381 进入 Starting 后快速报错，容器退出原因也待取证。 [Issue #77](https://github.com/WTaoGitHub/cybercafe-maas/issues/77)
