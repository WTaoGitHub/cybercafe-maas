# 新 PC 控制端上线计划与执行台账

更新日期 2026-11-08（北京时间）。**P1 已完成。接手从 P2 开始，先核对母版交付包、批准首台新 GPU worker 的来源地址，并与网吧管理员安排封版和首启。**

## 目标和约束

- 新实体机全新部署 GPUStack Server 和 MaaS 控制端。推理由新 GPU worker VPC 承担。覆盖 PostgreSQL、任务与 artifact 存储、执行器、私有入口、镜像仓库、bootstrap、GPU 用量采样与日报、监控及备份。旧 `kc-e2e` 下游栈留在原处，联调时由下游更新私有入口。
- 旧环境在并行部署、首启验收和切流验证期间继续服务。新环境使用独立数据库、卷、项目绑定和凭据。切流前确认旧任务、结果和用量历史的保留或交付方式。任何历史导入另行制定配套恢复方案。
- 四台现有 VPC 停机或重启后会失联。全过程保持它们在线。`XZ-31-001`、`XZ-31-002` 的永久禁止整机重启及等效操作约束见根目录 `AGENTS.md`。旧服务退役限于项目进程或容器。原 worker VPC 回收不属于本次切流操作，`XZ-31-002` 的最终处置必须先解决与现有约束的冲突。
- 母版由网吧管理员保存快照并启动新 VPC。仓库只记录角色、版本、脱敏结果和私有记录引用。连接信息、凭据、签名私钥、证书私钥及原始日志保存在受控位置。参见[私有连接约定](../../docs/handbooks/PRIVATE-CONNECTIONS.md)。

## 执行阶段与验收门槛

| 阶段 | 工作和通过条件 | 状态 |
| --- | --- | --- |
| P0 预检 | 已通过现控制端跳转，以公钥登录新 PC。新 PC 为 Ubuntu 24.04.3 LTS / x86_64，8 逻辑 CPU、约 15 GiB RAM、约 433 GiB 可用空间。只看到 Intel 核显，预检时未装 Docker。三台旧 GPU worker 都能访问新 PC 的临时 HTTP 测试端口，回程到 `XZ-31-002` 受其控制端白名单限制。 | 只读预检完成 |
| P1 新控制端 | 重新核对目标身份、私网地址稳定性、资源及现有端口。安装 Docker/Compose，锁定源码提交和镜像版本，以 GPUStack v2.2.3 为兼容基线。创建独立状态和私有访问边界，建立新 registry、bootstrap、MaaS 数据库、artifact 卷和执行器。启动采样、日报、监控与备份。核对数据库迁移、应用健康、registry TLS、bootstrap 签名及拒绝未授权访问。副本调节器先用 Observe，退役自动化先关闭。 | 完成，空集群及母版私网访问已验 |
| P2 母版与首台新 worker | 审查母版 GPUStack、bootstrap、registry 地址、证书 SAN、签名信任及防火墙控制端白名单。母版不复制既有 worker 身份或维护凭据。管理员封版后先启动一台带 NVIDIA GPU 的普通克隆。验证 NVIDIA、首次自动注册、冷拉取、双向端口、未授权来源拒绝、原生 GPUStack 推理和 MaaS 真实 OCR。 | 下一阶段，待管理员封版及首台新 worker |
| P3 扩至目标容量 | 按计划启用新 GPU worker，逐台验收独立身份、Ready、镜像 digest、单节点 OCR 和监控覆盖。按现网基线核对副本与并发，记录吞吐、延迟、失败率和 `UNKNOWN`。从下游实际运行容器验证新私有入口、TLS、鉴权、幂等提交、状态轮询、结果下载和用量。先完成备份恢复演练并确认旧历史处理方式。 | 待 P2 |
| P4 下游切流 | 确认窗口和回退负责人。停止旧入口接受新业务请求，核对队列、运行中任务、同步请求及 `UNKNOWN`，保留状态查询与结果下载直至旧任务按约定处理完毕。切换下游地址与凭据，做真实 OCR 和用量探针，观察达到约定门槛后停止旧项目服务。保留旧卷、配置和入口恢复步骤。 | 待 P3 和切流窗口 |
| P5 旧环境退役 | 记录稳定观察结果、备份去向和剩余历史访问方式。旧 worker VPC 的不可逆处置由管理员另行安排，并遵守上面的 VPC 约束。 | 待 P4 和单独处置决定 |

