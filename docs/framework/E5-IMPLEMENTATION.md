# E5修订六开发计划

2026-10-23。当前只设计；代码未实现、未部署。以 [E5-DESIGN.md](E5-DESIGN.md) 的无数据库统一配置读取为基线，每个独立feature完成必要验证后立即独立Git提交，不push。

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
