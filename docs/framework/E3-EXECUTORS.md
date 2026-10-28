# E3首轮：同机多执行器

2026-10-28。多个独立进程、每进程单任务，共享正式PostgreSQL与artifact卷。GPUStack继续路由到模型副本；不绑定执行器与GPU，不要求后端合批，不恢复collector。

## 运行配置

Compose的`MAAS_EXECUTOR_REPLICAS`控制副本数，默认1；全局和project槽位仍由数据库统一计数，与同步请求共享，不是每个执行器各一份额度。`MAAS_EXECUTOR_STOP_GRACE_PERIOD`默认120s，当前OCR max_runtime=25s。更长模型须先核算推理/鉴权/数据库结束时间并延长退出宽限，不能依赖强杀释放容量。

每个进程具有独立executor ID，日志包含executor_started/task_claimed/task_finished/task_released/executor_stopped及task/attempt，便于关联实例。无key、输入或结果文本进入这些日志。Docker running状态及日志用于实例诊断，尚非独立持久心跳注册中心。

SIGTERM停止新领取，让已发起的任务继续按原租约/心跳结束；恰好在领取后、派发前进入退出状态的任务记EXECUTOR_DRAINING并安全归队，不消耗执行重试预算。未知派发仍由原backend_activity持久意图隔离，不能因进程退出而自动重放。

## 受控扩缩容

在001 root执行：

```bash
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/executor_pool.py status
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/executor_pool.py scale --replicas 2
```

scale首轮允许1–4副本，但并发4不等于已完成性能验收。工具检查API/executor/observer配额一致、无未完成全局维护及池操作，拿维护锁后pause、排空、拒绝UNKNOWN/外部generation变化，保存env副本数并仅更新executor服务。确认数量、running及至少90s退出宽限后，用真实注册OCR探针恢复接单。它不改GPUStack副本、claim、节点或VM，也不自行提高project额度。

操作journal位于`/root/wtaobj/maas-live/executors/`，600；失败保持暂停并要求人工查看，不能直接删pending.json继续操作。未确认真实后端结束时不解除UNKNOWN。恢复步骤是核对journal/generation与实际任务/容器，恢复已备份env中明确的副本/配额/镜像，Compose只更新受影响服务，真实探针通过后才resume；不删库/卷、不重启VM/Docker。备份优先Mac私有目录，不打印env或完整docker inspect。

## 当前验证范围

独立测试库及临时容器使用本轮完整源码，286项MaaS回归通过；真实数据库中断专项1项deselected，本轮不重复停止数据库，前轮专项通过记录保留。其中7项新数据库测试使用spawn独立OS进程，覆盖竞争领取、跨同步/异步额度、暂停、共享文件/GC、SIGTERM、崩溃意图与并发恢复、派发前归队预算。另有7项退出/池保护单元测试。

常驻部署和1/2吞吐测试结果见[本轮报告](../../results/maas-e3-20261028/REPORT.md)。计划中单卡/不同模型副本、冷启动与较低显存claim矩阵分阶段执行；本轮先固定用户设定4个副本及原2GiB声明。部署两个进程不等于性能或全E3验收完成。
