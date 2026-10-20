# P3 受控入口维护与恢复

2026-10-20 · 首批实现 · [验收报告](../../results/maas-p3-maintenance/REPORT.html)。

## 已实现的边界

首批实现任务派发门禁、受控非流式同步OCR入口、持久活动核对、恢复探针与维护审计。任务API/执行器仍未常驻发布，临时服务与身份已清理。首批验收时尚未部署网络旁路策略；后续进展如下，W6整体仍未完成全局停机演练。

后续更新：已部署私有Nginx统一入口，并对两节点指定端口完成运行期网络客户端隔离及实测。运维回环/可信节点与原H3隧道仍在边界之外，完整节点停止演练未完成；当前状态以 [P3网络边界说明](P3-BOUNDARY.md) 为准。

- `pause` 与领取、实际发送前登记、同步准入使用同一PostgreSQL维护行锁。已经登记的请求视为在途，暂停不强杀它；尚未登记的已领取OCR任务退回队列。
- 退回记录保留为DEFERRED；attempt编号继续递增以防旧token提交，DEFERRED不消耗真实执行重试额度。
- `drain-status` 核对RUNNING/CANCEL_REQUESTED任务、DISPATCHING/UNKNOWN后台活动和同步活动。只有暂停且三类均空时，`managed_requests_drained=true`；排队任务可以保留。
- 同步入口 `POST /v1/chat/completions` 使用已登记key、逻辑模型名（如`ocr.default`）、`stream=false`，复用原生模型ACL、固定目标和新鲜预检。仅支持当前OCR connector；不开放管理/Playground/generic代理，不支持SSE。
- 同步发送前持久登记sync_activity；超时、连接中断、响应超限、不确定上游状态保留UNKNOWN；API进程崩溃留下的过期DISPATCHING在下一次准入/核对时变为UNKNOWN。未知活动同时阻止同步与异步新派发。
- 完整收到可解释的成功/输入拒绝响应后完成活动记录。应用层不自动重发；GPUStack/Higress透明重试边界沿用P2，不能承诺端到端只执行一次。
- `resume` 要求当前暂停且受控请求排空，并为全部已登记网络模型执行受信任预检和真实推理探针。失败保持暂停；维护generation变化时拒绝旧探针结果。当前只实现OCR恢复探针，其他网络connector将拒绝恢复直到补齐探针。
- `maintenance_events`记录暂停及验证恢复。同步活动不是商业计费账本；未新增同步计费承诺。

## 私有操作流程

以下命令在具有任务服务私密环境与数据库连接的运维进程内执行；不是公网API。容器部署时路径指容器内路径，探针图片需要只读挂载。探针凭据应是已登记且允许全部待恢复网络模型的专用inference key，命令只传credential ID。

```sh
python -m maas_platform.cli pause --reason 'planned node maintenance'
python -m maas_platform.cli drain-status
# 保持暂停，等待在途请求完成；有UNKNOWN先核对实际后端，不能按超时自动释放。
# 完成节点维护并恢复原模型配置/实例/路由后：
python -m maas_platform.cli resume --credential-id pilot --probe-image /probe.png
python -m maas_platform.cli status
```

任务提交/查询/取消和结果下载不因暂停而关闭；新任务可以有界排队，但原有鉴权和deadline仍生效。`resume`不带探针参数时，只要目录有网络模型就会拒绝，避免未验证恢复。

## 从排空到停止节点还缺什么

`managed_requests_drained` **只覆盖受控入口**，所有状态报告中的 `safe_to_poweroff` 目前固定为false。本工具不会执行关机、切系统或GPUStack maintenance操作。

真正停节点还须按以下顺序执行并保留证据：

1. 从统一外部入口关闭GPUStack直连、Playground和worker推理端口旁路，仅保留运维网络；核对已有长连接及仍可能发送请求的内部客户端。
2. 暂停受控派发并等受控在途结束；UNKNOWN不等于已停止，不能凭key撤销或worker失联解除。
3. 防止GPUStack重新拉起目标模型，停止节点上对应推理进程；同时核对控制面期望副本、调度策略与maintenance状态。单独停止worker既不销毁模型，也不阻止已有容器接受请求。
4. 在每个待维护节点只读检查：

```sh
python3 scripts/maas/runtime/inspect_stop.py
```

工具报告仍在运行的Docker worker、模型run容器、GPU设备容器及NVIDIA计算进程；包括暂停的推理容器。读取失败即报错，不会将无法读取解释为空闲。`backend_stop_observed`仅表示此观察范围内未见阻塞项；非Docker worker、外部入口与防止重新拉起的控制状态仍需核对。它不停止容器，不释放UNKNOWN，不授权关机。

5. 只有实际停止且不能重新拉起的证据成立后，才可私有执行 `resolve-backend --token ... --reason ...` 或 `resolve-sync --token ... --reason ...`。reason是运维人员的证据说明，当前不自动验证说明真实性。不要解除仍在途/仍可能执行的活动。
6. 恢复节点、模型、路由和collector，执行带真实探针的resume。001兼任控制端，不能随着普通worker切系统。

这些节点级停止、控制面维护与旁路关闭步骤尚未做成自动执行工具，不能把本批代码当作完整安全关机程序。

## 入口落地与下一步

[旧Nginx示例](../../deploy/maas/edge.nginx.conf.example)已由 [实际配置](../../deploy/maas/edge.nginx.conf)取代；私有入口已上线并通过真实OCR验收，另有网络策略覆盖宿主机及Docker转发。TLS、规则启动持久性和已有运维/独立服务请求仍需后续验证。只配置Nginx本身不会封锁旧GPUStack端口。

W6下一批：落实实际统一入口和旁路策略，核对GPUStack v2.2.3 maintenance/副本恢复行为，演练一次完整停止与恢复；W7再完善项目配额、TTL/孤儿清理与指标，W8负责常驻发布、HTTPS、备份及8小时耐久。
