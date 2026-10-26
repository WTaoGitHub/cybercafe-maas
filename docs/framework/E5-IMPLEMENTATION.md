# E5修订六开发计划

2026-10-23。当前已进入实现；分项进度以文末最新实现状态为准。以 [E5-DESIGN.md](E5-DESIGN.md) 的无数据库统一配置读取为基线，每个独立feature完成必要验证后立即独立Git提交，不push。

## feature顺序与最小交付

| 顺序 | feature | 代码/交付建议 | 必要验证 |
|---|---|---|---|
| F0 | 固定原生行为与配置协议 | 独立223实验记录、manifest schema、错误码/共享码scope、受保护原生token字段及版本策略 | 干净目录首次注册、同目录恢复、不同目录/名称两worker、凭据失败和注册确认方法；不以模拟证明原生 |
| F1 | 版本文件发布与读取 | 独立maas_bootstrap包内ReleaseStore/ConfigResolver、校验和签名、CLI publish/rollback；root-only私有配置 | 不可变revision、原子切换失败不损坏active、错image/摘要/签名拒绝；无session/DB模块 |
| F2 | 共享码访问HTTP服务 | ConfigAccessAuthenticator静态接口、hash校验、site/batch/active/到期策略、GET当前配置、限速/脱敏/不缓存响应 | 错码/越scope/停码拒绝，多客户端同码同revision，日志无秘密；替换测试auth实现不改resolver |
| F3 | 配置拉取与本机应用 | EndpointResolver、CredentialProvider、Verifier、原子文件和进程锁、apply/status命令 | 正常TLS及无限期时间值兼容、失败保留已有配置、并发BUSY、重复UNCHANGED、无秘密shell插值 |
| F4 | worker启动与核对 | NativeWorkerLauncher/Probe、boot/manual同入口、systemd成功依赖、本地脱敏状态 | 原生注册确认方法实测后接入；无worker才启动，运行期有影响变更NEEDS_MAINTENANCE，确认不明不盲重建 |
| F5 | 母版构建与封版工具 | image-prep固定版本/摘要、临时root支持、启动单元/独立地址码证书、污染扫描manifest | 受限临时root构建、不复制原生UUID/token/固定worker名、不破坏管理员授权、秘密扫描脱敏 |
| F6 | 控制端部署与运维适配 | 独立Compose/服务单元、001角色切换脚本及旧维护工具约束、网络/TLS发布工具 | 软件测试通过后才受控维护；保留全部控制/MaaS数据，无VM或引擎重启，回退按原生状态 |
| F7 | 新快照/真实克隆验收及手册 | 供应商交付清单、测试矩阵、证据报告 | 新版A/B同码同配置/独立worker、GPU容器/隔离模型和故障用例，非tower宿主替代 |

模块可在一个小包/进程实现，接口隔离不意味着拆多个网络服务。依赖优先复用已有Python工具链，实施时核对实际版本；不为文件读取增加PostgreSQL或消息队列。配置服务的“最多VM数/每VM会话/原生授权代理”不进入以上feature。

## 依赖和可并行准备

- F0确认GPUStack223正常注册和凭据范围，未证明逐VM权限不阻塞受限共享码试点，但必须明确统一token的实际暴露范围、独立无模型测试cluster。
- F1–F3本地模拟可推进；F4依赖F0原生核对能力，F5依赖F3/F4启动契约。模拟结果与真实GPU/原生证据分别报告。
- F6必须在本地和隔离测试后进行；001当前承载的实际OCR实例要先排空处理，不直接停worker，不混淆MaaS任务执行器。
- F7依赖供应商保存新版快照、新建独立VM和访问权限；不把缺平台操作默认为允许重启001002。不等待一次性启动注入、GPU资源DB/会话认证设计完成。
- TLS、注册码/原生秘密不入Git；示例使用明确占位值。地址专用端口部署前核对，签名密钥/证书尚未生成。

## 交付和检查

先本地单元/接口/进程测试实际不变量，再有界独立223/新克隆验证。重用测试集而不为纯文档造实现镜像测试。验证范围包括鉴权/秘密泄漏、原子发布、互斥幂等、native失败恢复、HTTPS错误、依赖检查、维护门槛与控制服务宕机不影响运行worker。

