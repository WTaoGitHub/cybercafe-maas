# E3首轮：同机多执行器

> **2026-10-29 E3-TUNE T0工具已完成**：本地90项及独立PostgreSQL模拟模型63项通过，1项数据库停机实验因缺host harness条件跳过；6/8/12多进程额度、持续补量、完成边界/计量、矩阵选择和运行时保护已验证。隔离工具环境及真实只读检查通过，正式R4/E4/G4/P4/Q1000:100/M2GiB与generation56不变；没有启动真实OCR调优负载。**下一步T0-BASELINE三轮持续供给基线**，随后按计划集成并执行各阶段；不能将工具通过当成更高并发性能通过。[验收](../../results/maas-e3-t0-tools-20261029/REPORT.md) · [操作说明](E3-TUNING-TOOLS.md)。下方“工具未实现”等为先前设计时状态。

> **2026-10-29饱和供给要求已纳入设计**：未来容量测量先预填并持续补充可执行排队任务，供给与轮询/下载解耦；饱和窗口断粮或监测证据不足时不进入容量排名，不裁剪空闲片段。低负载、拒绝和排空按独立用例处理。[设计细则](E3-TUNING-PLAN.md#41-饱和测量的持续供给与有效性条件)，本轮尚未实现新工具或启动负载。

> **2026-10-29后续容量调优已设计**：用户要求继续搜索副本/执行器/G/P/队列/显存申报的合理组合，原基础验收仍有效。[E3-TUNE计划](E3-TUNING-PLAN.md)明确补测6/8及条件更高档，并注明模型专属profile；现有工具仍有1–4等约束，须先按T0改造验证，不能直接执行新矩阵。本次仅计划，不改现网。

> **2026-10-29本轮完成验收**：当前两GPU试点的剩余矩阵、重复稳态、统一同步/异步额度及4→2→4排空扩缩容均通过。914个异步任务成功且attempt/成功事件均为1，18个同步成功及18个容量拒绝对账通过。保留4模型副本、4执行器、全局/project4及2GiB；generation56、UNKNOWN=0、监控正常。下方旧中断为历史，约122秒稳态不代表新8小时/生产SLA，低claim与每实例CPU/RAM硬限额未验。[完成报告](../../results/maas-e3-completion-20261029/REPORT.md)。

> **2026-10-29 E3复测已按异常中断要求停止**：无副本时原生已分配显存指标省略，测试脚本误报StopIteration；指标接口HTTP200、worker正常，未出现新503。未完成新容量点。已保留中断记录、修正脚本（21项回归）并恢复原4副本/真实探针，MaaS接单generation48、UNKNOWN=0、监控正常；本轮未重启负载。详见[E3中断与恢复](../../results/maas-e3-final-20261029/REPORT.md)。


> **2026-10-29网关后续修复**：已稳定复现控制端防火墙误丢弃落入受保护端口范围的出站返回包；将早期过滤迁至conntrack之后的mangle/PREROUTING，并仅允许ESTABLISHED/REPLY。26项本地回归、15项隔离内核检查、三轮副本重建15次OCR、实际入站隔离及一笔异步单计量均通过。MaaS已恢复接单（generation46）、UNKNOWN=0、监控正常；四模型副本和四候选executor保留，未重启VM/Docker/worker。原始单次503缺少源端口证据，Issue #1保持开放观察；母版/克隆网络文件未更新，E3剩余矩阵未自动续跑。[详细报告](../../results/maas-gateway-503-followup-20261029/REPORT.md)。


> **2026-10-29调试及服务恢复**：原503日志定位为Higress/Envoy连接worker8代理10.11.31.3:10150失败（新实例150，UF/local_connection_failure），非无匹配模型路由。16项分层检查、一次4→2→0→4重建的5次新请求均成功，未复现，底层触发原因尚未证实。正式恢复探针46行通过后MaaS已恢复接单（generation44），UNKNOWN=0、监控正常；原4副本/选择器保持，4个候选executor不变。**未修复定因、未自动续跑E3，E3未完成验收。** 下方paused43为昨日历史现场。[调试报告](../../results/maas-e3-debug-20261029/REPORT.md)。


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

scale首轮允许1–4副本，但并发4不等于已完成性能验收。工具检查API/executor/observer配额一致、无未完成全局维护及池操作，拿维护锁后pause、排空、拒绝UNKNOWN/外部generation变化，保存env副本数并仅更新executor服务。显式提供`--global-limit`和`--project-limit`时，会在同一暂停窗口一起更新API/executor/observer，保证额度一致；两个参数必须同时提供且project不大于global。确认数量、running及至少90s退出宽限后，用真实注册OCR探针恢复接单。它不改GPUStack副本、claim、节点或VM；未显式提供额度参数时不提高project额度。

操作journal位于`/root/wtaobj/maas-live/executors/`，600；失败保持暂停并要求人工查看，不能直接删pending.json继续操作。未确认真实后端结束时不解除UNKNOWN。恢复步骤是核对journal/generation与实际任务/容器，恢复已备份env中明确的副本/配额/镜像，Compose只更新受影响服务，真实探针通过后才resume；不删库/卷、不重启VM/Docker。备份优先Mac私有目录，不打印env或完整docker inspect。

## 2026-10-29补充验证工具

`benchmark_executors.py`在worker指标采集失败、同步/异步UNKNOWN、组合在途超全局额度或维护generation变化时停止新增派发，并将采样异常计入失败结果；合法缺失的可选指标仍记录null，不等同连接故障。已发出的请求收尾，不自动重放。

`benchmark_mixed_ingress.py --run-id <唯一编号>`要求四执行器及全局/project额度4、无在途且未暂停的受控窗口。一个客户端有界提交24个异步任务，三个同步客户端各发12次请求，仅明确的429/SYNC_CAPACITY属于预期拒绝。周期核对实际组合在途不超过4、至少出现一次同步/异步重叠；异步按任务命名空间核对成功、attempt=1及唯一事件。同步按空闲测试窗口前后token差集对账成功事件，不能当作持久request-ID映射或有其他同步流量时的精确归因。其他流量、未观察到重叠/拒绝或结果不一致均不得标记通过。本工具不扩缩模型/执行器、不改变额度。

相关本地30项回归通过；常驻实测结果单独记录，工具存在不代表剩余E3已经验收。

## 当前验证范围

独立测试库及临时容器使用本轮完整源码，286项MaaS回归通过；真实数据库中断专项1项deselected，本轮不重复停止数据库，前轮专项通过记录保留。其中7项新数据库测试使用spawn独立OS进程，覆盖竞争领取、跨同步/异步额度、暂停、共享文件/GC、SIGTERM、崩溃意图与并发恢复、派发前归队预算。另有7项退出/池保护单元测试。

常驻部署和1/2吞吐测试结果见[本轮报告](../../results/maas-e3-20261028/REPORT.md)。计划中单卡/不同模型副本、冷启动与较低显存claim矩阵分阶段执行；本轮先固定用户设定4个副本及原2GiB声明。部署两个进程不等于性能或全E3验收完成。

## 2026-10-28当前暂停现场

常驻已运行4个候选执行器，共享全局/project额度4；1/2各两轮、4一轮，共210个任务的唯一成功事件对账通过。但后续原生模型容量矩阵dual-4预热收到HTTP503，已按用户要求主动中断，不重试、不自动续跑。MaaS paused=true/generation43，模型仍4副本、原选择器/哈希匹配，双卡各2 running；没有执行最终恢复接单探针。当前不是E3最终发布/验收状态。应先处理503并明确恢复步骤，不能直接开始下一轮压测或按容器running判断入口正常。详见本轮报告及interruption-state.json。
