# MaaS 模型副本调节器迁移计划

> **2026-11-13 配置/资源热改实测补齐**：6/6通过。专属注册引擎B的镜像引用/命令/env有原生日志实证，普通修改不暂停、不回滚；单卡3.5→8及8→3.5混合占用均保留旧实例和计算申报，cap4实际2/2（错误重定价会为1或3）；非法/缺失声明停写，修复无需adopt/重启自动恢复；暂停后改B/8并归零保持，resume使用新配置，cap4实际1/1。共9次自动副本更新，本地190通过/3条件跳过。正式仍2/2、gen15、未暂停/正式journal空、UNKNOWN0，原引擎未改；新试点归零恢复基线，旧故障未知记录保留，所有常驻容器未动。镜像只换同digest引用，不是新权重/任意参数升级验证。下一步多模型联动，恢复耗时优化仍暂不开展。[报告](../../results/maas-replica-hot-20261113/REPORT.md) · [HTML](../../results/maas-replica-hot-20261113/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 故障与恢复实测补齐**：5/5 通过：订阅断开退为完整轮询并重连、读取 503 与不完整分页停写/恢复、原生已执行但回复超时后跨进程只读确认、未执行且读到旧目标仍保留未知 journal。每案例请求仅 1 次，前四项各提交原生 1 次、末项 0 次；最后试点 journal 按预期保留，已暂停/Observe、容器停止且 restart=no。正式 OCR 仍 2/2，generation15、正式 journal 空、客户 UNKNOWN0，API/E17/原生/DB 未重启；客户 OCR 唯一计量与日报回归通过。本地183通过/3条件跳过。工具独立运行，常驻镜像仍git-125298e；Pending 恢复耗时优化未开展。下一步补普通配置/资源热改和多模型联动，其他 R4 余项不改判。[报告](../../results/maas-replica-fault-20261112/REPORT.md) · [HTML](../../results/maas-replica-fault-20261112/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 最新常驻验收**：Pending 180 秒观察、Ready/非零下限收敛及持久保持已实现并部署；174 项本地通过、3 条原生环境条件跳过。真实 20→7/7 保留全部 Ready、3→2 Pending 保留下限、资源恢复 3/3、外部数量纠正、全局暂停/恢复及跨容器持久性均通过。API/E17 当前为 `git-125298e`，仅正式 OCR 获写入授权（min1/max2），当前 2/2，试点归零，UNKNOWN=0、PendingWrite 空。客户 OCR 唯一执行/计量与日报查询通过，旧插件 Observe，未重启 VM/worker/Docker。既有 Pending 的原生重调度仍有等待；本轮约 86.51 秒恢复，保留首次 30 秒超时，不宣称立即恢复。下方同日“未部署/未实现”及旧配置为历史，以本条和报告为准。完整故障/冷缓存/热改组合及规模验证仍未覆盖。[部署报告](../../results/maas-replica-pending-20261112/REPORT.md) · [HTML](../../results/maas-replica-pending-20261112/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 最新职责边界（候选已实现，未部署）**：调节器只管理副本数量；外部镜像、启动参数和资源声明变化不回滚、不暂停、不要求重新接管。每轮读取 GPUStack 当前显存声明规划新副本，旧实例仍按原生实际占用核算；v2 策略中的重复显存字段改为可选且不作规划依据。授权范围变化和未知写入保护保留，全局暂停优先，按模型控制仍是后续增强。此前“普通配置变化冻结”的记录已被本决议替代。本轮未连接或修改服务器。[职责与资源口径](REPLICA-RESOURCE-INPUTS.md) · [本地验收](../../results/maas-replica-resources-20261112/REPORT.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 用户确认分阶段范围**：本轮先做整个调节器的暂停、恢复；按模型控制登记为后续增强，排在全局控制真实验收与受控发布之后，不作为本轮发布前置。后续设计覆盖逐部署状态、容量占用、全局暂停优先及模型间互不阻塞，接口和排期另行确定。沿用 [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)，不新建重复 Issue。[决议与范围](REPLICA-CONTROL-API.md)。

> **2026-11-12 前一版控制记录（普通配置冻结规则已被后续决议替代）**：新增运维全局暂停/恢复/查询 API。未暂停的 v2 Apply 根据当前策略和 GPU 容量纠正外部副本数量修改，支持受服务下限保护的逐个缩容；数量变化不再触发旧的人工漂移冻结。镜像等保护配置和未知写入仍阻止动作。接管不隐式解除暂停，恢复须显式 resume；新增模型事件唤醒。141 项调节/E17/插件回归与 18 项客户接口回归通过，3 项环境条件跳过。旧 R4 人工归零停写结果仅作历史证据；新语义尚待独立真实试点和 R5 发布。本轮未连接或修改服务器。[操作与设计](REPLICA-CONTROL-API.md) · [本地验收](../../results/maas-replica-control-20261112/REPORT.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R4 实际 E17 主循环演练通过，原配置已恢复**：实际订阅/写库与 v2 Observe、显式接管、自动 0→1/1→2、暂停跨进程恢复、人工漂移停写、错误接管拒绝均通过。修复 E17 SIGTERM 退出（e447154），候选实际退出码 0；2 次原生 OCR 与 1 个唯一计量的 MaaS 异步任务成功。原 OCR 1/1、试点 0/0、UNKNOWN=0，恢复原 E17 镜像/环境和 v1/revision 7 策略，未正式启用 v2。完整 R4 仍缺隔离通道/在途不确定写入及冷缓存竞争；R5 未发布。[报告](../../results/maas-replica-v2-e17-20261112/REPORT.md) · [HTML](../../results/maas-replica-v2-e17-20261112/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R4 首轮真实试点通过，常驻未切换**：12 个实验、27 次自动 +1 确认、6 个容量点、7 次原生 OCR 与 2 个唯一计量的 MaaS 异步任务均通过。两卡达到 4 副本全部 Running，串行两轮均值 37.85 秒、上限 2 为 21.61 秒（缩短约 42.9%）；上限 4 实际在途仍峰值 2。原业务保持 1/1，原 E17 及 v1/revision 7 策略不变，试点归零、无待确认写入、UNKNOWN=0，临时 key 已撤销。完整 R4 尚缺常驻事件循环、受控冷热缓存及实际故障恢复；R5 未发布。[报告](../../results/maas-replica-v2-r4-20261112/REPORT.md) · [HTML](../../results/maas-replica-v2-r4-20261112/REPORT.html) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R1—R3 代码和 R4 只读前检完成，未发布**：102 项本地回归通过（Mac 缺原生 SDK 的 2 项另在固定 runtime 补验），GPUStack v2.2.3 内 34 项回归全部通过。真实两卡 Ready、原 OCR 仍 1/1；新版 Observe 稳定后计算目标 2，因未接管而保持零写入。已修复原生空 distributed_servers 默认对象导致误冻结的问题。独立实写/启动耗时比较、故障恢复试点及 R5 发布未完成，现网配置与人工目标不变。[实施与证据](../../results/maas-replica-v2-20261112/REPORT.md) · [并行边界](REPLICA-V2-PARALLEL.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R3a 串行写入本地实现，未部署**：v2 按现状执行 +1，不依赖新到达事件；disabled 不要求压力 feed，advisory 的宽限/冷却持久化。新增私有 status/adopt/pause，需明确采用当前目标；确认回执先落盘再清理 PendingWrite，未知结果不重发。实际在途仍限 1，R3b 并行预留和 R4/R5 真实验收发布尚未完成。[操作与边界](REPLICA-V2-APPLY.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R2 只读对账和持久状态基础完成，未部署**：已有 Ready GPU 无新事件也可按完整 API 现状重算；API 故障清空基线，轮询降级、稳定期、结构化私有状态已接入。接管/人工漂移/确认回执的持久函数已测试，真实 writer 和管理命令待 R3，v2 仍禁止发模型写请求。[实现边界与配置](REPLICA-V2-OBSERVE.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 R1 本地代码完成，未部署**：新增 MaaS v2 契约解析、私有只读诊断与结构化压力原因，能复现“旧样本过期 + 新 worker 未覆盖”。旧 gate 冻结语义保持；现有 E17 执行入口尚未接入 v2，不能据此认定周期调节或并行已实现。[契约、命令与测试](REPLICA-V2-CONTRACT.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 启动效率补充，待实现**：调节器还须尽快兑现闲置容量，正常路径采用事件快速唤醒、首副本优先和受预算约束的并行启动；原 +1/在途 1 是保守起步参数。R1—R5 同时纳入首副本/目标容量就绪时间、冷热缓存和并行档对照；只完成安全串行不能宣称效率完成。[补充设计](REPLICA-STARTUP-EFFICIENCY.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

> **2026-11-12 改进设计，待实现**：针对旧采样来源阻塞新 worker 扩容，新增[压力解耦与持续对账方案](REPLICA-RECONCILIATION-IMPROVEMENT.md)。建议新版显式启用周期对账和 advisory 压力模式，缺样走保守串行扩容；旧 schema 严格冻结语义不变。保留资源/授权/在途/未知写入强制门槛，增加持久接管基线和人工修改保护。R1—R5 为后续实现、试点和发布步骤，当前现网配置及人工 1/1 不变。由 [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39) 继续跟踪。下文首发仅到达事件/强制压力门槛的约束属于既有版本。

2026-11-09 · 目标设计，待实施

## 范围与职责

将现有副本调节策略迁到 MaaS 独立后台进程，复用 E17 与调节器都需要的 GPUStack 状态采集。首发保持已验证的 GPU 上线与恢复触发、只增扩容、部署授权和资源压力门槛。GPUStack 继续负责选卡、创建和维护实例、推理路由。自动缩容、归零和 worker 退役沿各自计划处理。

| 组件 | 职责 |
| --- | --- |
| MaaS 共享采集 | 订阅 worker 和模型副本事件，启动、重连和周期读取分页列表做对账。输出带采集时间、范围、会话与完整性状态的快照，包含 worker、GPU UUID、实例及绑定。 |
| MaaS 副本调节器 | 以事件触发重算，使用最新可调度容量、模型配置、显存、网络和磁盘压力及在途预算，计算目标副本数。按授权调用 GPUStack 原生模型更新 API，再读回确认。 |
| E17 日报 | 用实际观测计算在线区间、运行模型与覆盖率。独立处理快照落库、封存和按日期查询。 |
| GPUStack Server | 提供事件、权威资源和部署状态，执行副本放置与实例生命周期。 |

共享的是观测。E17 按 GPUStack 的 ready 状态统计在线，调节器额外校验可达、维护状态、UUID 归属、选择器、声明显存和可放置性。调节器读取 GPUStack 部署配置及目标副本数，发送前再次核对，不使用日报或历史落库结果作为写入依据。采集断线、对账未完成、快照过期或压力覆盖缺失时冻结新增动作。日报记录观测空档，日报任务独立消费，写库慢或查询故障不阻塞调节器。

## 分阶段实施与验收

| 阶段 | 实施内容 | 放行条件 |
| --- | --- | --- |
| 1. 固定对照基线 | 固定当前插件版本、策略及原生管理 API 契约。盘点可复用的纯策略、容量计算、压力检查、写入日志和对账逻辑。核对外部订阅与分页接口在目标组织和集群下的可见范围。 | 用同一组脱敏输入复现插件的目标向量、选卡证据、单轮步幅、冻结原因。覆盖 GPU 替换、过期事件和批量上线。 |
| 2. 共享采集与只读调节 | 在 MaaS 先并行接入共享采集，保持 E17 现有日报契约。让调节器在 Observe 模式计算预览。纯策略复用同一实现，GPUStack 进程内订阅适配改为 MaaS 的外部订阅。 | 新旧输入归一化后，在相同快照、策略和压力样本下得出一致的目标与拦截原因。覆盖分页变化、断线重连、状态乱序、同卡换绑和首次启动。初始全量快照用作基线，不把已有 GPU 当成新上线。E17 核对区间、覆盖率和模型清单。 |
| 3. 隔离环境实写 | 接入专用管理凭据与原生 `PUT /v2/models/{id}`，部署范围和副本上限使用独立授权。持久化在途写入与读回结果。模拟写入超时、进程中断、实例未就绪以及压力过期。 | 从有空闲容量的单部署确认一次受控扩容，实测原生实例就绪与 OCR。超时或中断后先按权威状态和日志对账，结果不明时保持停写。越权、压力门槛关闭或在途耗尽时无写入。 |
| 4. 受控切换 | 在目标环境运行 MaaS Observe，与旧插件并行观察，覆盖真实 GPU 上线和压力变化。将旧插件切至 Observe，确认其修订生效、在途动作和未确认日志已对账，然后只对一个已验收的 OCR 部署开启 MaaS Apply。 | 同一部署始终只有一个副本目标写入方。验证目标变化、实例落点与实际 OCR，核对同步请求、异步任务、监控和 E17 日报。启动时重新对账，切换前的旧上线事件不能触发新写入。 |
| 5. 稳定观察与交接 | 保留插件作为可回退的运行路径。更新启停、权限、告警、备份恢复和回退手册，确认新克隆加入时压力采集覆盖随 worker ID 更新。 | 记录稳定观察期间的扩容触发与读回结果、采集新鲜度、压力覆盖、在途计数、未确认操作和业务探针。通过后再安排插件退役。 |

## 权限与回退

MaaS 调节器默认 Observe。读状态与写部署分别使用专用权限。Apply 同时校验集群、部署归属、允许的部署和副本上限。单实例锁防止 MaaS 调节器内部重复启动，插件到 MaaS 的写入交接另由发布流程控制。

回退时先停 MaaS 写入，保留日志并核清在途请求及 GPUStack 实际副本目标，再恢复旧插件的观察与授权写入。回退沿用当前已确认的副本目标，回退期间继续通过业务探针验证服务。优先在独立测试环境完成验证，生产切换操作限于项目进程、容器与配置，并遵守既有 VM 约束。

相关资料：[目标架构图](MAAS-E17-REPLICA-ARCHITECTURE.drawio) · [现有插件设计](REPLICA-POLICY-PLUGIN.md) · [E17 实施记录](E17-GPU-TIME.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39) · [Issue #45](https://github.com/WTaoGitHub/cybercafe-maas/issues/45)

## 阶段 1 基线

2026-11-09 固定仓库基线 `e8c5b25`，插件源码版本 0.16.0。现网 001 的 Server 镜像标记为 0.14.0。用该镜像内的 GPUStack v2.2.3 运行固定源码的 28 项插件回归，全部通过。现网管理接口只读核对到 4 条 worker 记录、3 张就绪卡、1 个部署和 9 个实例。3 张卡的总显存和已分配显存均为整数，worker 保留显存字段齐全。已验证的是当前可见范围，目标环境仍须单独核对。

迁移应复用策略求解、压力门槛、原生读取和写入日志对账。原插件的事件来源、进程生命周期和 ORM 读取属于适配层。对照验收分别保留固定源码和现网版本标记。

## 阶段 2 只读候选与恢复点

2026-11-09 完成 MaaS Observe 候选。GPUStack 插件和 MaaS 复用同一份容量归一化和规划函数。Observe 使用 E17 的两条订阅触发，以分页管理 API 重读 worker、部署与实例作为规划依据。订阅断开即清空会话，重连后首个可信快照只建立基线。候选只计算和记录预览，Apply 策略由 Observe 配置加载器拒绝。

已在隔离容器通过原插件 28 项回归和 MaaS Observe 3 项测试，覆盖相同容量和规划、缺失或冲突身份拒绝，以及断线重连后重新建立基线。Compose 覆盖配置校验通过。001 两轮只读连接均成功读取 worker 与模型副本流，分页对账各得到 3 张可调度卡、1 个部署和 9 个实例。真实 GPU 到达的并行策略对照仍未执行。

候选使用 `deploy/maas/compose.replica-observe.yaml` 覆盖 E17 服务配置。显式传入 `MAAS_REPLICA_OBSERVE_DIR`，其 `policy.json` 和 `pressure.json` 为容器运行用户可读的私有文件。用含共享规划模块的 MaaS 镜像。在隔离库或目标 Observe 环境启用，避免和同一数据库的现有 E17 采集实例同时运行。阶段 2 本次只运行短时只读探测，未切换现网采集或副本调节。

恢复点为新控制端首台 GPU 克隆就绪后，在其 GPUStack 集群做真实上线的 Observe 对照。实际扩容和 OCR 属于后续隔离实写验收。新控制端首台克隆由管理员提供，见[新控制端上线计划](../../deploy/maas/NEW-PC-ROLLOUT.md)。

## 阶段 3 写入候选

MaaS 复用插件的原生管理适配、压力门槛、在途预算和持久写入日志。Apply 同时要求启用 Compose 写入覆盖、策略处于 Apply、集群和部署在授权清单内，以及压力样本新鲜并覆盖参与规划的全部 worker。首发只允许每次将单个部署的目标增加一个。启用 Apply 后重新建立 GPU 基线，等待新的到达或恢复事件。

发送前再次读取容量和部署，核对配置指纹、已就绪实例和剩余预算，再检查策略、订阅会话、事件时效和压力。请求体保留已核对的部署配置，省略由 GPUStack 维护的就绪副本数。带 LoRA 的部署暂不在此写入范围。发送前持久化写入日志。超时后按部署绑定和目标读回确认。若只读到旧目标，保留待确认日志并暂停后续写入。切回 Observe 后，在保留写入覆盖和日志挂载的条件下仍可继续只读对账。

隔离回归通过 28 项插件用例和 9 项 MaaS 用例，覆盖一次加一、实例待就绪时限流、压力过期、事件过期、响应丢失后进程重建，以及读回旧值时保留日志。用 GPUStack v2.2.3 原生模型校验了 OCR 更新请求字段，Compose 合并校验通过。回归的写入与超时由测试替身模拟。真实 GPU 到达、原生 API 实际更新、GPUStack 调度和 OCR 结果仍待试点验收。已有克隆启动与 OCR 记录可作为环境前置证据，调节器仍需单独记录写入及实例落点。

## 阶段 4、5 执行清单

新 PC 的首轮实测使用 GPUStack 集群 `maas-baremetal-workers`（ID `1`），试点部署为 `maas-ocr`（部署 ID `1`）。首台试点克隆须加入此集群。先核对 worker 的 `cluster_id=1`，再用其 worker ID 核对压力样本覆盖、实例落点和 OCR 结果。首轮验收从 `0 → 1` 开始。当前配置见[新 PC 上线台账](../../deploy/maas/NEW-PC-ROLLOUT.md#副本调节试点接力)。

1. 在试点准备独立 OCR 部署，记录集群、组织、部署 ID、当前目标与就绪数。验证 GPU 到达事件、压力文件的时间戳和新 worker 覆盖。使用同一套只读凭据观察两路策略结果，记录目标、冻结原因及 E17 日报样本。
2. 交接写入权前，将旧插件固定为 Observe，核对已生效的策略、在途实例和待确认写入。在试点期间暂停其他脚本和人工对该部署修改副本目标。GPUStack 更新接口没有比较后写入参数，试点依赖单一写入方。
3. 在 Observe 覆盖基础上叠加 `deploy/maas/compose.replica-apply.yaml`，为 E17 容器挂载只读管理凭据及持久日志目录。管理凭据为运行 UID 可读的私有文件，日志目录由该 UID 持有并设为 `0700`。读取与写入使用同一个 GPUStack 回环 HTTP 地址。先保持策略为 Observe，确认配置、压力源和日志可读，再将单一试点部署设为 Apply 并设置副本上限。
4. 在新的 GPU 到达或恢复后，核对一次目标加一、读回确认、GPUStack 实例就绪及实际 OCR。执行同步入口或原生推理探针和 MaaS 异步业务回归，记录 E17 报表、监控告警与压力覆盖。旧事件、断线和压力缺失期间应无新增写入。
5. 观察期间记录采集新鲜度、压力覆盖、在途数、待确认日志、目标与就绪数差异及探针结果。出现异常先将 MaaS 策略切回 Observe，保留管理读取权限和日志以核清结果。仍无法确认时保留现场并继续冻结写入。对账完成后才可把写入权交还旧插件，沿用 GPUStack 已确认的目标值。稳定观察通过后再安排旧插件退役。

## 2026-11-10 新 PC 首台克隆验证

首台快照克隆通过签名发布 `baremetal-diskless-r1` 的验签和 GPUStack HTTPS 预检。收到启动许可后，E5 引导成功，RTX 4080 SUPER 注册到集群 `1` 的 worker `2`。控制端以固定 SSH 主机公钥和来源受限的采样密钥读取该 worker 的网络接收与 Docker 盘写入速率，压力样本覆盖 worker `2`。

北京时间 17:55:47，MaaS 识别到空集群首次获得可用 GPU。采样覆盖缺失时保持目标为 `0`，进入恢复观察后继续等待。17:57:40，压力门槛开放，MaaS 将 `maas-ocr` 目标从 `0` 调至 `1` 并读回确认，待确认日志为空。GPUStack 创建实例 `1`，落到 worker `2` 的 GPU `0`。

首次就绪探测时，实例停在 `quay.io/gpustack/runtime:pause` 拉取。新 PC 上线台账记录从旧控制端缓存补齐该镜像。随后同一实例达到 Running，模型为 `1/1`，worker 本机 CUDA OCR 返回 `200` 和 2 行。 [辅助镜像 #57](https://github.com/WTaoGitHub/cybercafe-maas/issues/57) · [上线台账](../../deploy/maas/NEW-PC-ROLLOUT.md)

MaaS 异步 OCR 提交返回 `202`，任务最终为 `FAILED / UPSTREAM_REJECTED`。同一试点推理凭据可以列出模型，GPUStack 服务端 `/token-auth` 返回 `200`，两个原生推理入口均返回 `401`。首次扩容已验证，端到端 OCR 待上游鉴权修复后复验。 [调节器 #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39) · [推理鉴权 #58](https://github.com/WTaoGitHub/cybercafe-maas/issues/58)

### 推理鉴权排查

同一试点凭据和图片经 Higress 返回 `401`，经 GPUStack Server 容器内回环原生 API 返回 `200` 和两行。GPUStack v2.2.3 的 Higress 认证回调为模型路由返回集群注册码，而原生 API 转发使用已选 worker 的会话凭据。两条路径的凭据口径差异与无盘 worker 的现象吻合。

隔离试验使用当前 Server 镜像、临时数据库和 `--network none`，未达到原生 API 就绪。诊断日志显示 Server 在迁移及扩展加载后因找不到非回环 IPv4 退出。后续应改用不发布端口的独立内部 bridge，验证禁用嵌入式网关时的 HTTPS 注册、隧道、按模型鉴权及推理，再决定是否切换。现网入口未切换，端到端验收仍待完成。 [鉴权问题 #58](https://github.com/WTaoGitHub/cybercafe-maas/issues/58)

2026-11-10 使用独立内部 Docker bridge、临时数据库和无端口发布的同版镜像复验。禁用嵌入式 Higress 后，原生 Uvicorn 的版本和健康接口均返回 `200`，匿名模型列表及推理请求均返回 `401`。启用受管注册后，`verify_p2.py` 的七项检查全部通过，包括合成 worker 原生注册、单次许可、旧凭据拒绝、心跳和普通 worker 注册。隔离资源已清理。普通 HTTP GET `/connect` 返回 `404`，未执行 WebSocket 握手，也未在隔离环境运行真实 GPU 推理。切换前仍须验证实际 HTTPS 隧道与授权推理。 [鉴权问题 #58](https://github.com/WTaoGitHub/cybercafe-maas/issues/58)

同版候选在独立内部 bridge 加入临时 Nginx TLS 入口，未发布宿主机端口。合成 worker 接续后，客户端校验证书并通过 `wss://…/connect` 使用新会话凭据完成握手及 ping/pong，旧凭据被拒绝。原七项注册回归同时通过，临时资源已清理。此轮验证覆盖 HTTPS 隧道握手和凭据轮换，真实 GPU 推理流量与 MaaS 计量仍待候选拓扑验证。现网入口未切换。

### 新 PC 试点切换验收

新 PC 在空闲时段备份 GPUStack PostgreSQL 并校验归档目录，保留原 Higress 容器。首轮探测遇到启动期间连接重置，自动恢复原容器。第二轮容忍启动阶段短暂断连，同一私有回环端口切换到禁用 Higress 的 GPUStack v2.2.3，沿用原网络、数据库和 worker 身份。worker 2 恢复 Ready，部署 1 保持 1/1。匿名模型查询返回 401，原生授权 CUDA OCR 返回 200 和两行。MaaS 异步 OCR 为 SUCCEEDED，返回两行，数据库核对一条成功用量、数量 1，UNKNOWN 为 0。两项控制面健康检查返回 200，压力采集活跃。现用候选容器保留原服务名称，停用的 Higress 容器保留作回退。未接入第二台 worker。 [鉴权问题 #58](https://github.com/WTaoGitHub/cybercafe-maas/issues/58)

### 2026-11-11 Higress direct 候选

原生 proxy_mode=direct 使 Higress 模型后端指向实际实例端口。独立 GPUStack/Higress 环境使用合成 worker 和实例元数据调用真实 GPU OCR，授权请求 200、两行，匿名和错误 Key 均 401，无模型权限 Key 为 403。原生配置及 Envoy 实际端点已交叉核对，旧控制端直连实例端口失败。隔离资源已清理，常驻原生 API 及 1/1 部署健康。下一轮先验证真实试点克隆由集群 worker_config 获取 direct、注册与路由刷新，再验证多副本和性能。 [报告](../../results/maas-higress-direct-20261111/REPORT.md) · [Issue #59](https://github.com/WTaoGitHub/cybercafe-maas/issues/59)

2026-11-11 已备份新 PC 的集群配置，经原生 API 将集群 1 的 worker_config.proxy_mode 设为 direct，读回一致，并核对其他配置字段。该默认值由 worker 启动注册时读取，当前 worker 2 仍为 Ready / worker，OCR 为 1/1。管理员暂时无法提供新克隆，真实启动验收等待管理员响应，常驻入口继续使用已验收的原生 API。新克隆就绪后先核验身份、注册返回的配置及上报模式，再以当前 enrollment 版本镜像准备 Higress 候选，验证实例后端、授权与拒绝请求、MaaS 异步结果和单次计量。历史 Higress 回退容器的 enrollment 版本须重新核对后再使用。

用户随后安排先排空 004。已拦截 HTTP/HTTPS 新提交，队列及在途活动清零后暂停 MaaS，generation 1。两个调节器均为 Observe，OCR 0/0、实例清空，004 worker 停止且 GPU 无计算进程，原生状态已为 not_ready。集群默认 direct 和同 GPU 接续授权就绪，压力采样计划性停止。配置、身份与数据库备份已保存到 Mac，等待管理员应用新快照。接手从[维护交接](../../results/maas-higress-direct-20261111/MAINTENANCE-HANDOFF.md)继续。

### 真实 direct 克隆与常驻 Higress 验收

2026-11-11，新 GPU 克隆以 worker 3 加入集群 1，实际上报 Ready / direct。Observe 期间受控恢复部署 1 至 1/1，实例 2 在新 worker 上 Running。当前 enrollment 镜像切换为 embedded Higress，Envoy 后端与真实实例端口一致。匿名与错误 Key 返回 401、无模型权限返回 403；授权 CUDA OCR 返回 200、两行。MaaS 恢复探针通过，异步任务 SUCCEEDED，成功计量一条、数量 1，UNKNOWN 为 0，HTTP/HTTPS 私有入口恢复。

压力采样已改为 worker 3，固定主机公钥和来源受限命令核验通过。MaaS 恢复 Apply 修订 3，仍只授权集群 1、部署 1、最多 1 副本，待确认日志为空；旧插件保持 Observe 修订 1。此次 0→1 为受控手动恢复，未新增调节器自动扩容结论。不同 GPU 注册不覆盖同卡接续，首次主机名时序、多 worker、实例重建与性能对照仍待后续。备份已在 Mac 校验，原生 API 同版容器保留且停止。见[真实验收](../../results/maas-higress-direct-20261111/LIVE-WORKER3.md)与 [Issue #59](https://github.com/WTaoGitHub/cybercafe-maas/issues/59)。

### 003 排空与自动恢复待验收

第二台 VPC 的下一轮准备已完成：策略 Apply 修订 7、策略及运行时写授权上限均提高为 2，单次增加及在途上限仍为 1；重建同镜像 E17 重新建立单卡事件基线，未沿用旧恢复事件，当前仍为 1/1，压力 open、执行 idle，等待第二台。双节点自动 1→2 尚未验收。[准备记录](../../results/maas-replica-003-restart-20261111/CAP2-READY.md)。

本轮后续已通过真实恢复验收：003 以同一 GPU、新 boot ID 恢复 worker 3 Ready/direct；补齐压力采样后，由 capacity_restored 自动 0→1，经过缺失冻结、恢复观察及 open 后写入 confirmed。实例 3 Running，Higress/OCR 两行、MaaS 异步 SUCCEEDED、唯一成功用量 1、UNKNOWN=0。MaaS generation 4 和入口已恢复，Apply 修订 5、上限 1 保留。第二台 VPC 的 1→2 尚未开展。见同一交接页 recovery-validation.json；以下为准备阶段历史。

用户明确本会话聚焦规模调节器，后续验证均围绕自动副本调整，不独立开展 E5 同卡接续验收。先安排管理员使用相同快照启动 003，再单独准备第二台 VPC。

2026-11-11 已确认 003 对应集群 1 的 worker 3。保存 GPU UUID、Linux boot ID 和旧原生 worker 凭据至 Mac 私有目录，复制并校验两套数据库及配置备份。拦截新提交、排空 MaaS 后暂停，generation 3；调节器先切 Observe 修订 4，再将 OCR 降至 0/0，确认实例与 GPU 计算进程均为 0 后停止 worker 容器。VM 未重启，worker 3 记录保留且已由原生机制进入 not_ready。

为下一轮真实容量恢复事件重新启用 Apply 修订 5，仍只授权部署 1、最多 1 副本，空 GPU 基线和待确认日志为空已核对；模型目标保持 0，不手动恢复。压力采样计划性停止。管理员启动后先满足 Ready 与有效压力样本前置条件，核对调节器自动 0→1、原生实例就绪、Higress 推理和唯一 MaaS 成功计量。当前仅准备完成，自动恢复尚未验收。第二台 VPC 的 1→2 留待本轮完成后，同时调整策略与写入授权并重建事件基线。见[003 交接](../../results/maas-replica-003-restart-20261111/HANDOFF.md) · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。
