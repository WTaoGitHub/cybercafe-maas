# 新 worker 注册后未自动扩容：只读排查

2026-11-12 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

**直接原因：压力采样仍绑定退役的 worker 3，未覆盖新 worker 4、5，且样本已过期。** 调节器收到了容量恢复事件并计算出扩容候选，但执行被 pressure_unavailable 拦截。600 秒后事件过期，转回等待 GPU 上线。当前一个副本是用户后续人工设置，不能当作自动扩容证据。

## 时间线（北京时间）

| 时间 | 证据 |
| --- | --- |
| 11 月 11 日 15:52:26 | 压力文件最后成功样本，worker_ids=[3] |
| 15:52:31 | 采样服务记录 Pressure sample withheld: CalledProcessError |
| 19:31:04 | worker 4、5 原生注册记录 created_at，分别为 003、004，两条记录几乎同时创建 |
| 19:31:20 | capacity_restored；current={1:0}、policy_target={1:2}，但 pressure_unavailable、pressure_up_preview_limit=0、execution_state=pressure_unavailable |
| 19:41:25 | waiting_for_gpu_arrival，pressure_unavailable，execution_state=idle；符合 600 秒事件窗口到期 |
| 20:02:19 | 当前模型 updated_at，当前目标 / 就绪 1/1；用户说明期间人工修改。该时间戳不是完整操作审计，不能独立证明操作者 |

用户估计启动时间约为 19:15，原生注册和调节器日志中的确切时间如上。当前 E17 容器当日 13:51:55 创建，日志覆盖上述事件，未重启获取证据。

## 原因与边界

策略仍为 Apply 修订 7、部署 1 上限 2，最大样本年龄 20 秒、恢复观察期 10 秒，要求压力样本覆盖全部参与规划的 worker。采样来源文件仍只有 worker 3；读取时最后样本已过期约 19.5 小时。采样服务 active 只表示进程运行，不能证明采样成功。

因此本轮不是调节器漏掉事件，也不是模型引擎启动失败：扩容写入尚未发出。未确认写入日志为空，事件日志没有成功扩容 confirmed。当前人工设置的副本保持不变，本次未更改策略、采样、身份或副本，未重放事件。

源码交叉核对：`maas_replica_pressure.PressureGate.evaluate` 在过期或覆盖不足时返回 pressure_unavailable；`maas_platform.replica_observe.run` 将可执行扩容预览归零，并在 arrival_window_s 到期后清除事件。

## 建议（未实施）

1. 更新现役 worker 的实际 ID、固定 SSH 主机公钥和受限压力采样来源，移除退役来源，核对所有参与规划节点均有新鲜样本。CalledProcessError 的底层网络或密钥原因尚未专项验证，不作推断。
2. 采样来源随 worker 登记、退役自动维护，这是当前无人值守调节链路的集成缺口。
3. 暴露样本年龄、缺失的 worker ID、持续阻塞时间和事件过期状态，避免只看 active 误判。
4. 明确事件过期后的有界恢复契约。目前只修复压力源，不会重放已过期事件；不得伪造事件或人工增加副本冒充自动验收。

下一轮验收前重新确认现役节点、人工副本目标和上限。原始证据位于 Mac `.maas/replica-investigation-20261112/`（700/600），不发布连接、口令或原始私有日志。
