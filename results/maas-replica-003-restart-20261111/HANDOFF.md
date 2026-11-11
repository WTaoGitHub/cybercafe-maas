# 003 排空与规模调节器恢复验收

**最新结果：自动恢复验收通过。** 管理员启动后，003 的 boot ID 已变化、GPU UUID 保持一致，worker 3 恢复 Ready / direct。恢复受限压力采样后，调节器依次经历 pressure_unavailable、pressure_recovering、open，由真实 capacity_restored 事件自动将部署 1 从 0 增至 1，写入 confirmed；未人工增加副本。实例 3 在 worker 3 上 Running，OCR 1/1。经 Higress 的恢复探针和 MaaS 异步任务均成功、两行，SQL 核对唯一成功用量一条、数量 1，UNKNOWN=0、无告警。MaaS 已恢复，generation 4，HTTP/HTTPS 入口维护拦截已移除并校验、重载。Apply 修订 5、上限 1 保留。证据见 [recovery-validation.json](recovery-validation.json)，原始日志与身份保存在 Mac 私有目录。下文为此前准备记录；第二台 VPC 的 1→2 尚未验证。

2026-11-11 · [模型规模调节器 #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

本轮围绕模型服务规模调节器，先准备 003 的维护窗口，再由管理员使用相同快照启动。GPU UUID、启动 ID 和旧凭据用于关联操作前后记录；不将 E5 同卡接续协议作为本轮独立验收项。不由本会话执行 VM 重启、平台重建或快照还原。

**准备已完成，可以交给管理员操作。** 003 的 OCR 为 0/0，实例及 GPU 计算进程为 0；原生 worker 3 已进入 not_ready，记录保留。MaaS 暂停、generation 3，HTTP/HTTPS 新提交返回 503，查询继续可用；无队列、在途或 UNKNOWN。调节器已恢复 Apply 修订 5、最多 1 副本，观测到空 GPU 容量，模型目标保留为 0，等待管理员启动后的真实恢复事件。压力采样计划性停止，新节点身份核对后再恢复。管理员操作及自动 0→1 尚未发生，不能标为通过。

## 本轮准备和交接条件

- 已核对 003 为 GPUStack 集群 `maas-baremetal-workers`（ID 1）的 worker 3，原始状态 Ready / direct，原始 OCR 为部署 1 的 1/1，实例位于该节点。
- 本机 GPU UUID 与 GPUStack 上报一致。操作前的 GPU UUID、Linux boot ID、worker UUID、旧 worker_token、容器标识及原重启策略保存在 Mac `.maas/003-drain-20261111/`，权限 700/600，凭据不进入公开报告或 Git。
- 已备份当前 GPUStack、MaaS PostgreSQL、入口配置、调节策略、采样来源及容器定义。两套数据库分别用匹配版本的 pg_restore 校验目录；归档已复制至 Mac 并核对 SHA-256。
- 排空顺序：拦截 HTTP/HTTPS 新提交，等待队列及活动清零，再暂停 MaaS；将 MaaS 调节器切为 Observe 修订 4，旧插件继续 Observe。受控将部署 1 降为 0，确认实例与 GPU 计算进程清零后，仅停止 worker 容器并设 restart=no。保留原生 worker 3 记录及其凭据，不删除、退役或重新登记。
- 压力采样按计划停止。等待 GPUStack 原生离线判定后，再将 MaaS 调节器恢复 Apply 并建立空 GPU 基线；模型目标仍保持 0，不手动恢复副本。

最终状态以同目录 `validation.json` 为准；本页的准备过程不替代管理员操作后的验收结果。

## 管理员启动后的调节器验收

1. 管理员使用相同快照启动 003，提供实际 SSH 入口。先核对控制端能观测到该节点 Ready、cluster_id=1、proxy_mode=direct。注册未完成时，记为测试前置条件未满足，不展开 E5 专项验收。
2. 补齐实际节点的受限压力采样和固定 SSH 主机公钥，核对新鲜样本覆盖实际 worker ID。不能沿用旧样本或仅按名称认定覆盖。
3. 从真实订阅和 API 状态核对 GPU 容量恢复事件。等待压力恢复观察期结束，由 MaaS 调节器自动将部署 1 从 0 增至 1，核对持久写入日志及原生读回结果。不得人工增加副本代替验收。
4. GPUStack 原生调度使实例就绪后，核对实际 worker 落点及 Higress 后端，再运行 OCR、MaaS 恢复探针和一笔异步任务，验证唯一成功计量与 UNKNOWN=0，最后恢复业务入口。
5. 如果事件过期、压力缺失、写入结果不确定或需要外部输入，保留证据并暂停验收；不重启 VM、重放不确定请求、清除 UNKNOWN 或伪造新上线事件。

## 与第二台 VPC 的顺序

先完成本轮 0→1。之后单独准备第二台 VPC 的 1→2：同时提高策略和写入授权上限、重新建立事件基线，再由管理员启动第二台并补齐其压力采样。当前上限仍为 1；第二台启动本身不会绕过该限制。调节器增加副本不保证原生调度一定分配到第二台，最终须核对实例落点。
