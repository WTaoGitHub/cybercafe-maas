# 网关503后续：修复控制端误丢弃出站返回包

2026-10-29。结论：**已确认并修复一处本项目防火墙缺陷；原始单次503的确切源端口没有历史证据，Issue保持开放观察。** 当前MaaS已恢复接单，generation46、UNKNOWN=0；四个模型副本、四个候选执行器保持。未继续E3容量矩阵。

## 发现及对照证据

控制端原来的MAAS-P4-EARLY运行于raw/PREROUTING，按目的端口阻断受保护服务。受保护端口含40000–40063、41000–41999，与控制端32768–60999的出站临时端口范围重叠。前者合计1064个端口落入临时范围；这仅是范围重叠，不是故障概率估计。

worker7在旧固定受信集合内；worker8只被允许访问控制端的原生入口，并不在“所有保护端口都受信”的旧集合中。控制端向worker8代理建立出站连接时，若选择重叠的本地临时端口，worker8返回的SYN-ACK会被误认为访问控制端受保护服务。该包在连接跟踪之前即被DROP。

本轮未发送OCR的实际TCP对照：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 控制端→worker8，普通临时端口 | 立即连通 | 立即连通 |
| 控制端→worker8，受保护范围内临时端口 | 约1.5秒超时，控制端DROP计数增加3个包 | 约0.12毫秒连通 |
| 控制端→worker7，重叠临时端口 | 连通，受旧信任列表保护 | 连通 |
| 实际网关容器网络→worker8，两个重叠范围 | 未在旧规则下重复本项 | 两项均立即连通 |

初次对照复用了刚关闭的源端口，后两项bind返回EADDRINUSE；它们不是连接失败证据。已换独立空闲端口取得有效对照，初始记录另存[tcp-probe-initial.json](tcp-probe-initial.json)，有效结果为[修复前](tcp-probe-before.json)与[修复后](tcp-probe-after.json)。

## 修复

- 将控制端的早期过滤迁移到**mangle/PREROUTING**，位于conntrack之后、Docker DNAT之前。
- 在早期过滤、INPUT和Docker转发边界只允许`--ctstate ESTABLISHED --ctdir REPLY`返回，不增加整个网段/worker的全端口信任，不使用不分方向的ESTABLISHED放行。
- 新mangle规则及首位hook确认生效后，才解除本项目旧raw hook；保留无关规则与原入站限制。替换安装失败时不解除旧hook。
- `apply_raw`函数名为既有入口工具兼容保留，实际表由TABLE=mangle决定；状态输出改为early_table/early_hooks_first。容器入口工具继续复用它。
- 静态允许来源、原生入口、Docker桥例外、TLS、鉴权及GPUStack重试策略均未扩大或修改。没有周期SSH采集、collector或额外推理重试。

[Netfilter官方hook顺序](https://wiki.nftables.org/wiki-nftables/index.php/Netfilter_hooks)：raw=-300、conntrack=-200、mangle=-150、dstnat=-100，因此不能在原raw阶段直接依赖本次包的连接跟踪方向。

## 验证

| 验证 | 结果与范围 |
|---|---|
| 本地单元回归 | 26通过：规则方向、迁移失败保护、容器入口、TCP元数据、复现次数/路径上限、部署配置并发保护 |
| 三个独立网络namespace | 15项通过：旧规则IPv4/IPv6重现；新规则回复成功；未授权主机/IPv6/DNAT/容器直连、伪装源服务端口及已有入站连接仍阻断；SSH端口保留 |
| 实际控制端隔离 | 7个未授权目标均不可达，SSH可达；临时资源已移除，防火墙规则未被探针改动 |
| 三轮4→2→0→4 | 15次全新原生网关OCR均200/46行；每轮目标running后立即请求，不加固定等待掩盖问题 |
| 网关日志关联 | 15条请求匹配，均200；本轮窗口模型18未发现新5xx，实际请求覆盖两张GPU |
| 正式恢复 | 注册OCR探针46行通过，generation45→46恢复接单 |
| 一笔异步任务 | SUCCEEDED、attempts1、结果46行、task_usage_events恰好1条；TLS校验开启 |
| 身份及运行状态 | 两worker的boot ID和worker容器ID保持；控制端原容器ID/StartedAt、VM boot、Docker PID保持；model配置哈希与原值相同 |
| 最终状态 | 模型4副本、双节点各2；UNKNOWN=0、监控ok/alerts为空；4个候选executor仍在，未形成E3最终选型 |

TCP只保存313条连接头元数据，无图像、Bearer或其他载荷。异步SQL核对初次误用了不存在的表名；修正为task_usage_events后仅GET/SQL核对同一已成功任务，**没有重提任务**。两个隔离探针的初期“规则未变”比较把iptables-save时间戳/计数器或新加载空表当成变更，断言修正后重新核验通过；这些属于验证工具问题，不是新的推理失败。

## 发布与回退边界

改动限控制端的boundary_policy.py、boundary.py、persist_boundary.py及诊断工具。先将原文件、策略/状态和防火墙快照备份到Mac私有目录并校验SHA256；控制端也保留本次三文件回退副本。备份/完整规则不入Git。

进入全局维护锁后暂停MaaS，确认无活动/UNKNOWN；停止边界刷新timer并等待既有刷新结束，在边界锁内原子更新文件，再运行refresh/status。新hook检查通过后恢复timer；只有全部诊断和正式业务探针成功才恢复接单。部署窗口没有重启VM、Docker、GPUStack Server、worker或MaaS容器；三轮只由GPUStack原生缩扩OCR推理实例。

如需紧急回退，应先暂停排空、持维护锁并停止刷新timer，按私有三文件备份恢复源码；先安装/确认旧raw边界生效，再解除本项目新mangle hook，执行旧refresh/status并恢复timer。禁止全表盲目恢复、删除数据库/结果、解除UNKNOWN或重启设备。旧规则会重新引入已知误拦截，只作为经过核对的紧急回退材料。完整流程须由运维在批准窗口执行，不由失败脚本盲目触发。

母版与两克隆的网络文件**本轮未修改**。其raw式同类规则需要在下一次母版网络更新中评估同样的返回方向问题，并补管理员新快照/独立克隆首启验收；不能把控制端已修复解释为所有设备已升级。

## 对原问题及后续的判断

- 已证明本项目规则能够造成“网关/控制端到worker8的连接偶发失败”，具有确定触发方式，并已有对应修复和内核/实机对照。
- **没有原始503请求的源端口或TCP抓包**；其3023ms与本轮1.5秒探针超时也不是等同的测量条件。因此不能追溯证明原始请求必然命中该DROP。原有xDS推送时间接近的事实仍不构成另一根因的证明。
- 副本重建会创建新上游连接，从而暴露临时端口问题，这是与触发场景相符的解释，尚不是对原始单次连接的直接取证。
- [GW-20261028-001](../../docs/issues/GW-20261028-001.md)保持OPEN，状态为“可复现边界缺陷已部署修复，原始503继续观察”。三轮诊断回归不是8小时稳定性或容量/SLA验收。
- 下一步可在核对环境后另行安排E3剩余矩阵；本轮不自动继续E3、不升级GPUStack、不修改claim，不把任务登记视为部署授权。

证据：[summary](summary.json)、[内核回归](kernel-regression.json)、[隔离回归](ingress-regression.json)、[部署](rollout.json)、[重建](recreate.json)、[关联日志](probe-access.json)、[恢复](recovery.json)、[异步](async-regression.json)、[最终状态](final.json)、[文件SHA256](manifest.json)。
