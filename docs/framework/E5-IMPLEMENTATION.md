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
