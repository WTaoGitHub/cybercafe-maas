# 实施计划 v0.2

> **2026-10-27最新执行顺序已获用户确认**：E5多克隆收尾 → 当前拓扑维护/网络边界 → 上下游职责及服务认证契约 → 下游实际联调 → E3并发 → E8/E7。用户已将OCR目标副本设为14，目前7 running/7 pending，等待管理员新增独立克隆；自动补齐取决于新节点容量和接入结果，尚未验收14 running。详见[当前执行与扩容记录](NEXT-STEPS-20261027.md)。

> **E5当前执行基线（2026-10-23修订六）**：共享码直接读取批次统一配置→本机boot/manual应用→GPUStack原生注册。无E5数据库、VM登记、独立会话凭据或GPU UUID关联。后续日期记录若有更早会话/登记设想，仅为历史。见 [当前设计](E5-DESIGN.md)、[开发计划](E5-IMPLEMENTATION.md)和 [部署测试](E5-TEST-DEPLOYMENT.md)。

2026-10-20 · 当前执行计划；P0已通过受控试点验收，P1本地适配器及故障窗口已验收（[收尾报告](../../results/maas-p1/REPORT.md)），详见 [P0报告](../../results/maas-p0/REPORT.md)及 [首批实现报告](../../results/maas-p0-p1/REPORT.md)。依据：[DESIGN.md](DESIGN.md)。

## 1. 交付策略

一个仓库、三个新增运行单元：任务API、执行器、PostgreSQL；结果服务以API+ArtifactStore模块和控制端持久卷实现。GPUStack固定v2.2.3，复用身份、模型目录、路由、推理和部署；不拆独立用户服务、目录服务、结果微服务或GPU调度服务。

先验证接入与鉴权，再用模拟后端建设持久任务闭环，随后接既有OCR并验证故障。真实模型质量优化不在此关键路径。每阶段先满足门槛再推进，不把静态源码能力当作现网验收结果。

## 2. 工作包

| 工作包 | 任务分类 | 具体任务 | 交付物 | 优先级 |
|---|---|---|---|---|
| W0 版本与接入 | 现网核对 | 镜像digest/依赖、入口模式、generic代理、usage、retry/fallback | 实测能力矩阵与边界记录 | P0 |
| W1 身份与协议 | 鉴权/接口 | 已登记GPUStack key映射、原生权限验证、查询授权、能力schema、API/OpenAPI和错误 | AuthConnector探针、协议草案、安全反例 | P0 |
| W2 存储与数据 | 持久化 | PostgreSQL迁移、任务/attempt/幂等、artifact元数据、控制端卷、secret引用 | 可恢复数据结构与迁移 | P0 |
| W3 持久闭环 | API/执行 | 上传→提交202→领取→模拟推理→原子结果→查询；租约心跳和取消 | 单机可运行最小闭环 | P0 |
| W4 既有模型接入 | connector/适配 | OCR调用GPUStack、版本化目标、配置指纹、错误/结果转换、关联ID | OCR和模拟后端同流程 | P0 |
| W5 故障恢复 | 可靠性 | 重启、失联、旧attempt、有限重试、过载、未知后端执行和结果提交竞态 | 自动化故障测试及报告 | P0 |
| W6 受控维护 | 运维 | 全局暂停、同步旁路限制、在途核对、maintenance、恢复探测 | 可执行维护/恢复手册及记录 | P0 |
| W7 可运营边界 | 配额/监控 | 有界队列、project并发、存储TTL、任务计量、指标告警、日志脱敏 | 看板、告警、配额与清理验证 | P1 |
| W8 交付与耐久 | 部署/验收 | Compose、secret注入、备份恢复、8小时测试；具备条件时双系统切换 | 发布包、回退步骤、验收报告 | P1 |

## 3. 技术与代码组织

建议Python/FastAPI + PostgreSQL；SQLAlchemy/迁移工具、异步HTTP客户端等精确依赖在P0后锁定，不沿用GPUStack内部ORM作为公共SDK。文件存储先本地卷，接口可替换为对象存储。

```text
maas_platform/
  api/                     # /maas/v1 与私有维护API
  auth/                    # 已登记key绑定、GPUStack认证/授权connector
  domain/                  # 状态、错误、幂等、取消和重试策略
  persistence/             # 独立数据库与迁移
  execution/               # 领取、心跳、执行、协调循环
  connectors/gpustack223/   # HTTP交互、版本指纹、兼容细节
  artifacts/               # 原子提交、下载授权、引用与TTL
  metering/                # 唯一成功事件与attempt统计
model_contracts/           # 能力/schema/固定版本目标配置
adapters/mock/             # 延迟、故障、连接中断、取消模拟
adapters/ocr/              # 对现有OCR的薄封装，不重写方向算法
deploy/maas/               # Compose、入口路由、配置、备份/回退脚本
tests/maas/                # 状态机、事务、鉴权、恢复与端到端
```

以上为拟建结构，现有gpu-ocr和GPUStack部署脚本保持。避免使用Python包名platform遮蔽标准库。

## 4. 实施顺序与环境

1. 本地完成协议、迁移、模拟后端和故障注入；不依赖本地入站访问。
2. 在001增量部署独立任务服务与数据库/卷，用私有入口测试；不修改GPUStack数据库，不替换现有同步OCR路由。
3. 绑定已登记试点key，接入GPUStack两worker；先验证权限、模型版本固定、JSON/SSE/generic路径和统计实际行为。
4. 控制维护窗口内做重启/断连、全局排空及恢复，持续记录请求与任务差异。
5. 验收通过后再开放外部客户入口；HTTPS、路径白名单及多project隔离未通过前仅受控内测。

本计划不直接触发任何维护、数据库部署、长时负载或系统切换；实施需另行执行用户授权任务。

## 5. 测试与验收矩阵

| 类别 | 用例 | 通过条件 |
|---|---|---|
| 身份 | 未登记/过期/撤销key、伪造身份头、跨project访问 | 拒绝越权，不要求客户management key，不只做本地指纹检查 |
| 模型授权 | 限定模型key、别名变化、执行前撤销 | 不越权；不静默改模型revision |
| 幂等 | 并发重复提交、相同key不同body、换project | 同project同请求一个任务；不同body409；project互不混淆 |
| 持久性 | 返回202后重启API/执行器 | 记录仍在，恢复执行或明确失败，无永久悬挂 |
| 尝试竞态 | 旧attempt晚返回、取消与成功并发 | 唯一终态/成功事件；过期提交被拒 |
| 故障 | 002失联、503、deadline、OOM、网关多次重试 | 有限预算，分类可解释，未知后端活动不被误判为空闲 |
| 结果 | 写文件后数据库失败、数据库成功前崩溃、磁盘满 | 不伪报成功；孤儿文件可回收；已成功结果可读 |
| 保留期 | 活动输入引用、过期结果、跨project下载 | 不删活动输入；到期410；不能跨project读 |
| 版本 | 版本目标改绑、旧镜像不可用 | 明确报错/排队策略，不冒充原revision |
| 维护 | 全局暂停与领取竞态、旁路请求、旧实例仍RUNNING | 停止派发，未确认后端结束不允许自动关机；恢复前实测路由可用 |
| 容量 | project洪泛、队列满、磁盘不足 | 有界拒绝/等待；无无限排队或无限重试 |
| 恢复 | 数据库与文件备份还原 | 记录和artifact可核对，报告实际RPO/RTO |
| 耐久 | 至少8小时任务负载 | 无无法解释的任务遗失/重复成功计量，报告错误率、P95和积压 |

所有性能阈值在P0依据实际输入、资源和试点要求确定；不能把历史OCR四副本吞吐直接作为任务平台SLA。

## 6. 发布、回退与完成定义

新增服务使用独立端口和路径；先备份并校验持久卷权限。数据库迁移优先采用向前兼容方式；回退时停止新任务接收、保留任务数据，再退应用版本，不自动删库/卷。原GPUStack同步调用路径保留，任务API故障不能靠丢弃任务假装回退成功。

每阶段产出代码、必要测试、配置示例和可重现记录。最终交付包含当前架构、实际组件清单、限制、运维与恢复手册。未通过身份桥接/跨project验证时不得开放多租户；未通过未知后端活动处理时不得承诺自动安全切系统。

## 2026-10-20 P2首批进展

固定版本OCR connector、两节点同内容镜像、受信任预检、未知执行隔离与人工解除已实现并实机验证。64项回归通过，数据库中断专项1项通过。详见 [P2报告](../../results/maas-p2/REPORT.html)与 [接入说明](P2-OCR.md)。

W4/W5最新进展：常驻collector、专用只读清单通道、权限/配置漂移及容器重建已验证（82项回归通过）。后续用户明确允许中断既有002服务，已完成空闲停止、在途强停、保留SSH的节点间网络隔离及恢复；P2按该范围通过，整机断电/双系统切换未验证。本轮回归82通过、1项跳过（数据库实际停启专项未重复）。当前未常驻发布任务端点。见 [离线报告](../../results/maas-worker-offline/REPORT.html)。下一步W6全局维护及同步旁路控制，再做W7运营限制和W8发布耐久。

