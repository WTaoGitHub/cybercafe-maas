# E3原生网关503调试分析

2026-10-29。**已确定故障链路层级；当前服务可用并已恢复MaaS接单，未复现原503，底层触发原因仍未证实，不能标记为已修复。E3未自动续跑。**

## 1. 原始503实际发生在哪里

原请求北京时间2026-10-28 20:23:38.398开始，耗时3023ms后503；request_id=b3ab8c65-7d57-4f33-9595-b6183148824b。读取保留的Higress访问日志（access.log.1），取得：

| 字段 | 实际值 |
|---|---|
| route_name | ai-route-route-17.internal |
| upstream_cluster | outbound\|80\|\|model-18-150.static |
| upstream_host | 10.11.31.3:10150 |
| response_flags | UF |
| response_code_details | upstream_reset_before_response_started{local_connection_failure} |
| upstream_service_time | - |

UF表示上游连接失败，详见[Envoy官方定义](https://www.envoyproxy.io/docs/envoy/v1.35.5/configuration/observability/access_log/usage)。不是route_not_found/no_route标记，也不是模型返回的业务错误。**该次最终转发尝试选中了新实例150，并在网关→worker8代理的连接阶段失败。** 不是直接连接OCR端口40014。单条最终访问日志不足以证明原请求所有内部尝试绝对没有执行，因此没有重放原请求。

v2.2.3源码和实际Envoy端点共同确认当前调用路径：MaaS或本次原生测试客户端→GPUStack/Higress→worker代理10150→模型实例端口。worker代理根据X-GPUStack-Model-Instance查端口再转发。当前Envoy四个model18 cluster指向worker7/8的10150；其health_checks未配置主动检查，HEALTHY是发现状态，不能视为一次真实端到端连接验证。Envoy实例150 host计数保留cx_connect_fail=1/rq_error=1，进一步印证连接层失败。

## 2. 历史时间线和排除边界

- 20:23:30左右worker创建147–150；旧实例145/146等在清理，出现日志线程2秒未退出警告，不等于worker进程重启。
- 20:23:36.749 Higress已生成model-18-150.static ServiceEntry。
- 20:23:38.386附近正在推送CDS/EDS/RDS；20:23:38.398请求开始。时间接近，**支持关注配置切换窗口，但仅凭先后顺序不能证明它就是根因**。
- 故障前约10秒，同一worker8代理10.11.31.3:10150曾成功承接旧实例146的请求；失败最终选择的是新实例150。因此不能简单归因于固定地址写错、长期防火墙阻断或一直使用旧实例地址。
- 两worker容器ID/启动时间保持，RestartCount=0，VM boot ID未变；相关内核窗口未见OOM/NVIDIA Xid/网卡重置记录。但日志缺失或未记录的瞬时网络问题不能据此排除。
- 历史单个模型的完整启动日志未全部保留，且没有原失败时TCP抓包，不能追认当时究竟是连接拒绝、丢包、队列/连接池或其他瞬时条件。

原始日志备份仅在Mac私有目录`.maas/backups/e3-debug-20261029/`；公开证据只导出必要字段，不含API key、kubeconfig、请求图片、OCR文本或完整网关配置。历史相关日志保留在access-window.json和routing-timeline.json。

## 3. 当前分层验证

先保持MaaS paused=true/generation43，未改部署，逐实例执行一次：直连OCR请求、worker代理转发健康检查、worker代理OCR请求；随后发4个新网关请求。共16项检查全部通过，其中12次OCR均返回46行，实际覆盖两张GPU。每个测试具有新UUID，任何异常都会停止，不对原失败业务请求重试。

当前原150实例的直连、代理调用均正常；网关4个请求实际覆盖原148/149/150（含昨日失败的150），访问日志为200/via_upstream；原147的直连和worker代理测试通过。不声称4次网关调用一定覆盖全部副本。原503是过去的连接失败，不是当前持续可复现的错误。

## 4. 一次受控重建及TCP元数据

在已暂停的MaaS维护锁下执行**一次4→2→0→4**，只修改model18副本数，不改镜像、权重、claim或worker选择器，不重启worker、VM或Docker。每次观察到目标实例全部running即发一个新请求，之后3个新网关请求，5次均成功。新实例151–154、最终4副本/原配置哈希匹配。

通过001物理接口短时记录连接元数据，不保存包载荷。收到111条SYN/FIN/RST等元数据，30个SYN均观察到对应SYN-ACK；另有5个RST；它们可能包含副本切换时的已有连接回收，未证明其底层原因，不能仅因存在RST就判为原故障复现。**本次未捕获原故障的同类503或未完成握手**，不能用这份新抓包替代昨天的缺失证据。

网关会为跟踪采样修改UUID中的trace位：其中一次客户端ID2c4aa7b1-f38c-46df...在访问日志为2c4aa7b1-f38c-96df...，其时间、路径和其余UUID位对应且HTTP200。记录在unmatched-access.json；不能把请求头ID当不可变的业务幂等键。

## 5. 恢复与当前状态

在确认4副本正常、配置匹配且无受控UNKNOWN后，执行一次已有正式MaaS恢复探针。ocr.default / ocr-registry-r1识别46行，通过后原子恢复接单，generation43→44。最终paused=false，UNKNOWN=0，monitor ok=true/alerts=[]。恢复探针不计入成功业务计量；没有替用户重放业务任务。

4个候选executor及全局/project额度4保持原E3暂停前配置，未在本轮重新选型。E3矩阵仍为中断状态，未自动重跑dual4/dual8或压测。调试工具只新增宿主脚本，不替换GPUStack、API或执行器镜像；未改防火墙、注册凭据、路由策略或后端重试配置。

## 6. 结论与下一步

**已确认**：原失败的最终转发连接发生在Higress/Envoy→worker8代理，选中的新模型实例正确，MaaS未参与该原生调用；当前模型、worker代理、网关链路均可工作。

**尚未确认**：连接失败是瞬时网络、代理连接可接纳性，还是配置推送/新连接池切换时序；一次新重建未复现，不认定根因已修复，不通过加固定sleep或推理自动重试掩盖。

建议后续受控E3复测沿用新请求ID及短时TCP元数据，补采当次Envoy连接失败计数和两端网络/连接池证据，遇503即停并保留服务端响应头/错误体摘要。若要反馈上游，可提交脱敏访问记录、明确实例/代理地址、配置推送时间及最小复现步骤。无需恢复collector，也不为每次MaaS推理增加SSH预检。

证据：summary.json、layers.json、probe-access.json、recreate.json、tcp-summary.json、recovery.json、final.json。2项TCP元数据解析/不记录凭据单元测试通过；真实调试检查及复现结果如上，不等于全套E2E或E3最终验收。

## 问题跟踪

已登记[GW-20261028-001](../../docs/issues/GW-20261028-001.md)，状态OPEN：服务恢复但根因未确认，不能以未复现关闭。[日志归档与校验清单](log-manifest.json)包含脱敏证据及Mac私有原始备份的大小、SHA256和位置。