E5成功标准是同批克隆读取相同配置并原生自动注册，不是实现中央VM台账。独立会话/凭证平台移至[E5.F2](ENHANCEMENTS.md#e5f2凭证管理与自动分发增强)，GPU资源历史移至[E5.F3](ENHANCEMENTS.md#e5f3gpu资源vm与原生worker关联)。详细拓扑及T01–T14见 [部署测试](E5-TEST-DEPLOYMENT.md)。


## 云备份扩展点预留

F3仅预留cloud_backup.url=`http://configuration.cybercafe.phanlink.cn`、enabled=false及EndpointResolver扩展接口，不实现云客户端、同步、重试或切换。配置读取/主源失败测试应断言disabled入口零请求、无秘密转发；不依赖真实域名可达。云端部署、证书和启用策略后续单列，不计入F0–F7交付。


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


## 管理员交接点（2026-10-23确认）

制作新快照、创建并首次启动独立克隆A/B，必须由管理员在云网吧平台操作，不属于自动部署脚本的执行范围。

| 阶段 | 负责人 | 交付/准入条件 |
|---|---|---|
| 快照前准备 | 我们负责业务层准备，与管理员核对镜像条件 | 核对旧启动钩子、依赖/固定镜像缓存、首启身份初始化；安装客户端/配置/信任材料，核对母版未注册worker、无实例身份污染；当前尚未完成实际母版准备 |
| 快照与新VM | 管理员 | 准备验收通过后保存新版快照，记录快照版本；由同一快照创建并启动独立A/B，每台绑定GPU；不得重建/还原/重启001002 |
| 克隆交接 | 管理员 | 提供A/B名称、IP、SSH入口及安全提供的访问方式、所用快照版本、GPU分配信息；无需在聊天中提供密码或私钥 |
| 真实场景验收 | 我们 | 只在新克隆核对18840/18080可达、自动注册、原生身份独立、GPU容器/模型请求及手动apply；保存证据与问题清单 |

快照前准备完成后明确通知“可制作快照”，在管理员交付A/B前，真实首启和双克隆验收保持待执行。等待期间可继续独立的软件、文档和旧运维工具适配，不把模拟测试视作真实启动通过；不自动执行任何平台快照/创建VM操作。


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


## 2026-10-26后续增强：角色驱动的worker启动保护

新增[E5.F5](ENHANCEMENTS.md#e5-f5-role-based-guard)：以明确节点角色/启动许可替代固定名称与IP黑名单，继续保护控制端和母版，避免克隆复用旧002地址时误拦截。状态为后续设计/排期，未实现、未部署，不设为本轮完整改造前置；当前002复用的最小兼容修正仍需独立处理，不能因登记增强就认定保护已经修改。

方案必须处理template角色随快照继承的问题，只依赖母版→快照→克隆及我们可控流程，不预设供应商API、可信注入或独立系统身份。此保护用于运维防误操作，不是可信设备认证，也不改变001002禁止VM重启的约定。角色不明时给出可解释状态，不自动修改角色、停止服务或重启VM。完整范围与验收见增强条目。


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


## 2026-10-26：004网络修复与手动接入验收

001持久化策略新增native_server_cidrs=["10.11.31.0/24"]，支持后续多个IPv4 CIDR。仅在001局域网接口放行到10.11.31.1:18080/tcp：raw开机过滤、HOST和DOCKER链均受控；Docker按conntrack原始目的地址/端口匹配，未扩大所有端口或容器直连许可。原配置18840规则保留；其他业务端口、IPv6和SSH范围不变。重用原boot/refresh服务实现持久化，未重启系统或Docker。策略源码及配置已备份至Mac和001私有目录。

004实测18840/18080连通，原生/v2/workers未认证返回401，18834/8820/11435仍超时；修复前18080超时，修复后连通，完成实际网络对照。refresh再次执行成功，边界rules_ok/inventory_matches和IPv4/IPv6早期hook验证通过；实际整机重启持久性未测试且不得在001002执行。

首次人工apply实际注册了worker ID4至test1(cluster3)，原生状态ready/unreachable=false，但E5误报REGISTRATION_UNCONFIRMED。查v2.2.3 WorkerPublic定义，worker_uuid明确exclude=True，不会在列表响应中出现。确认逻辑已修复为使用本机原生worker token访问/v2/workers?me=true，严格要求单条记录、me=true、集群及本机名称匹配；本机UUID/token/名称文件仍须存在且有效。不是忽略身份或改用管理员token，也不重放注册接口。

修复仅替换004与当前tower模块中的确认条件，分别保持各自名称保护版本；004仍未安装“放行002/保护tower-*”改动。Mac保存修改前模块。之后apply返回APPLIED/ready，再次apply返回UNCHANGED，容器ID/启动时间未变，未重复创建worker。容器内nvidia-smi正常识别RTX4080 SUPER/580.95.05。001反向10150和实际metrics_port=10151可达；之前探测10161拒绝仅说明该端口不是当前实际指标端口。bootstrap40项、边界相关7项测试通过。

第一版首启失败证据保留，本次只判“网络修复后的手动注册及重复应用成功”；未做新快照首启、双克隆独立性或模型推理验收。原systemd failed为首启历史状态，手动CLI成功不自动清空。当前tower文件含修正，管理员保存的第一版镜像仍不含它。下一步按用户计划评估安装剩余保护补丁，再由管理员保存新版快照并新建独立克隆验证自动接入；不要重启或重建001002。

[网络修复证据](../../results/maas-e5-preparation/native-network-fix-20261026.json) · [手动接入与确认修复证据](../../results/maas-e5-preparation/clone-004-manual-registration-20261026.json)。


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