## 2026-10-20 P3/W6首批进展

受控暂停与发送前竞态保护、持久同步准入、活动排空核对、真实恢复探针、维护generation及审计、只读节点停止检查已实现，98项回归通过，临时两节点OCR实机验收通过。见 [报告](../../results/maas-p3-maintenance/REPORT.html)与 [操作手册](P3-MAINTENANCE.md)。W6仍未整体完成：原GPUStack/Playground/worker旁路未封锁，Nginx模板未上线，未实施完整GPUStack maintenance/副本停止恢复演练；没有自动关机或切系统。任务API/执行器仍未常驻发布。

## 2026-10-20 P3/W6网络边界进展

私有Nginx已上线；两节点IPv4/IPv6宿主机、Docker DNAT及容器目标规则已应用，隔离客户端实测通过，经过Nginx的真实OCR维护闭环通过。当前仅运行期规则，运维回环/可信节点及独立H3隧道仍有明确边界；W6剩余GPUStack maintenance/副本控制与完整节点维护演练，P4补规则持久性、启动窗口及公网TLS。见 [报告](../../results/maas-p3-boundary/REPORT.html)。

## 2026-10-20 P3/W6进程级维护验收

后续已停用H3及隧道，并完成GPUStack maintenance、全局副本归零、002原worker及GPU进程停止/恢复演练。模型原配置、maintenance、worker容器身份和副本分布均恢复，真实探针后恢复派发；113项回归通过。W6按可信两节点的受控进程级维护范围验收，未关闭VM或切系统，未承诺无损逐节点drain。见 [报告](../../results/maas-p3-node/REPORT.html)。下一批W7运营限制；P4常驻发布、网络启动持久性、HTTPS、备份与8小时耐久仍待完成。

## 2026-10-20 P3/W7运营边界验收

同步/异步共享全局及project并发，存储字节配额、TTL与安全孤儿回收、同步唯一成功事件和探针排除、私有指标/本地告警观察进程、执行器日志脱敏均实现。125项回归通过、1项跳过；经私有Nginx的真实OCR及清理/计量验证通过。P3按可信两节点共享卷与受控入口范围通过。见 [运营报告](../../results/maas-p3-operations/REPORT.html)及 [操作手册](P3-OPERATIONS.md)。P4先补Compose运行环境与常驻发布，再做网络启动持久性/HTTPS、独立监控、备份恢复和8小时耐久；本轮未部署常驻任务API、执行器或observer。

## 2026-10-21 P4首批：前置项1–3完成

真实OCR正式目录、独立受限推理身份/私有secret/持久卷已配置；collector增加无完整后台配置的只读精简证明投影；Compose2.40.3已安装，API/执行器/observer/独立PostgreSQL在001常驻。实际UID10001镜像回归126通过、1跳过，collector最终权限专项19通过。真实同步/异步OCR、证明刷新/不可写、四服务容器重启后的任务/结果/暂停状态保留及探针恢复通过。未重启主机或Docker daemon，未改变推理防火墙。见 [报告](../../results/maas-p4-bootstrap/REPORT.html)及 [手册](P4-LIVE.md)。P4继续正式维护配置、网络启动持久性/HTTPS、独立监控、备份恢复及8小时耐久，不将常驻启动成功等同于阶段整体验收。

## 后续增强 E1：逐 worker 排空、其他节点继续服务

已按用户要求登记，排在P4完成之后，不扩大当前全局维护实现的承诺。先验证单节点停止接收新请求、节点级在途/UNKNOWN核对及剩余节点容量，再实现单节点停止和探针恢复。范围增加突发故障的最小范围隔离、按模型健康子集预检、剩余容量准入及UNKNOWN局部隔离；新增多模型并发故障验收，要求健康模型继续服务。详见 [增强计划](ENHANCEMENTS.md)。

## 后续增强 E2：面向用户的批处理服务

