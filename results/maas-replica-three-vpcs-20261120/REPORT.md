# 三台 VPC 冷启动后的副本自动调整

2026-11-20 · GPUStack v2.2.3 · 调节器策略 revision 8 · [调节器 Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

**调节器实测通过。** 现场补齐 worker 依赖后，Unlimited-OCR-2、OvisOCR3、PP-OCRv4 自动达到目标和 Ready 数 **1 / 2 / 5**。三种模型当前的 MaaS 派发额度均为 0，业务派发保持暂停。

## 关机和零容量

两台原在线 worker 的三套部署先排空并归零。GPUStack 回读确认实例为 0，GPU 显存约 433 MiB/卡。管理员负责 VPC 关机、开机，MaaS 控制面持续运行。观察到可调度 GPU 为 0 后启用 Apply，两轮稳定对账均保持 0 / 0 / 0，未写入副本数。

## 三台注册和首次故障

三台使用同一份快照的 VPC 注册后，GPUStack 观察到三张不同的 Ready GPU，每张约 15.99 GiB。首个 PP-OCRv4 副本由调节器写入，放置在 worker 8（实例 266）。当前和前一次启动日志均出现 `Worker execution credential must be a private file`。副本反复进入 ERROR，串行模式等待该已提交副本，目标停在 0 / 0 / 1，Ready 为 0 / 0 / 0。顺序为 Unlimited-OCR-2、OvisOCR3、PP-OCRv4。 [CAPACITY-INC-004 / Issue #70](https://github.com/WTaoGitHub/cybercafe-maas/issues/70)

逐台核对确认 worker 8、9、10 的宿主与 worker 容器均看到模型目录。三台宿主都缺少独立执行凭据文件和用于验证回执服务的证书。MaaS 执行凭据表只涵盖当前三张 GPU 中的一张。三台安装的启动包都缺少限额模块，worker 创建路径也没有调用该模块，因此受保护版本的 OvisOCR3 与 Unlimited-OCR-2 缺少创建时 CPU、内存和 swap 限额。

## 现场修复

- 按三张实际 GPU UUID 核对归属，复用一项已有登记并增加两项。把各 GPU 的私有凭据文件和经 TLS 验证的配套公钥证书写入本机缓存目录，回读确认 worker 容器可见且私有文件模式为 `0600`。
- 复用仓库现有的限额模块，在核对固定 worker 镜像和 Docker 运行时代码后，补齐当前三个 worker 容器内的规则。Unlimited-OCR-2 新容器已读回 4 CPU、16 GiB RAM 和零 swap 的 cgroup 限额。热修过程中未重启 VM、worker、Docker 引擎或 MaaS API。
- PP-OCRv4 原实例通过 GPUStack 的自动重试恢复至 Ready。随后只恢复 MaaS 调节器开关，由调节器写入后续目标。全程保持 MaaS 派发暂停，避免将容量验收当作业务流量的恢复条件。

以上修复只覆盖本轮三个 VPC。新克隆还需要在首次注册之前自动准备 GPU 对应的执行凭据及证书，并随母版发布含限额模块和调用路径的启动包。生产可用性还需在一次新克隆中核对实际容器挂载和 cgroup 限额。相关工作继续跟踪 Issue #70。

## 自动扩容结果

从 PP-OCRv4 原实例恢复开始，观察到目标按 `0/0/1 → 1/0/1 → 1/1/1 → 1/1/2 → 1/1/3 → 1/1/4 → 1/1/5 → 1/2/5` 变化。最终连续两个完整快照保持 `1/2/5`，调节器处于 idle、无未确认写入。八个实例的 `GET /health` 均返回 HTTP 200。其中五个 PP-OCRv4 实例的健康响应均确认执行保护开启，两个 OvisOCR3 和一个 Unlimited-OCR-2 实例读回 4 CPU、16 GiB RAM 和零 swap。

| Worker | 最终布局 | GPUStack 申报显存 | MaaS 共卡准入 |
| --- | --- | ---: | --- |
| 8 | OvisOCR3 × 1 + PP-OCRv4 × 2 | 13 GiB | 未登记 |
| 9 | Unlimited-OCR-2 × 1 + PP-OCRv4 × 1 | 15.5 GiB | 未登记 |
| 10 | OvisOCR3 × 1 + PP-OCRv4 × 2 | 13 GiB | 未登记 |

在三张完全空闲 GPU 上做只读规划时，求解器曾给出 `1/2/6`。实际启动逐个交由 GPUStack 落位，随着卡上占用变化，最终规划收敛为 `1/2/5`。MaaS 根据实际共卡布局单独计算业务额度，上表两种组合未在当前策略的已验证集合内，三个池均返回 `no_eligible_replica`。本次覆盖副本自动调节、启动及健康检查，尚无这些共卡组合的业务推理验收。

调节器继续处于 Apply，保留实际自动分配的副本。MaaS 派发继续暂停，最终回读在途及未知为 0。三个节点已核对本次原子写入的临时文件数量为零。未创建测试任务或测试数据。
