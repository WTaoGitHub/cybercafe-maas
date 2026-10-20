# P3 受控维护首批实现与验收

2026-10-20 · GPUStack v2.2.3 · 001控制端 + 001/002 worker。

## 结果

已实现受控入口暂停、发送前竞态保护、持久同步活动核对及真实恢复探针。**98项回归通过、1项跳过；临时两节点OCR实机验收通过。** 原服务正常，测试资源已清理。

W6整体仍在进行：原GPUStack/Playground/worker推理旁路尚未封锁，未进行完整节点停止与GPUStack维护恢复演练。本轮不执行自动关机或系统切换，也未将任务服务常驻发布。

## 已交付功能

| 功能 | 实现与验证 |
|---|---|
| 受控暂停 | 领取、发送前登记和同步准入共享维护行锁；已领取但未发送的OCR退回队列，保留DEFERRED记录，不消耗执行重试预算 |
| 同步入口 | 新增POST /v1/chat/completions，逻辑模型名、登记key、原生模型ACL、版本预检；当前只支持OCR及stream=false |
| 活动追踪 | 同步请求发送前持久登记；不确定完成保留UNKNOWN，异常退出的过期活动也转UNKNOWN；阻止同步及异步派发 |
| 排空核对 | 私有drain-status列出活动任务、异步后台活动和同步活动；排空只覆盖受控请求，不等于GPU空闲 |
| 验证恢复 | resume要求全部网络模型真实探针；失败保持暂停；维护generation变化拒绝旧探针结果 |
| 节点观察 | 只读检查Docker worker、推理/GPU容器和NVIDIA计算进程，读取失败不当作空闲；不执行停止或关机 |
| 运维记录 | 维护事件审计、同步UNKNOWN人工解除、受限Nginx入口模板与操作手册 |

## 实机验收

临时任务库、API和用户/key使用既有P2试点模型，两个真实worker保持在线；未新增worker、Docker daemon或模型，未修改原入口或防火墙。

1. 受控同步OCR基线HTTP200、46行；异步基线任务完成。
2. 暂停后受控同步请求HTTP503 / MAINTENANCE_PAUSED；新异步任务留在QUEUED，没有领取执行。
3. 暂停期间历史结果仍HTTP200、46行；状态为managed_requests_drained=true、safe_to_poweroff=false。
4. 不提供真实探针的resume被拒绝，仍保持暂停。
5. 指定登记凭据与图片后，真实恢复探针返回46行，才解除暂停；原排队任务随后成功，attempts=1。
6. 两个成功异步任务对应两条成功计量；同步基线与恢复探针各一条COMPLETED活动；维护审计为pause → verified-resume。

节点只读检查发现：001/002各有原worker、模型/GPU容器和两个GPU计算进程。两节点均正确报告backend_stop_observed=false、safe_to_poweroff=false。没有把“请求排空”误报为“进程已经停止”。

## 回归与收尾

回归98 passed、1 skipped，10.72秒。覆盖发送前暂停/退回、旧token、取消、未知活动隔离、同步容量/权限、上游异常状态/超限/压缩错误、进程异常退出、恢复探针失败、维护generation竞态和节点观察。跳过项为独立宿主机编排的数据库实际停启专项；本轮未重复，历史P2已有通过记录。另有一条Starlette依赖弃用警告。

临时API、身份/key、独立验收库、artifact及私密配置已清理，测试数据库容器已停止。原rapidocr-gpu与保留的maas-ocr-p2-r2四个实例逐一请求均HTTP200、46行；两worker ready，常驻collector正常。

## 当前边界与下一步

所有safe_to_poweroff固定为false。节点工具仅观察Docker和GPU计算进程，不能证明非Docker worker已停、控制面不会重建模型或外部旁路已关闭；人工UNKNOWN解除仍依赖运维人员实际停止证据。

当前同步入口不支持SSE，也不是全部GPUStack协议的通用代理；现有Playground保留原运维入口。应用不自动重发，但GPUStack/Higress可能透明重试，不能承诺GPU只执行一次。同步容量独立于异步容量计数，尚未实现跨入口统一项目配额；该项属于W7。

Nginx文件是未上线模板，未做实际Nginx运行验收。下一批W6落实统一入口的实际旁路策略、GPUStack maintenance/副本停止与恢复流程，并完成一次完整演练；再进入W7限额/TTL/指标与W8发布、备份和8小时耐久。

## 材料

- [实机验收](acceptance.json)、[回归日志](regression.log)、[JUnit记录](regression.xml)
- [001节点观察](inspection-001.json)、[002节点观察](inspection-002.json)、[原服务复核](final-verification.json)、[资源清理](cleanup.json)
- [维护操作手册](../../docs/framework/P3-MAINTENANCE.md)、[路线图](../../docs/framework/ROADMAP.html)
- [实机验收脚本](../../scripts/maas/runtime/test_maintenance.py)、[只读节点工具](../../scripts/maas/runtime/inspect_stop.py)