已登记，安排在P4之后，与E1基本独立。首版建议最多100张PNG/JPEG：多选上传、批次进度、独立子任务、有界公平接纳、失败项重试和结果ZIP下载。优先补齐批次持久协议及输入/结果保留，再实现页面和有界结果打包；不绕过现有配额、UNKNOWN隔离和项目授权。任务与验收详见 [增强计划E2](ENHANCEMENTS.md#e2-面向用户的批处理服务)。

## 后续增强 E3：多执行器并发与容量验证

已登记，安排在P4之后。优先在001部署两个独立执行器，每进程仍一次执行一个任务；共用数据库、artifact卷和一致配置。先验证竞争领取/租约恢复、统一同步异步配额及共享文件清理，再对比并发1/2的吞吐与资源占用；不预设性能收益，不新增GPUStack worker或Docker daemon。与E1容量准入、E2批次公平接纳衔接，详见 [增强计划E3](ENHANCEMENTS.md#e3-多执行器并发与容量验证)。

## 2026-10-21 P4：正式维护配置与常驻演练完成

已绑定正式Compose任务CLI、pilot-ocr探针身份、模型1/9与002专用SSH；增加每窗口私有配置/日志、未完成窗口保护、001恢复watchdog及collector新鲜度等待。129项回归通过、1项跳过。直接使用常驻API/执行器/正式任务库和原卷完成begin→stop→recover→resume，窗口60.626秒，STOPPED保持10秒，历史结果可读、排队任务恢复一次成功，探针未计费；002原worker身份/restart policy/Agent恢复，两端timer解除。见 [报告](../../results/maas-p4-maintenance/REPORT.html)和 [正式维护手册](P4-MAINTENANCE.md)。P4后续为文档统一、网络启动持久性/HTTPS、独立监控、备份恢复及8小时耐久；E1–E3不在本轮实现范围。

## 2026-10-21 P4发布短项完成，8小时测试已改为今晚19:00定时启动（北京时间）

用户选择私有TLS、不开放公网。已完成HTTPS回环证书验证、独立监控/有界指标历史与看板、任务DB+artifact+配置加密异机备份与独立恢复、两节点pre-DNAT启动拦截和Docker重启抽样、兼容镜像回退。131项回归通过、1项跳过；短测57/57成功，四客户端一次提交P95约45.06ms。8小时测试已改为今晚19:00定时启动（北京时间）（每5秒、40张固定中英文样本），完成前P4不整体验收；按持续时间、SQL时间连续性、终态及成功事件共同核对。见 [运行手册](P4-RELEASE.md)和 [进展报告](../../results/maas-p4-release/REPORT.html)。未实际整机重启或双系统切换，同机监控及手动备份的生产边界保持明确。

## 2026-10-21 E1本地候选开发

按用户要求提前开展E1设计、代码与本地测试，正式服务器/配置/今晚P4测试保持不变。候选模块独立、未接入P4执行路径；请求路径对接、GPUStack单worker别名实际部署、运维hooks和持续流量实机验收仍待P4通过后推进。见 [E1设计/路线图](E1-DESIGN.md) 与 [本地验证报告](../../results/maas-e1-local/REPORT.md)。不将本地模拟通过标记为E1整体验收。

## 后续增强 E4：同步接口持久幂等

2026-10-21已登记，暂缓实现，当前优先级中低，非P4及首期OCR兼容接入的强制前置。背景为外部消费者body request_id可选/不保证透传，而当前同步没有幂等，超时重发可能重复执行/计量。现阶段优先复用异步任务幂等、明确同步不自动重试并保留UNKNOWN；先验证稳定性、容量/成本和真实接入。若同步必须安全重试/找回结果，或高成本模型及商业对账使收益显著，提升优先级。候选采用PostgreSQL原子占位、project+接口+key、持久状态/结果与唯一成功计量、UNKNOWN不重放及TTL/墓碑；详细背景、收益/成本、任务与验收见 [E4增强计划](ENHANCEMENTS.md#e4同步接口持久幂等与原结果复用)。不改变当前服务。

## 最高优先级后续任务 E5：云网吧设备身份与动态接入

2026-10-21用户明确提升为最高优先级：**P4验收完成后立即开始E5进一步调研、分析与解决，先于E1–E4后续实施**。重点解决临时IP/hostname、无盘还原导致的身份丢失、克隆凭据、稳定设备/GPU映射、启动代次及可信地址更新。先调查真实环境、实测GPUStack v2.2.3注册/重连，再设计并验证最小方案；据结果修订collector、白名单、SSH、维护和E1契约。详见 [E5范围与验收](ENHANCEMENTS.md#e5云网吧设备身份识别与动态节点接入)。P4测试期间不改运行环境。

## 2026-10-21 22:16 夜间耐久失败更新

2026-10-21夜间耐久测试在22:16:38以FAILED结束：2360接纳/2359成功，1个PREFLIGHT_UNAVAILABLE；仅运行约3小时16分钟，P4未通过。collector与002 SSH限流窗口吻合，当前服务已恢复健康，原因仍需验证。保留失败记录，不自动重新计时；先修复并重新安排完整8小时，P4通过前不启动E5。见 [失败分析](../../results/maas-p4-release/ENDURANCE-FAILURE.md) 和 [更新报告](../../results/maas-p4-release/REPORT.html)。

## 2026-10-22 P4修复方向重评：信任GPUStack服务抽象

依据用户明确的信任边界，建议取消依赖每5秒SSH镜像清单的逐请求preflight门禁，复用GPUStack原生资源/实例健康及推理路由；镜像/配置核验转为发布检查与独立审计。保留鉴权、静态模型版本绑定、队列/配额、维护、发送意图、UNKNOWN和唯一计量。需同时迁移同步/异步、配置、恢复及监控依赖，经本地与受控验证后发布，新run ID重新完成P4八小时验收。**本轮仅设计重评，正式配置与代码行为未改变**。见 [职责与实施清单](GPUSTACK-TRUST-BOUNDARY.md)。这调整了原“优先隔离SSH采集通道”的主修复方向，运维SSH治理作为独立事项保留。

## 最新排期：E6退役自建collector、P4复测、E5

2026-10-22用户决定退役自建collector，作为P4架构整改先清点/迁移同步异步、维护恢复、配置/挂载、监控、备份与安装依赖，验证后再停进程和清理独占SSH资产。复用GPUStack原生状态，不重建节点采集平台；保留业务鉴权/任务/UNKNOWN/计量及按需发布核验。新run ID重新完成P4八小时验收后立即启动最高优先级E5。E1–E4不新增实施，E1候选相关证明契约需后续修订。本轮仅登记，未变更服务器。见 [E6背景与任务验收](ENHANCEMENTS.md#e6退役自建collector复用gpustack服务状态)。


## 后续增强 E7：GPUStack千Worker部署策略与规模验证

2026-10-22新增，待调研与验证，不改变E6整改 → P4重新验收 → E5立即启动的顺序。以1000个同时在线Worker为目标，核对v2.2.3官方边界，测量通信/数据库/事件/网关开销，比较独立控制端、外置数据库与网关拆分等部署策略；按100/500/1000分档测试稳态及集中上线/离线恢复。验收前冻结业务阈值，目标不少于24小时稳态，明确模拟与真实GPU证据边界，不预先宣称千节点已获支持；必要时评估多个独立管理域。详见 [E7任务与验收](ENHANCEMENTS.md#e7gpustack支持1000个worker的部署策略与规模验证)。本次仅登记，正式环境不变。


## 2026-10-22 E6上线，P4复测改为今晚20:00

自建collector已停止/禁用/永久mask，推理、安装、维护、备份、监控依赖迁移完成，专用SSH资产清理且私有回退材料保留。候选镜像194项通过、1跳过；真实同步/异步OCR、60.512秒完整维护和2686成功记录/2404文件独立恢复通过。白天新复测按用户要求主动停止，20/20成功。新run e6-night-8h-20261022于2026-10-22 20:00北京时间定时启动，至次日04:00，P4仍未通过。通过最终核对后立即E5；E1–E4及E7未启动新增实施。详见 [E6报告](../../results/maas-e6/REPORT.html)及 [执行记录](E6-RETIREMENT.md)。


## 2026-10-22长期约束：不重启现有两台VM

用户明确XZ-31-001、XZ-31-002现在和以后均不得重启，包括等效的关机再启动/断电/平台重置或重建/系统还原与切系统重启。只读取证、供应商说明或独立获准环境用于补充启动与持久性证据。已核对服务器实际timer/service和endurance.py、verify_endurance.py：今晚20:00至次日04:00只执行OCR负载与结果/SQL核对，没有VM或容器/引擎重启操作，计划时间保持。自动化不得添加此类恢复操作，不能将未验证边界标记通过。


## 2026-10-23 当前验收状态

E6整改后的独立8小时复测已通过：28800.79秒、5760/5760成功、零错误；独立SQL核对任务/终态/成功事件一致，最大提交间隔5.224秒。P4在两节点私有试点范围通过，原失败记录保留。未进行VM或容器/引擎重启，不代表最大吞吐、生产HA或公网验收。完整证据见 [P4报告](../../results/maas-p4-release/REPORT.html)及 [E6记录](E6-RETIREMENT.md)。立即转入最高优先级E5首轮调研设计，其他增强尚不启动。


### E5首轮交付（2026-10-23）

已核对GPUStack v2.2.3注册源码，完成 [GPU资源/临时VM会话设计、能力差距与最小验证计划](E5-DESIGN.md)。母版克隆场景采用HTTP登记+数据库管理身份与凭据，资源状态/调度/路由仍复用GPUStack。当前未实现登记服务或镜像、未完成克隆实测；供应商可信启动注入及GPU UUID稳定性待确认，下一步为信息确认和Mac只读盘点。未更改服务器。E1–E4/E7继续暂缓。


## E5修订二：镜像引导与临时会话（2026-10-23，当前执行基线）

依据用户接受的cybercafe源码评审，保留image-prep、systemd首启、GPU UUID读取及HTTP领配置机制，重写旧marker跳过登记、同指纹复用密钥、共享批次认证和文件KV逻辑。最新任务顺序为E5.0设计完成 → E5.1原生能力/本地盘点及供应商确认 → E5.2登记服务/数据库 → E5.3引导/构建 → E5.4独立双VM → E5.5真实GPU克隆 → E5.6交付。详见 [设计修订二及验收矩阵](E5-DESIGN.md#修订二借鉴cybercafe的启动交付机制2026-10-23)。

生产关键依赖为可信启动凭据、GPU UUID稳定/独占、GPUStack原生注册权限及旧会话撤销/路由收敛；可先独立做本地设计和模拟测试，不能绕过门槛发布。登记服务不承担节点健康、不进入推理请求链路。现有001/002永久不重启；母版管理员已重启但SSH超时，升级激活未知。本轮只完成文档修订，未实现/部署E5功能，其他增强继续暂缓。


### E5.1只读取证进展（2026-10-23）

已恢复访问tower并确认驱动580.95.05、CUDA13微型内核及NVIDIA runtime识别成功；GPU容器和克隆未验证。新增供应商swbootid/ACK通道线索、既有CyberCafe开机引导、身份未重建、时钟异常及新worker到Server18080不通等证据，见 [取证与异常预案](../../results/tower-post-reboot/REPORT.md)。E5.1部分取证完成，可信启动证明/UUID重绑定与独占/原生撤销能力仍未验收，E5.2–E5.6尚未实施。用户确认母版昨晚重启，机器日志时刻不足以反推实际时间；本轮未重启或改配置。


## E5修订四：启动配置服务范围收敛（2026-10-23，当前执行基线）

配置服务只保障克隆启动原生GPUStack worker并自动注册。tower为离线可用的镜像构建环境，001/002来自旧版镜像，新版驱动/CUDA/Container Toolkit只要求在新版独立克隆验证。首版包含环境检查、独立会话凭据、签名版本配置、兼容检查、幂等/冲突、错峰重试和一次注册结果回报，成功后退出；不包含运行期更新、依赖自动修复或第二套健康/调度。LAN为唯一登记权威，云端公共配置备份是后续任务，不能用备份manifest代替可信登记。

执行顺序保持E5.0设计完成 → E5.1原生能力/独立实验与版本盘点 → E5.2配置/登记最小接口 → E5.3引导/封版 → E5.4独立双VM → E5.5真实新版克隆与tower离线接入 → E5.6交付手册。E5.1部分取证完成，其余未实施；生产凭据和旧会话原生隔离仍为能力门槛。详见 [当前设计与验收矩阵](E5-DESIGN.md)，旧修订保留为历史，不继续扩大当前范围。不修改服务器，不重启001002，其他增强暂缓。


### E5触发契约补充（2026-10-23）

默认克隆开机从配置服务拉取并应用；管理员SSH到克隆后，可运行本地固定版本maas-worker-bootstrap.sh apply --trigger manual触发相同流程。命令是设计草案、未实现。E5.3统一boot/manual入口、进程互斥、会话复用、revision幂等与原子落盘；E5.4增加自动/手动一致性和并发验证。影响已有worker运行的变更先返回NEEDS_MAINTENANCE，既有维护完成后再执行，不自动停推理容器或重启Docker/VM，不隐含实现E1。详见 [两种触发方式](E5-DESIGN.md#两种触发方式与统一本机入口)。


### E5测试部署拓扑确定（2026-10-23）

001转为GPUStack Server-only并承载LAN配置服务；tower封版交付快照；独立新克隆验证；002保留旧版基线。执行顺序为E5软件/原生门槛 → 备份和维护排空 → 处理001实例/worker及旧运维假设 → LAN配置服务 → 母版引导封版 → 供应商快照/新VM → 首启与SSH手动apply场景。001当前有3个OCR实例（rapidocr-gpu两个都在001），不能直接停worker；002容量和暂降副本策略需实施前明确。详见 [可执行部署计划与回退边界](E5-TEST-DEPLOYMENT.md)。本轮仅只读核对和计划，不部署、不重启VM。


### E5首版认证：可替换共享注册码（2026-10-23，当前决策）

首版采用站点/批次共享注册码，不再将供应商一次性注入列为试点前置。E5.2分离RegistrationAuthenticator、AdmissionPolicy及SessionService；E5.3客户端CredentialProvider与Launcher分离。hash保存、scope/过期/撤销/限量/限速和独立会话审计为首版范围，母版仅可预置试点共享码，不含原生管理员/已注册身份。先在独立无业务模型cluster验证自动加入，不把持有码当作硬件认证。后续显式切换到一次性码/供应商证明，公共启动流程不变。详见 [模块化设计与替换验收](E5-DESIGN.md#首版共享注册码与可替换模块)。本轮未实现、未生成凭据或改服务器。


### E5寻址首版决策与后续增强（2026-10-23）

首版新版母版预置001固定LAN IP 10.11.31.1及部署时冻结的配置服务HTTPS专用端口；地址/CA引用与共享注册码文件分离，boot/manual读取同一配置。证书SAN覆盖IP，不扫描网络、不跳TLS，管理员SSH改地址后可再次apply。后续登记 [E5.F1稳定域名/DNS与启动地址注入](ENHANCEMENTS.md#e5f1配置服务稳定寻址与发现优化)，排在首版闭环之后，不阻塞实现。此为文档决策，未改母版或网络。


### 后续增强E5.F2：凭证管理与自动分发（2026-10-23）

已登记 [凭证生命周期、自动交付及更强认证方案](ENHANCEMENTS.md#e5f2凭证管理与自动分发增强)，安排首版启动闭环之后。包含一次性码/供应商证明插件、可信启动或SSH分发、批量轮换/撤销/恢复、派生会话和原生worker泄漏处置及平滑迁移。当前首版仍采用模块化共享注册码及必要的范围/有效期/限量/停用/脱敏审计，不因登记后续任务而取消这些约束。本次仅计划，无实际发码或服务器变更。


### E5 HTTPS首版决策（2026-10-23）

配置服务使用自签名服务端证书，母版预置完整公开信任证书，私钥只留配置服务器；正常验证IP SAN及有效期，不做独立CA/第三方证书集成。正式上线迁到独立稳定LAN机器时，制作正式镜像更新地址、证书和注册码，测试码停用。其他证书方式待实际扩展需求再评估。详见 [当前HTTPS设计](E5-DESIGN.md#首版https自签名服务端证书)。本轮未签发证书或更改服务器。


### E5自签名证书默认无限期（2026-10-23）

用户指定默认无限期，notAfter采用RFC5280约定9999-12-31T23:59:59Z，实现时验证签发/客户端兼容；不设周期续期，不跳TLS时间/SAN/信任校验。更换地址、密钥或正式镜像仍按可信交付更新证书。注册码/会话有效期不受影响。本次仅设计，无证书签发或服务器变更。


## E5修订六：统一配置读取，去除首版登记会话（2026-10-23）

同批配置相同，首版共享码直接授权GET配置；无需VM登记→会话凭据链路，也不增加E5DB/GPU资源表/每VM结果回报。原生GPUStack管理独立worker，配置服务只做版本文件、访问认证、签名/兼容与基本日志，结果在克隆本地查看。共享码可读原生cluster token时须承认其实际权限；先独立无业务模型cluster试点，不承诺每VM撤销、设备数量配额或重复GPU防护。

开发按 [F0–F7计划](E5-IMPLEMENTATION.md) 推进：原生契约→文件发布→认证只读API→本机应用→原生启动核对→image-prep→001切换部署→新版快照/真实新克隆验收。001保留控制面和MaaS任务执行器，仅去GPUStack worker；002保留旧版，不重启001002；tower只制镜像。固定001IP、自签名证书默认无限期、共享码模块化、boot/manual同入口等既定决定保留。

凭证分发/独立会话移至E5.F2，GPU资源关联移至E5.F3，不阻塞首版。软件先完成本地/隔离验证，再改服务器；本轮只修订文档，未实现或部署E5。


### 云备份配置服务：仅预留入口（2026-10-23）

指定 `http://configuration.cybercafe.phanlink.cn`，cloud_backup.enabled默认false；E5只预留配置字段和解析模块扩展点，不实现/部署云服务、不探测或自动切换。LAN失败时也不向备用端发送共享码/原生秘密，后续启用前再确定HTTPS和同步/授权方案。首版仍只依赖001 LAN HTTPS。


## 2026-10-23用户操作后的实际进展

用户已建独立Docker cluster `test1`（ID3）并删除001原生worker记录；只读发现原容器仍运行后，依据已选角色切换及用户允许停止模型服务，已停止001的wta-gpustack-worker和本机cybercafe-ocr，restart=no，保留镜像/卷。Server及MaaS API/任务执行器/observer/DB未重启，healthz=200；002未改，test1当前无worker/模型。见 [准备证据](../../results/maas-e5-preparation/REPORT.md)。

不再需要创建test1，仍需安全配置它的原生注册凭据；001旧维护脚本的固定worker/model假设尚待适配。现有模型可停止，不再作为E5切换的保留条件；原业务推理路由未重测，控制存活不代表OCR可用。配置服务和E5脚本仍未实现/部署，新克隆验收未完成。


## 2026-10-23实施进展：18840配置服务入口

已部署001 `https://10.11.31.1:18840`，只读共享码配置API、版本文件及专用INPUT规则上线，当前LAN允许10.11.31.0/24，policy/生成器支持多CIDR。自签名证书notAfter=9999-12-31T23:59:59Z在001/002/tower校验通过；授权读200、匿名401、非允许来源实测DROP。实际运行镜像8项测试通过，原Server/MaaS容器ID和启动时间不变。详见 [实施报告](../../results/maas-e5-preparation/BOOTSTRAP-HTTPS.md)及 [操作手册](../../deploy/bootstrap/README.md)。

这是F1/F2部分功能和网络部署，不是E5全部完成：通用发布CLI、boot/manual引导、image-prep、母版快照/真实新克隆注册、旧维护工具适配仍待做。云备用保持禁用；仅新克隆到原生18080/注册才是后续验收，tower到18080不通不作阻塞，未扩大原生端口规则。多CIDR能力不代表千节点容量已测；001002未重启。


## 2026-10-23最新实现状态：克隆客户端与离线母版工具

本节覆盖前文历史状态：F1新增本地签名发布/新revision回退CLI；F3拉取、验签、私有暂存/锁及status已完成；F4 boot/manual统一apply、所属Docker worker启动、原生me=true身份核对已实现并模拟验证；F5离线Ubuntu文件安装、污染拒绝和systemd单元已实现。客户端在001真实HTTPS接口连续prepare验证通过且未启动worker；本地累计32项测试通过。

尚未完成：发布CLI线上演练、旧维护工具适配、母版generalize/实际安装/保存快照、新建A/B克隆的开机与原生注册、真实GPU和模型验收。systemd只在启动时引导并有界重试，成功后不持续守护worker；镜像工具只写离线文件系统，不自动导出平台镜像。001002未重启，原生18080规则未改，云备份禁用。详见[实现记录](../../results/maas-e5-preparation/CLIENT-IMPLEMENTATION.md)及[客户端操作说明](../../deploy/bootstrap/clone/README.md)。

下一步先核对母版首启身份生成和旧启动钩子，再安装客户端/缓存固定镜像、保存新版快照并创建独立新克隆；以克隆到18080和实际注册为门槛，tower不通不作阻塞。


## 2026-10-23母版准备暂停点（用户最新要求）

用户要求先准备、不要部署到tower。已完成本地公开交付包（源码commit f7de733）、只读清单脚本及校验工具；34项测试和实际包解压/临时离线文件树安装验证通过。真实码未拉取，无token/私钥入包。tower仅只读取证：Ubuntu24.04/Python3.12/crypto41.0.7及NVIDIA runtime已具备，但固定worker镜像未缓存、旧CyberCafe服务enabled、machine-id/SSH主机密钥未generalize。

**当前只完成本地部署材料准备，不是实际母版封版完成。** 不上传/安装到tower，不自动缓存镜像、停旧服务、清理身份或请求管理员立即制作快照。实际部署及旧启动职责审查、首启身份契约、离线镜像来源/构建方式确认仍待后续部署指令；检查通过后再由管理员制作快照、创建和启动独立A/B。001002不变。详见[母版准备报告](../../results/maas-e5-preparation/TEMPLATE-PREPARATION.md)。


## 2026-10-26实际部署：tower业务层已安装，封版待完成

用户本轮授权部署，并明确确认cybercafe-agent/provision由E5替代，覆盖此前“只准备不部署”的暂停点。已在tower私有备份7个旧文件及状态到/root/e5-before-install-20261026-105709；两项旧服务disabled/inactive，供应商命名/网络/SSH服务未改。客户端、公开信任材料、600权限配置和共享码已安装；真实HTTPS/Ed25519连续prepare验证通过，临时原生注册材料已删除。固定GPUStack镜像linux/amd64按摘要缓存成功，E5单元enabled/inactive，尚无容器或实例状态。Docker未重启，machine-id和SSH密钥未清理，001002未修改。

本轮完成的是母版E5业务层部署，不是平台快照交付或真实克隆验收。封版前仍需确定首次machine-id/SSH主机密钥生成及启动顺序、迁出旧设备凭据和含秘密回退材料、管理员确认供应商失败单元影响；随后由管理员保存快照并创建/首次启动独立A/B。原生注册、GPU容器与模型能力须在新克隆验收，不能用母版prepare成功替代。

实际命令修订、备份/回退及证据见[母版手动部署手册](../../deploy/bootstrap/clone/MANUAL-TOWER-DEPLOYMENT.md#15-2026-10-26实际部署记录已执行)和[部署结果](../../results/maas-e5-preparation/tower-deployment-20261026.json)。本地公开包仍是f7de733历史交付，未重打包；本轮手册修订单独交付，不把旧包内“暂停部署”的文案视作当前状态。


## 2026-10-26执行边界调整：适配既有供应商环境

用户明确：网络、命名、machine-id和SSH主机密钥均为供应商环境现状，基本无法获得技术支持或网管API。当前E5试点不依赖供应商API、启动注入、离线镜像编辑、首启泛化支持或问题解释；管理员只做平台已有的人工快照/创建启动VM操作并交付访问入口。

当前决策覆盖前文将OS身份泛化和供应商确认作为试点阻塞条件的要求：保留现有machine-id、SSH主机密钥及供应商网络/命名机制，不部署新OS身份初始化模块，不改写当前系统身份。共用SSH主机密钥的安全边界和其他组件依赖machine-id的潜在影响保留记录，不将其描述为生产隔离已满足。

GPUStack223 worker.worker_uuid读取运行内存或本机data_dir/worker_uuid，不读取machine-id（依据本地v2.2.3的gpustack/worker/worker.py和gpustack/utils/uuid.py）。E5另生成随机本机worker名；因此重点保证镜像没有E5 identity.json和原生UUID/token等实例状态，再由真实A/B核对原生记录独立。相同machine-id不直接作为判定注册失败的条件，不承诺因此不存在任何平台网络副作用。

试点前检查只覆盖我们能控制和验证的范围：E5及依赖已安装、固定镜像摘要缓存、共享码/HTTPS配置有效、旧业务服务disabled、镜像无E5/原生实例身份，并处理我们旧业务中的不必要秘密与备份交付。供应商失败单元只记录现状；未证明影响相关能力时，不要求先由供应商修复才创建测试克隆。若新克隆网络、hostname、GPU或SSH确有异常，保存证据并按实际可控范围适配，不推断供应商已提供支持。

管理员制作受控试验快照并创建独立A/B后，我们验证：18840和18080可达、实际hostname不触发母版保护、自动配置拉取、两个独立原生worker、GPU可见与模型请求、重复手动apply不重复创建。先记录自动首启结果再手动恢复，不能将人工恢复成功算作无人值守通过。若首次失败，只在新克隆排查/适配；001002仍永久禁止VM重启。

正式生产系统身份泛化/SSH密钥隔离另作后续评估，不作为本轮小规模E5闭环前置；E5.F1/F2/F3的增强不默认依赖供应商API。旧离线image-prep仍保留严格清洁镜像策略，本试点采用已完成的运行中母版手动安装路径，不调用其generalization门槛，也不为适配而删除或绕过检测。


## 2026-10-26优先级更新：旧凭据清理为Nice to have

用户将清理旧CyberCafe凭据/残留明确列为低优先级后续任务[E5.F4](ENHANCEMENTS.md#e5-f4-legacy-cleanup)，暂缓执行，不再作为本轮试验快照、A/B自动注册或GPU/推理验收的前置。此前“先清理旧秘密再创建试验克隆”的顺序由本节覆盖。当前主线仍为母版→管理员快照→独立新克隆→实测接入，不等待供应商API/支持。

备份优先保存在Mac私有目录。中断后的本地核对显示归档及15个源文件校验记录已完成，母版尚未删除旧文件或秘密备份；下次开展先复核记录。本轮不继续清理。E5/原生worker实例身份不得固化的要求仍保留，与本项可选旧业务清理分开；供应商系统身份、SSH/网络及仍在使用的E5凭据不在清理范围。


## 2026-10-26替换验证路线：新VM加入test1，不迁入旧集群

用户明确管理员先回收旧002，再基于tower新快照创建一台全新VM。新VM自动加入test1（cluster ID3），**不是**002所在的cybercafe-poc（cluster ID1）；“取代002”指最终承接模型服务能力，不是复用旧worker记录或自动迁移旧cluster模型。

1. 管理员回收旧002、保存tower试验快照并创建/首次启动新VM；这是旧环境退役、新独立环境交付，不由本系统执行重启/重建001002，也不要求保留旧002直到新VM成功。记录新快照版本及新VM访问入口，不依赖供应商API。
2. 首先保留新VM的自动启动证据，不先手动apply；检查18840和18080、配置revision/cluster_id=3、独立本机/原生身份及GPUStack test1中的实际worker状态。
3. 注册通过后，在test1显式部署测试模型、验证GPU/模型推理；旧cluster1的rapidocr-gpu实例/模型不会因新worker注册自动迁移。随后核对或调整MaaS逻辑模型到新部署的上游路由/版本绑定，再测试完整客户入口。不能将cluster3有worker写成原OCR入口已恢复。
4. 旧cluster/失联worker/pending模型记录按后续运维清单处理，不把删除VM误认为原生记录和MaaS目录已同步删除；旧业务凭据清理E5.F4仍低优先级，不阻塞本次验证。第二台克隆用于之后验证同快照的身份独立和多节点行为。

实际代码前置注意：当前已部署worker.py在注册之前就按hostname拒绝XZ-31-002，并按IP拒绝10.11.31.2；此判断与目标cluster无关。**如果新VM复用这些名字/地址，将被误拦截。** 需在制作快照前明确实际分配方式并适配自己的角色保护，或确认新VM不使用这些值；不能仅改为test1就认为问题已解除。目前未修改保护代码，未执行任何VM回收/创建或模型迁移操作。


## 2026-10-26后续增强：角色驱动的worker启动保护

新增[E5.F5](ENHANCEMENTS.md#e5-f5-role-based-guard)：以明确节点角色/启动许可替代固定名称与IP黑名单，继续保护控制端和母版，避免克隆复用旧002地址时误拦截。状态为后续设计/排期，未实现、未部署，不设为本轮完整改造前置；当前002复用的最小兼容修正仍需独立处理，不能因登记增强就认定保护已经修改。

方案必须处理template角色随快照继承的问题，只依赖母版→快照→克隆及我们可控流程，不预设供应商API、可信注入或独立系统身份。此保护用于运维防误操作，不是可信设备认证，也不改变001002禁止VM重启的约定。角色不明时给出可解释状态，不自动修改角色、停止服务或重启VM。完整范围与验收见增强条目。


## 2026-10-26旧002复用兼容修正：本地完成，部署受阻

用户授权应用当前002相关最小改动。源码提交596516e已移除worker.py对XZ-31-002名称、10.11.31.2地址的禁止启动判断；保留XZ-31-001、tower名称及10.11.31.1控制端地址保护。bootstrap测试38项通过，新增测试覆盖旧名称/旧地址分别和同时复用，以及混入控制端地址仍拒绝。

**尚未部署到tower，当前不能通知管理员保存最终试验快照。** 本轮MCP连接111.4.255.126:6025超时，直接SSH同样连接超时；001仍可连接，但从001到原tower内网10.11.31.3:22也超时。未读取或修改tower文件，不能假定其当前环境未发生其他变化；未操作旧002或重启任何VM。

本地补丁及摘要已准备在dist/e5/002-guard-patch-596516e（不含凭据）；[状态记录](../../results/maas-e5-preparation/002-guard-patch-status.json)。原f7de733公开包保持历史内容，仍含旧保护，不能通过重装旧包获得本修正。待管理员确认母版在线/当前SSH入口后，先核对目标版本、优先备份旧worker.py到Mac，再仅替换模块并核验；不启动worker、不清理旧凭据。部署验证通过后再明确通知保存快照。


## 2026-10-26：管理员确认新母版，002兼容补丁已部署

当前6025入口对应的新母版为tower-8CbKAhaaf，管理员已确认其角色。源码fbd3d3e在允许新克隆复用XZ-31-002名称/10.11.31.2地址的同时，将新母版名称纳入保护；001名称及10.11.31.1保护保留。该补丁已原子替换母版的/opt/maas/lib/maas_bootstrap/worker.py，旧文件优先备份到Mac。此前SSH不可达、补丁未部署的记录为历史状态。

38项本地bootstrap测试通过；远端摘要、导入、真实母版拒绝、模拟002复用允许和控制端IP拒绝均已核验。cluster3配置保持不变，无容器、无E5/native注册身份。单元enabled/failed为此前开机尝试留下的状态，本轮未启动、重启或重置单元，未运行apply，未修改驱动、旧凭据或001/002。真实克隆启动注册尚未验证，模拟保护测试不等于注册验收。

GPU仍不可用：当前PCI 10de:2f04要求NVIDIA open kernel modules，实际加载闭源模块，nvidia-smi显示No devices found。母版到001:18080此前仍超时；按既定边界，原生端口重点在新克隆实测。下一步先单独处理GPU模块兼容，再由管理员安排受控快照/新克隆，验证配置拉取、test1自动注册、GPU和模型服务。不得把本次补丁成功写成GPU环境或真实首启验收通过。

原f7de733归档包仍是历史版本，重装会丢失本次修正；需使用当前源码重新构建，或显式叠加fbd3d3e模块并核对摘要。证据见[部署核验](../../results/maas-e5-preparation/002-guard-patch-deployment-20261026.json)，GPU只读取证见[new-tower-comparison-20261026.json](../../results/maas-e5-preparation/new-tower-comparison-20261026.json)。E5.F4清理、E5.F5通用角色保护继续暂缓。


## 2026-10-26：TOWER母版名称保护规则更新（已部署）

管理员确认“TOWER就是改镜像的”。当前试点据此采用命名约定：hostname忽略大小写后，等于tower或以tower-开头即拒绝启动worker；不再逐台登记随机后缀。XZ-31-001名称保护也忽略大小写，10.11.31.1地址保护保留；002名称/IP继续允许复用。towerish-worker、worker-tower不匹配母版规则。

保护在配置拉取、环境检查和容器创建前执行，因此母版的开机入口及手动apply均不会进入接入流程。此规则只用于防误启动，不是身份认证；管理员说明并非完整命名规范，首次普通克隆仍需实测hostname。若普通克隆继承tower名称会被拒绝，应先取证再适配，不直接移除母版保护。

本地40项bootstrap测试通过，母版模块已备份到Mac后原子替换；远端真实名称及模拟名称保护、002复用均验证通过，installed SHA256为7db3e0709fec7728e8c310798879b5dc14c589b3845823f953f66fb59b465ae5。无容器、无注册身份，原enabled/failed历史状态保留。本次未运行apply、未启动worker、未重启VM或改驱动。GPU兼容问题及真实克隆注册仍未验收；若只分配4070/4080，可先用新克隆验证现有驱动，不将母版5070修复作为这项试验的强制前置。

[本次部署证据](../../results/maas-e5-preparation/tower-prefix-guard-deployment-20261026.json)。原f7de733归档包未更新，重新部署须使用当前源码构建或叠加当前模块并验证摘要。通用角色识别E5.F5仍为后续增强。


## 当前执行顺序：先验证第一版快照，再应用新改动（2026-10-26用户明确）

管理员当前可用于创建VM的最新已保存镜像仍是第一版，包含E5自动启动/原生注册逻辑，但不包含后续“放行002名称/IP”和“保护tower及tower-*”修正。当前tower文件已安装新补丁，不等于已保存镜像包含补丁；不推断平台会自动持久化母版改动。此前部署证据仅证明当时母版文件的状态。

1. 管理员使用第一版已保存镜像创建独立普通VM，提供SSH入口、预期镜像版本及GPU型号；本系统不执行平台操作，不回收或重启001/002。本轮先不要求保存新快照，也不在新VM上提前打补丁或手动apply。
2. 新VM上线后先只读取证：核对hostname/IP、worker.py摘要、启动时间、E5错误码与日志、GPU/容器运行时、到001:18840及18080连通性、独立身份/容器和test1原生worker记录。保留首次开机证据，不把人工补救算成自动注册成功，不输出凭据。
3. 第一版worker.py参考SHA256为4b6a48c469aad46d261cee29b937c4dd279ad5556bd91481aefe28e53f87dc9f，以现场文件核对为准。复用XZ-31-002或10.11.31.2会被旧保护拒绝，这是本轮已知预期限制，不算新回归。第一版只保护原tower具体名称，不含通用tower-*保护；管理员应确认新VM是普通克隆。其他合法名称/IP才可继续观察原有自动注册流程。
4. 完成第一版结果记录后，再应用新补丁并作对照验证。若首轮已创建worker/注册身份，先检查运行状态及变更影响，再安排配置应用，不删除身份或强行重复注册。补丁后人工apply仅证明手动路径；新版真实首启仍需管理员保存包含补丁的后续快照，并另建独立克隆验证。
5. 新版参考摘要为7db3e0709fec7728e8c310798879b5dc14c589b3845823f953f66fb59b465ae5，源码d81f02b。现有母版补丁不回滚；此次只更新执行计划，不修改服务器或镜像。旧凭据清理、角色系统等后续增强继续暂缓。

阶段验收分别记录“第一版自动启动结果”“新补丁部署/手动验证结果”“新版快照真实首启结果”，不能互相替代。若分配4070/4080可先实测现有驱动；GPU实际识别、容器访问和注册结果仍以新VM取证为准。


## 第一版真实克隆004首启检查（2026-10-26）

新入口111.4.255.126:16482实际为XZ-31-004（10.11.31.4），15:06:12启动。worker模块摘要匹配第一版，未安装后续保护补丁。RTX 4080 SUPER 16GB、闭源580.95.05可正常读取GPU；Toolkit1.20.1。此为宿主机识别结果，不代替容器GPU/模型验证。

E5开机入口已触发并自动重试5次，最终NATIVE_SERVER_UNREACHABLE、enabled/failed。按代码顺序已通过配置拉取/验证、Docker/GPU环境检查，在原生Server网络检查失败；18840可连、18080超时。无容器、无注册身份，test1仍0 worker。因此配置引导部分有效，自动注册首启验收未通过，不能归因为002或tower名称保护。

001在10.11.31.1:18080监听，但旧MAAS-P3-HOST/MAAS-P3-DOCKER网络规则仅允许局域网来源.1/.2，新克隆.4未纳入；配置服务18840单独允许10.11.31.0/24。这是已确认的访问策略缺口，但未做抓包/对照放行，不能仅凭规则断定超时的唯一根因。下一步设计原生端口最小放行与反向worker连通性验证，再安排人工重试；该结果不能替代后续新版快照首启验收。

本轮仅只读检查，未打补丁、运行apply、重启服务/VM或改防火墙；本机仅首次记录新SSH入口的主机公钥。[脱敏证据](../../results/maas-e5-preparation/clone-004-first-boot-20261026.json)。


## 2026-10-26：004网络修复与手动接入验收

001持久化策略新增native_server_cidrs=["10.11.31.0/24"]，支持后续多个IPv4 CIDR。仅在001局域网接口放行到10.11.31.1:18080/tcp：raw开机过滤、HOST和DOCKER链均受控；Docker按conntrack原始目的地址/端口匹配，未扩大所有端口或容器直连许可。原配置18840规则保留；其他业务端口、IPv6和SSH范围不变。重用原boot/refresh服务实现持久化，未重启系统或Docker。策略源码及配置已备份至Mac和001私有目录。

004实测18840/18080连通，原生/v2/workers未认证返回401，18834/8820/11435仍超时；修复前18080超时，修复后连通，完成实际网络对照。refresh再次执行成功，边界rules_ok/inventory_matches和IPv4/IPv6早期hook验证通过；实际整机重启持久性未测试且不得在001002执行。

首次人工apply实际注册了worker ID4至test1(cluster3)，原生状态ready/unreachable=false，但E5误报REGISTRATION_UNCONFIRMED。查v2.2.3 WorkerPublic定义，worker_uuid明确exclude=True，不会在列表响应中出现。确认逻辑已修复为使用本机原生worker token访问/v2/workers?me=true，严格要求单条记录、me=true、集群及本机名称匹配；本机UUID/token/名称文件仍须存在且有效。不是忽略身份或改用管理员token，也不重放注册接口。

修复仅替换004与当前tower模块中的确认条件，分别保持各自名称保护版本；004仍未安装“放行002/保护tower-*”改动。Mac保存修改前模块。之后apply返回APPLIED/ready，再次apply返回UNCHANGED，容器ID/启动时间未变，未重复创建worker。容器内nvidia-smi正常识别RTX4080 SUPER/580.95.05。001反向10150和实际metrics_port=10151可达；之前探测10161拒绝仅说明该端口不是当前实际指标端口。bootstrap40项、边界相关7项测试通过。

第一版首启失败证据保留，本次只判“网络修复后的手动注册及重复应用成功”；未做新快照首启、双克隆独立性或模型推理验收。原systemd failed为首启历史状态，手动CLI成功不自动清空。当前tower文件含修正，管理员保存的第一版镜像仍不含它。下一步按用户计划评估安装剩余保护补丁，再由管理员保存新版快照并新建独立克隆验证自动接入；不要重启或重建001002。

[网络修复证据](../../results/maas-e5-preparation/native-network-fix-20261026.json) · [手动接入与确认修复证据](../../results/maas-e5-preparation/clone-004-manual-registration-20261026.json)。


## 第二版受控试验快照前核对（2026-10-26）

只读核对当前tower文件：002名称/IP复用、大小写无关tower/tower-*保护及GPUStack223 me确认修复均已安装，模块SHA256为bf34d3d93ae362f3de985a31a81eea5495dcd5739fa6243be251391bfcc6f994。启动入口enabled；无容器、E5 identity/applied或原生token/UUID/name，固定worker镜像已缓存，配置cluster3及信任文件/共享码齐备，旧agent/provision disabled/inactive。到001:18840/18080均可连。仅有apply.lock和旧last-result.json，systemd failed属历史启动结果，不作为已注册实例或当前worker运行状态，不需为试验快照重启/人工apply。

结论：若下一台独立普通克隆分配RTX4070/4080系列，本轮无额外必须写入母版的改动，可请管理员保存第二版受控试验快照并启动新克隆，先只读观察自动启动，再核对test1 ready、独立身份及GPU。001的18080网段修复属于控制端配置，无需写进母版。当前母版5070仍不可初始化，不宣称第二版支持5070；若新克隆分配5070须另处理驱动模块。旧凭据清理继续Nice to have。

不要用已注册004保存母版，避免复制实例身份；应保存当前tower。记录管理员侧快照版本/时间及新VM SSH入口，保留004作为手动接入对照。本轮未保存快照、修改服务器或重启VM；[封版前只读核对](../../results/maas-e5-preparation/template-snapshot-readiness-20261026.json)。新版无人值守首启、双克隆及模型推理仍待实测。


## 第二版真实克隆首启自动接入通过（2026-10-26）

用户确认端口16482现为新建VM，虽然复用XZ-31-002名称及10.11.31.2地址，不能视为旧002或先前004。只读检查启动时间16:49:33；worker模块SHA256 bf34d3d93ae362f3de985a31a81eea5495dcd5739fa6243be251391bfcc6f994匹配封版前母版，证明保护及注册确认修正已随镜像带入。

E5于16:49:46自动开始、16:50:00完成，trigger=boot、code=APPLIED、NRestarts=0，单元enabled/active(exited)，符合oneshot成功状态。新worker ID5、随机名称e5-9c5be5b92f10f84b714a5dbe自动加入test1(cluster3)，本机原生身份文件存在。首启记录native_state=not_ready是注册时的瞬时快照；随后本机个体token的me=true查询及001管理查询均显示ready/unreachable=false，不将旧快照误读为当前不可用。

宿主机和worker容器均识别RTX4080 SUPER/580.95.05，18840/18080可达，001反向10150/10151均可连。002名称/IP复用未被误拦截。整个检查未运行prepare/apply、未改配置/补丁或重启任何VM，只有只读命令及容器内GPU查询。因此第二版独立克隆的无人值守配置获取、worker启动和原生纳管链路通过本次实测。

边界：未部署模型或执行端到端推理；未验证5070、所有GPU型号、大规模或多克隆同时在线。新ID5与先前ID4名称不同，可证明观察到独立原生记录；004目前not_ready/unreachable，不算双节点同时可用验证。旧worker记录未自动删除，后续清理需区分集群/ID。母版启动保护采用名称约定，仍非平台可信角色认证。

下一步在test1为新worker部署测试模型并通过MaaS客户入口完成请求；另安排独立克隆并行身份验证。不得通过重启001/002开展测试。[第二版首启证据](../../results/maas-e5-preparation/second-snapshot-clone-002-20261026.json)。


## 2026-10-26：新克隆OCR及MaaS端到端通过

test1新worker ID5上已部署独立maas-ocr-e5-r1（model12/route14/instance94），完整OCR image ID校验匹配已验证版本。MaaS客户模型仍为ocr.default，上游切到新route，静态revision登记为ocr-e5-r1，未改写旧route含义或历史数据。切换前确认无排队/活动/UNKNOWN、备份catalog及key模型许可，暂停排空后更新并以真实探针恢复；没有容器/VM重启或collector恢复。

私有TLS端到端：同步200识别46行；输入上传201、异步提交202、SUCCEEDED/attempts1；结果下载200、46行、新revision正确，处理node均为新克隆GPU。SQL确认本任务成功1条且计量1条。收尾未暂停、活动请求0、UNKNOWN=0，worker/model ready；临时LAN镜像传输服务已关闭。部署及核验脚本已保存在scripts/maas/runtime，回退材料优先备份Mac。

发现的实际部署缺口：原宿主机模型目录没有挂入bootstrap worker，实例93路径检查失败。试点改用专用公开元数据目录，在宿主机与现有worker分别准备，移除失败实例后由GPUStack生成94；OCR程序/权重仍在镜像内。此为人工分发和容器文件系统准备，重建worker需重新准备，不是规范模型卷或自动模型分发完成。后续需完善模型目录生命周期与分发；本次未更改母版。

另一待办：常驻维护binding仍引用旧worker2/model1、9，不适用于当前worker5/model12及SSH16482。先重绑并校验，不能直接调用旧绑定。双克隆并行、故障迁移、当前版本长测、5070及生产验收仍未完成。

[端到端报告](../../results/maas-e5-ocr/REPORT.md) · [试点操作手册](../../deploy/maas/E5-OCR-PILOT.md)。


## 模型目录/分发前置调研完成：复用GPUStack223原生能力（2026-10-26）

核对固定v2.2.3提交1cfcbbff02aacdb51f0a390378dd7bcddd9bf365和实际runtime0.2.1/runner0.1.27.post4，原生支持私有镜像地址、username/password auth_config、Always/IfNotPresent/Never（默认IfNotPresent）、digest拉取、mirrored挂载及HF/ModelScope下载。7项原生函数内存mock通过，无真实Docker API/registry调用。界面Image Credentials及Volume Mounts属于Kubernetes选项，不能当成Docker worker的现成凭据分发配置。

关键更正：官方worker镜像默认mirrored=true，当前OCR实际继承整个/var/lib/gpustack数据卷RW。此前“只挂载专用模型父目录”只适用于非mirrored路径，不能用于当前环境。试点复制元数据让local_path通过，但没有证明该目录传给OCR；OCR权重在镜像内，原请求/计量验收有效，目录隔离尚未完成。已修正试点报告和操作手册，未修改服务器或读取真实秘密。

后续不自研分发调度器。E5仅补worker启动契约：独立模型目录bind、原生身份/认证卷排除、必要cache目录、registry信任与只读认证接入、挂载布局版本检查。GPUStack继续负责模型调度、拉镜像、下载及推理容器生命周期；registry服务与OS/NFS挂载属基础设施。

下一门槛是小测试镜像的真实私有registry认证冷拉、正确/错误凭据、digest与缓存行为，以及新克隆目录可见性/身份隔离验证。实际私有仓库、自签信任无重启生效、NFS和新布局尚未实测；不把源码支持写成部署完成，不在001002做VM或Docker引擎重启。默认registry不会替换显式quay.io地址，发布后使用registry manifest digest而不是本地image ID。

[详细核对报告](GPUSTACK-223-DISTRIBUTION-REVIEW.md) · [源码及实测证据](../../results/maas-e5-distribution-review/findings.json)。


## 私有仓库第一项真实验证通过（2026-10-26）

在001隔离部署临时HTTPS registry，独立reader/publisher与LAN ACL，精确新增Docker certs.d信任，无insecure registry/daemon配置改动。真实发布小探针镜像并登记registry manifest digest（不同于本地image ID）。匿名/错误密码401、reader读取200/写入401；两端Docker28.2.2无重启完成自签信任接入。

新worker5原先没有canonical引用及对应source config ID。GPUStack原生调度的错误凭据model13/instance95授权失败；正确只读凭据model14/instance96真实冷拉并running/health通过，digest/config ID匹配发布值，registry日志可见manifest和layers下载。停止测试仓库入口、worker连接被拒绝时，新model15/instance97仍运行，验证IfNotPresent缓存复用。未在worker手动pull/load，也未修改worker启动配置。

收尾：删除本次3模型、backend21及全部探针容器，撤销两个测试账号并验证401；测试registry两容器停止、restart=no、18443/18500无监听。数据/镜像及精确trust文件保留，账号/私钥优先备份Mac。原worker/OCR容器ID、启动时间和restart count保持，两端Docker PID/boot未变；MaaS OCR仍200/46行，原网络边界核对通过。

第一项门槛已通过，正式registry生命周期、worker只读凭据自动接入、独立模型卷/原生身份卷隔离、新镜像/新克隆及NFS仍未实现或验收。测试凭据只放临时probe模型env且已撤销，不采用此方式作为正式凭据设计。下一项先完成模型目录/挂载隔离的设计与最小验证，保持复用GPUStack原生机制。

[第一项报告](../../results/maas-e5-registry/REPORT.md) · [操作记录](../../deploy/maas/REGISTRY-PILOT.md)。


## model-storage-v1已实现并在新克隆验证；母版待连接恢复（2026-10-26）

核心源码e202112：独立只读发布目录/srv/maas/models、可写缓存/srv/maas/model-cache，原生mirrored保留vendor排除项并排除/var/lib/gpustack，显式自身容器名；新版本/摘要核对实际挂载及关键env，旧布局返回NEEDS_MAINTENANCE。根目录重叠、秘密路径、symlink及其他用户可写根拒绝。57项bootstrap测试通过，离线构建与公开交付包已包含storage.py。

新克隆16482已完成容器级维护：显式暂停/排空，原model12降0，停止保留旧worker容器，原生UUID/name/token/JWT字节及worker ID5保持，创建新worker，重复apply=UNCHANGED。无VM/Docker引擎重启。当前model16/route15/revision ocr-e5-r2使用公开新路径；旧版本保留停用，不覆盖原revision。实际推理容器只有模型RO与缓存RW两个卷，身份文件/socket不可见，RO写失败EROFS、缓存写入宿主机可见。同步/异步/下载/一次计量均通过，MaaS已恢复、UNKNOWN=0。

母版6025无法连接，用户确认暂时不可达并会通知恢复；未向母版写入本轮改动，不反复尝试。公开包已生成并核验，等待恢复后备份/安装/确认母版无身份，再由管理员保存新快照并起独立克隆。此处未做新快照/真实首启、NFS、HF下载或强隔离验收。原通用维护binding仍旧，独立待重绑；本轮不是通用E1实现。

[本轮报告](../../results/maas-e5-storage/REPORT.md) · [部署与回退](../../deploy/maas/MODEL-STORAGE-V1.md)。

## model-storage-v1母版安装完成（2026-10-26 19:18 北京时间）

用户通知母版恢复后，确认6025现为tower-icDC5YcU3，boot为19:10:12；旧源码摘要符合预期。旧client/worker/bootstrap配置已优先私有备份Mac，随后安装e202112三模块和公开存储配置，未运行apply、未启动worker、未重启VM或Docker。

核对通过：cluster3/test1配置保留、母版保护返回PROTECTED_HOST_WORKER_FORBIDDEN、启动单元enabled、无worker实例身份目录/identity/applied/维护receipt、无Docker容器，独立模型目录与缓存及公开marker齐备。单元failed为开机被母版保护拒绝的历史结果，本次不重置或启动它。只读目录属性由克隆创建worker时落实，母版目录准备不等于推理容器挂载验收。

现在可以请管理员保存含model-storage-v1的新快照并创建独立克隆；收到新入口后验收首次启动自动注册、真实挂载和OCR。本轮没有制作快照，也没有验证这个新快照的首启；001/002未操作。旧凭据清理继续暂缓。[安装证据](../../results/maas-e5-storage/tower-installed.json) · [母版核对](../../results/maas-e5-storage/tower-verification.json)。此前母版待连接记录为历史状态，由本节更新。


## 后续增强E8：GPUStack v2.3.0适配（2026-10-27）

用户已要求登记为后续增强，优先级高，当前仅完成源码调研。顺序：先完成当前E5的2.2.3新版快照克隆验收，再在独立环境适配正式2.3.0；若千节点E7采用2.3基线，先过E8功能门槛再做规模验收，不阻塞E7调研。现网、worker及母版继续保持当前版本，不立即升级。

阶段：兼容性清单 → 独立环境/启动与维护工具适配 → OCR/MaaS全链路回归 → 原生revision/YAML/LB/鉴权能力复用验证 → 分发与挂载隔离回归 → 备份恢复及受控发布。先server后同版worker；母版更新和管理员快照另验，禁止001/002 VM重启。E1–E7业务边界保留，不恢复collector、不附带LLM高级能力或后端合批。

[范围、依赖与验收](ENHANCEMENTS.md#e8-gpustack-230-adaptation) · [调研报告](GPUSTACK-V2.3-RESEARCH.md)。实施/独立验证/现网升级尚未开始。


## model-storage-v1新快照克隆实际验收（2026-10-27）

用户确认管理员保存新快照并启动新克隆，SSH16482、hostname XZ-31-002和10.11.31.2复用，实际为独立新worker6（e5-ba71e774d9ca94c0138c01fa）。手动apply之前已取得trigger=boot/APPLIED及原生ready证据，三模块摘要与母版一致，layout=model-storage-v1。后续手动apply=UNCHANGED，身份/worker容器和boot ID不变。新GPU RTX4080SUPER、580.95.05，与旧GPU UUID不同；不据此宣称同GPU跨VM复用已验证。

首次部署OCR发现新克隆无业务镜像，历史quay.io/cybercafe-maas-ocr:p2-r1远程引用404；停止新部署失败重试并暂停排空MaaS。随后通过LAN SSH从001缓存离线交付精确镜像，未启用旧临时registry、修改authorized_keys或长期分发凭据，未重启VM/Docker。model17/route16、revision ocr-e5-r3现为服务目标，旧model16副本0保留。模型RO/缓存RW及身份/socket排除实测通过；同步/异步/结果下载46行、一次计量通过，MaaS恢复接单、UNKNOWN=0。

此次通过的是“新版快照首启接入、挂载布局及离线交付后OCR闭环”，不是完全无人值守模型分发。下一步优先持久镜像仓库、只读拉取凭据/证书自动接入和空缓存新克隆冷拉取；继续复用GPUStack分发，不固化12GB业务镜像作为唯一方案。E8仍是后续计划，当前v2.2.3不变；双克隆/NFS/千节点及通用维护binding适配未因此完成。

[新快照克隆验收报告](../../results/maas-e5-clone-storage-20261027/REPORT.md)。

## E5镜像分发第一步完成（2026-10-27）

已在001部署常驻LAN HTTPS registry并发布不可变OCR镜像；独立registry.py实现签名payload绑定、endpoint allowlist、精确Docker信任和专用只读SDK认证目录。worker6经显式容器维护保持原生身份，当前registry-access-v1，重复apply=UNCHANGED，VM/Docker引擎未重启。67项bootstrap测试通过。

本次无worker手工pull/load，GPUStack自动取得新manifest/config/layers并启动model18/route17；test1及os=linux/arch=amd64选择器取代随机worker-name。OCR/MaaS同步异步/下载/一次计量46行通过，当前ocr-registry-r1、UNKNOWN=0。旧基础镜像缓存存在，不替代全空缓存新快照首启。

母版未更新。活跃配置e5-registry-r1仅兼容tower-e5-registry-v1，旧快照image_id会收到409；下一步先更新母版客户端/允许registry目标/image_id，保留母版保护、排除实例/auth状态，再由管理员保存快照和创建独立克隆验证无人值守首启。replicas仍1，新节点加入不自动增加副本。回退材料保留，实际回退未演练，生产HA/RPO和NFS权重分发、E8仍未完成。

[仓库报告](../../results/maas-e5-registry-service/REPORT.md) · [第一步验收](../../results/maas-e5-registry-access/REPORT.md) · [操作与回退](../../deploy/maas/REGISTRY-ACCESS-V1.md)。


## registry-access-v1母版实际更新完成（2026-10-27）

用户授权后，6025母版tower-KuGJk9WU8已备份并安装client/worker/registry，storage保持；image_id=tower-e5-registry-v1，registry allowlist仅10.11.31.1:18443。实际源码导入/摘要、内存读取及验证e5-registry-r1、registry-access-v1布局计算、母版保护与enabled启动单元均通过。无实例身份、registry-auth/generations或Docker容器，未运行apply或重启VM/Docker，未触碰001/002服务。OCR未预置，仅缓存固定worker镜像。

下一步由管理员保存新快照、启动普通独立克隆，验证首次自动配置/注册/调度/认证拉取/OCR。既有旧快照不随文件更新而改变，真实新版首启尚未验证。当前replicas=1，若保留worker6需安排测试部署或额外副本。E8与其他增强不因此启动。[母版报告](../../results/maas-e5-registry-template/REPORT.md)。


## 无OCR镜像缓存的新快照首启验收通过（2026-10-27）

管理员启动端口16487的新克隆，开机自动读取e5-registry-r1并以独立worker7加入test1，自动生成registry信任/只读认证。首次取证时新旧OCR镜像均不存在。旧worker6已离线，GPUStack使用同一model18/route17及稳定标签自行将instance103调度到7，原生拉取固定镜像并进入running；没有手动补节点配置、apply/pull/load，也未改模型/路由/MaaS revision。

真实模型RO/cache RW及身份/认证排除通过；私有TLS同步/异步/下载46行及单次计量通过。worker7 ready、MaaS正常接单、UNKNOWN=0，VM/worker容器未重启。新版快照完整无人值守正常首启门槛本轮通过；母版无需为QA再次更新。旧worker记录保留，双克隆、轮换、独立权重/NFS、容量和E8不因此完成。GPU UUID与旧6相同而VM boot/worker不同，只记录一个再现案例，不证明普适稳定或并发排他。[验收报告](../../results/maas-e5-registry-clone-20261027/REPORT.md)。


## 消费者兼容OCR已交付（2026-10-27）

按下游文档实现POST /ocr与/v1/ocr，接受model+base64/data URL或受限公网HTTP(S)图片URL，返回顶层lines/confidence/polygon；language为auto/zh/en提示、return_boxes默认true、request_id仅日志关联。复用SyncIngress统一鉴权/配额/维护/UNKNOWN，输入准备有界且输出验证先于成功计量。原Chat/异步接口和数据库schema保持。

001 API发布consumer-ocr-r1，仅API重建、HTTP/TLS入口白名单reload；执行器/observer维持e6，GPUStack/worker7/母版及客户监听未改变。新契约四张成功图对应四成功事件，包含空白图[]和真实URL；旧Chat/异步/下载46行及单次计量通过。MaaS恢复接单、UNKNOWN=0、monitor ok。未完成文生图/音视频、E4同步幂等、消费者自身环境联调或新的8小时耐久；公网仍未开放。

本地186通过/56跳过，独立测试库相关测试89通过/1历史harness跳过。后续E8回归应纳入新门面、图片下载DNS固定/TLS和转换前计量约束。[接口契约](../api/CONSUMER-OCR.md) · [验收](../../results/maas-consumer-ocr/REPORT.md) · [发布/回退](../../deploy/maas/CONSUMER-OCR.md)。
