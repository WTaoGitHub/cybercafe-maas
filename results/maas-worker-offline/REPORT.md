# 既有 worker 002 离线与恢复测试报告

2026-10-20 · GPUStack v2.2.3 · 控制端 XZ-31-001 · 故障节点 XZ-31-002。

## 结论

三项实机测试通过，82项回归通过、1项跳过。P2按本报告的故障范围通过，可以进入P3全局维护控制。实际整机断电、双系统切换及8小时耐久仍未验证。

最重要的发现：**停止GPUStack worker，不代表它启动的推理容器已经停止。** 因此，节点失联或HTTP超时后，系统保留UNKNOWN后台活动并阻止后续派发；不能直接重试任务或宣告节点可安全切系统。

## 测试范围与方法

用户明确允许中断既有002服务、当前无客户请求。本轮直接使用原worker，未追加GPUStack worker或Docker daemon，001控制端保持运行。

创建临时模型 `maas-ocr-002-offline`，通过worker selector固定002，使用与P2试点相同内容的镜像；独立临时身份、任务数据库和artifact隔离测试。临时任务接口仅监听001本机。每次故障前设置180秒自动恢复定时器，执行结束主动恢复。

在途测试先暂停专用测试推理容器，再观察任务DISPATCHING后注入故障，稳定制造请求无法完成的情形。这验证已进入派发阶段的超时与不确定性处理，未证明GPU内核正在计算时被中断，也未通过上游日志证明请求已到达推理进程。

网络隔离只丢弃001↔002非SSH流量，保留双向TCP22；未关闭网卡、重启宿主机或模拟外部网络全部断开。

## 实测结果

| 场景 | 观测到worker不可用 | 任务与结果 | 恢复验证 |
|---|---|---|---|
| 空闲停止worker | 36.09秒 | 历史结果仍可下载；推理容器仍在运行 | 原worker恢复后，新OCR成功46行 |
| 在途强制停止worker | 22.46秒 | FAILED / UNKNOWN_BACKEND_ACTIVITY，attempts=1；新mock任务保持QUEUED | 确认实际后端终止后解除UNKNOWN，mock及新OCR成功 |
| 在途节点间网络隔离 | 25.31秒 | worker端口不可达；UNKNOWN与排队保护成立；历史结果可下载 | 清除隔离、恢复worker与模型后，新OCR成功46行 |

以上为单次测试的观测延迟，包含控制面刷新、轮询与脚本耗时，不是恢复SLA或统计性能指标。

三个离线阶段，各做一次原同步OCR探针，均返回HTTP200、46行。不能据此推断请求必定由001处理：停止worker后002推理进程可能仍在，且GPUStack网关存在透明重试；本轮没有逐请求定位执行节点。异步试点collector要求两节点就绪，002离线时health为WORKER_UNAVAILABLE，停止授权新OCR派发；自动缩减可用节点集合尚未实现。

两次在途故障均按以下顺序恢复：测试模型期望副本设为0 → 确认既有002 worker停止 → 删除专用测试推理进程并确认无残留 → 人工解除UNKNOWN → 排队mock任务完成 → 恢复worker/副本 → 核对版本快照 → 新OCR成功。没有根据“worker离线”本身解除UNKNOWN。

**attempts=1仅证明任务层没有重放，不能证明GPU只执行一次。** 实测Higress允许额外2次上游重试（最多3次尝试），应用层没有重发不等于端到端恰好执行一次。

## 数据一致性、回归与收尾

本轮共8个任务：6成功（4个OCR、2个mock），2个预期失败。成功计量事件6条，与成功任务数一致；清理前未决UNKNOWN为0，未产生多个成功计量事件。

回归结果：**82 passed, 1 skipped，6.23秒**。跳过项为需宿主机编排的真实数据库停启专项；此前P2已有独立通过记录，本轮未重复。另有一条Starlette依赖弃用警告。

收尾复核：

- 原 `rapidocr-gpu` 与保留的 `maas-ocr-p2-r2` 各2个实例，分别位于001/002；四个实例逐一直接请求均HTTP200、46行。
- 两个worker均ready；002原worker容器ID不变；001 collector active且health正常。
- 临时模型、路由、身份、任务API/执行器、测试数据库和artifact已清理；测试PostgreSQL容器已停止，保留既有测试环境卷。
- 专用测试推理容器无残留；故障iptables链已删除；活动故障恢复定时器为0。

## 下一步

开始P3/W6：全局暂停派发、限制同步旁路、核对在途及UNKNOWN、恢复前探测。随后做队列/项目并发限额、TTL和指标；P4再发布常驻任务服务、HTTPS、备份恢复和至少8小时负载验收。

本轮不增加自动关机、自动重试未知任务或Windows/Ubuntu切换承诺。控制端001故障、高可用、外部公开入口和多租户商业运营仍属于后续范围。

## 可追溯材料

- [空闲停止证据](idle.json)、[在途强停证据](kill.json)、[网络隔离证据](partition.json)
- [任务与计量收尾](cleanup.json)、[四实例复核](final-verification.json)、[002状态](final-node.json)
- [节点清理](node-cleanup.json)、[定时器及数据库收尾](final-cleanup.json)
- [回归日志](worker-offline-regression.log)、[JUnit记录](worker-offline-regression.xml)
- [故障执行器](../../scripts/maas/offline/node002.py)、[控制端脚本](../../scripts/maas/offline/controller.py)、[本地编排](../../scripts/maas/offline/run_tests.py)、[只读收尾复核](../../scripts/maas/offline/verify.py)

测试脚本固定现有节点及临时资源作用域，并非通用生产维护工具。重新执行前需按脚本检查私密配置、端口、测试数据库与状态文件，不能直接复用旧测试状态。