安装参考[实体机手册](BARE-METAL.md)。该手册的同机 NVIDIA worker 步骤适用于具备 GPU 的主机，本次用独立新 GPU VPC 替代。其他参考包括[母版网络隔离](../bootstrap/clone/network/README.md)、[仓库服务](REGISTRY-SERVICE.md)、[下游容器入口](CONTAINER-HTTP.md)、[备份与恢复](../../docs/handbooks/OPERATIONS.md#8-备份异机保管与恢复)和[消费者 OCR 契约](../../docs/api/CONSUMER-OCR.md)。切流以实际运行契约和下游验收为准，尤其核对快捷 OCR 接口的同步或异步响应。

## 切流回退

旧入口先拦截新提交和同步推理，让已入队任务继续派发直到排空。MaaS 的 `pause` 会停止派发，不能单独用它拦截异步提交或排空等待队列。

新入口联调失败、任一目标 worker 无法稳定推理、任务无法排空或出现未核实的 `UNKNOWN` 时，暂停切换并保留旧服务。若切换后出现故障，先阻止新环境继续接收任务，记录已接收的任务、结果及计量归属，再恢复旧入口和下游配置并验证真实 OCR。两套独立数据库的任务和幂等键各自生效，已接收的请求先按所属环境核对再决定如何处理，避免重复执行或计量。

## 接力与执行记录

新的 Codex 会话先读根目录 `AGENTS.md`、本页和最新一条台账。执行前重新只读核对目标主机、旧服务状态和当前阶段。阶段完成后更新上方状态和下一步，按仓库约定提交、推送，并更新已有 GitHub Issue。

### P1 已部署和恢复点

- 新 PC 以 `/srv/cybercafe-maas/releases/2efbb301ec2696cf2fe288c8e9c03c1a8bfb488b` 保存本轮部署源码，运行状态在同一根目录的 `control/` 下。Compose 项目为 `maas-control`，使用 `control/app.env`。Docker 29.1.3、Compose 2.40.3 来自 Ubuntu 包源。保留新 PC 现有静态地址设置，未重启主机。
- Docker Hub 拉取和 Quay 大镜像直拉超时。旧控制端只读导出已缓存镜像，未传输旧卷、数据库或凭据。Python、PostgreSQL、registry 和 Nginx 镜像在目标机导出复核，配置摘要与来源一致。GPUStack 镜像层摘要与来源一致，启动后报告 v2.2.3。其副本调节器为 0.14.0，未配置模型策略及 Apply 写权限，退役插件为 `off`。MaaS 从锁定提交构建，镜像 `cybercafe-maas-tasks:git-2efbb30` 的目标机 ID 为 `sha256:5637400e83b2032053a7e640261e113c03ba0bed01105a89d6e677330bdebfb2`。
- 新 GPUStack 使用独立卷并已更换管理员初始密码。新 MaaS DB 完成 8 项迁移，API 健康，单执行器与 observer 已运行，observer 无告警。E17 采样及日报观察记录已写库，目前是空集群，尚无封存日报。E17 使用 30 天有效期的独立 management key。MaaS 推理绑定保持空，入口匿名请求返回 401。
- Registry TLS 验证通过，匿名读 401、reader 读 200、reader 写 401。Bootstrap HTTPS 健康检查及匿名拒绝通过，独立 Ed25519 密钥已验签。基础部署先只绑定回环，后续受限私网入口及签名发布见下节。
- 已生成新 MaaS 空环境的本地 DB 与 artifact 基线备份。校验和通过，DB 已还原到临时测试库并核对 8 项迁移。该证据只覆盖空环境，完整发布态恢复演练留到切流前。

### P1 私网入口与发布准备

- 新 GPUStack Docker 集群 `maas-baremetal-workers` 和 OCR 部署 `maas-ocr` 已建，部署、副本调节器均按新环境的实际集群和部署 ID 配置。OCR 期望和就绪副本均为 0。原生路由要求鉴权，独立非管理员推理 key 已绑定新 MaaS 项目，能力接口已验证。真实推理等 P2。
- 新仓库的 OCR 镜像 `maas/ocr:registry-r1` 经临时 SSH 隧道从原仓库复制，`skopeo copy --preserve-digests` 后核对两端原始 manifest 的摘要均为 `sha256:9ec6c93fd35ecf2bfd1a9d842cf7afab4276cff6475e5afe8d56f43acbdca9d9`。原生后端按摘要固定镜像。临时隧道已关闭，旧仓库未改。
- Bootstrap 签名发布 `baremetal-pilot-r1` 已生效，以新集群的注册令牌和新仓库的只读凭据组装。已用项目克隆客户端经验证的 HTTPS 取回并验签。母版仍使用原设置。五文件交付包在新 PC 的 `control/bootstrap/operator/baremetal-pilot-mother/`，权限仅 root，包含 `bootstrap.json`、`clone-boundary.json`、`server.crt`、`release-signing.pub` 和 `shared-code`。母版变更前备份原五文件，只安装并核对配置及信任材料，母版自身保持 worker 与边界服务不运行。只有管理员保存快照并启动新 VPC。
- 新 PC 的 `maas-control-worker-edge` 将三个 worker 入口转发到本机回环，由 `control/worker-edge/nginx.conf` 限定来源。目前只允许已验证的母版地址。母版访问 GPUStack 成功，registry 与 bootstrap 的证书及预期 HTTP 状态通过，旧控制端访问三端口均被拒绝。MaaS 消费者入口和 PostgreSQL 继续绑定回环。P2 为首台克隆核验其源地址，添加精确允许规则，再验证首启。若只能在克隆启动后获知地址，放行后按引导手册做受控重试，并如实记录首次启动结果。
- 新 PC 当前运行 `maas-control-gpustack-observe`，已记录实际 Observe 计划，退役插件关闭。原 `maas-control-gpustack` 容器停在新 PC 上作为回退锚点，两者共享新 GPUStack 卷，切换时只运行其中一个。旧控制端的 GPUStack 和 MaaS 健康端点通过复核，未切流。

每次会话追加一行，包含北京时间、阶段、操作范围、验证结果、脱敏证据或变更提交、阻塞及下一步。长报告放在 `results/` 并在此链接。只有满足该阶段门槛才标记通过。若中断，注明最后一次成功操作和恢复点。

| 日期（北京时间） | 阶段 | 操作及结果 | 证据、阻塞与下一步 |
| --- | --- | --- | --- |
| 2026-11-07 | P0 | SSH、主机资源、旧服务及双向网络只读预检。临时 HTTP 监听收到三台旧 worker 的请求并已退出。`XZ-31-002` 防火墙仅放行旧控制端，其余两台旧 worker 的回程端口可达。母版已安装通用网络隔离配置，未在母版应用。 | 新克隆必须验证新控制端白名单及快照继承。预检未进行 GPUStack 注册或真实推理。 |
| 2026-11-08 | P0 | 登记新控制端并行部署、管理员封版、先验收再切流及回退顺序。本次只编辑仓库文档。 | 下一步 P1。复用现有 [控制端迁移 #14](https://github.com/WTaoGitHub/cybercafe-maas/issues/14)、[快照首启 #20](https://github.com/WTaoGitHub/cybercafe-maas/issues/20)及[实体机验收 #48](https://github.com/WTaoGitHub/cybercafe-maas/issues/48)。 |
| 2026-11-08 | P1 | 安装 Docker/Compose，部署新控制端回环实例，验证鉴权、数据库迁移、空集群采样及基线恢复。复查旧 GPUStack 和 MaaS 健康端点正常。 | 恢复点见上方。确认受信来源后继续 P1，未进入 P2。 |
| 2026-11-08 | P1 | 完成受限私网入口、OCR 镜像复制、新集群零副本配置、签名发布及 Observe 验证。准备母版交付包，复查旧端健康。 | P1 通过。P2 从母版交付包复核、管理员封版及首台克隆来源准入继续。尚无新 GPU worker 或真实 OCR 证据。 |
