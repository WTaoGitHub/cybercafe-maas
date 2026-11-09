# 新 PC 控制端上线计划与执行台账

更新日期 2026-11-09（北京时间）。**P1 已完成。接手从 P2 开始，先核对母版交付包、批准首台新 GPU worker 的来源地址，并与网吧管理员安排封版和首启。**

## 目标和约束

- 新实体机全新部署 GPUStack Server 和 MaaS 控制端。推理由新 GPU worker VPC 承担。覆盖 PostgreSQL、任务与 artifact 存储、执行器、私有入口、镜像仓库、bootstrap、GPU 用量采样与日报、监控及备份。`kc-e2e` 下游容器目前仍在 XZ-31-001，后续也将迁到新 PC，与 MaaS 同机部署。下游迁移尚未实施。
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
| P4 下游迁移和切流 | 确认窗口和回退负责人。准备新 PC 上的下游容器和同机入口，通过下方验收后切换业务。停止旧入口接受新业务请求，核对队列、运行中任务、同步请求及 `UNKNOWN`，保留状态查询与结果下载直至旧任务按约定处理完毕。观察达到约定门槛后停止旧项目服务。保留旧卷、配置和入口恢复步骤。 | 待 P3 和切流窗口 |
| P5 旧环境退役 | 记录稳定观察结果、备份去向和剩余历史访问方式。旧 worker VPC 的不可逆处置由管理员另行安排，并遵守上面的 VPC 约束。 | 待 P4 和单独处置决定 |

安装参考[实体机手册](BARE-METAL.md)。该手册的同机 NVIDIA worker 步骤适用于具备 GPU 的主机，本次用独立新 GPU VPC 替代。其他参考包括[母版网络隔离](../bootstrap/clone/network/README.md)、[仓库服务](REGISTRY-SERVICE.md)、[下游容器入口](CONTAINER-HTTP.md)、[备份与恢复](../../docs/handbooks/OPERATIONS.md#8-备份异机保管与恢复)和[消费者 OCR 契约](../../docs/api/CONSUMER-OCR.md)。切流以实际运行契约和下游验收为准，尤其核对快捷 OCR 接口的同步或异步响应。

## 下游同机接入验收

按新 PC 实际部署的容器网络设计消费者入口。若选用 Docker 网关入口，按已批准的网络名称解析实际网桥，核对子网和网关，限制放行到消费者端口并保留 MaaS 鉴权。新 PC 需单独配置和验收这一行为。旧机的防火墙脚本和规则绑定其宿主机环境。

在独立环境重建同名、同子网和同网关的下游网络，确认网桥名变化后入口可恢复。验收时从实际下游业务容器提交 `POST /maas/v1/ocr`，验证鉴权、`202` 受理、任务成功、识别结果和用量。确认未授权来源及内部端口仍受限。参考[旧机网桥故障与修复 #51](https://github.com/WTaoGitHub/cybercafe-maas/issues/51)。

方案 B 在新 PC 的临时网络上通过连通性验证。测试下游通过 Docker DNS 访问 `http://maas-edge:18831`。代理容器不发布宿主机端口，通过仅挂载给代理的 Unix socket 转发到现有回环 API。匿名能力查询返回 `401`，带测试凭据返回 `200`，无效图片提交返回 `422 IMAGE_BASE64`。重建代理和网桥后重复通过，其他 Docker 网络无法通过名称或代理 IP 连入。双网卡测试容器保留原业务默认路由，代理换 IP 后重新发起请求仍能用原名称连接。临时资源已清理，现有控制面未重启。正式接入仍需部署 socket 转发服务，核对下游地址可配置及其客户端重连行为，并在 Worker 就绪后用实际下游应用验收 OCR。

### 方案 B 正式入口

新 PC 已创建独立的内部 Docker 网络 `maas-consumer`。由 MaaS 的 systemd 服务管理代理容器，并在该网络上登记别名 `maas-edge`。代理通过 `maas-consumer-api.socket` 转到本机回环 MaaS API。消费者端口无宿主机映射。现有运维入口和 Worker 私网入口继续独立运行。

迁移下游时，在其 Compose 中把 `maas-consumer` 声明为 `external: true`，只给调用 OCR 的后端服务追加该网络，同时保留其原业务网络。MaaS 地址配置为 `http://maas-edge:18831`，凭据使用新控制端登记的服务凭据。两套部署各自管理容器，专用网络由宿主机管理。

部署配置保存在新 PC 的 `/srv/cybercafe-maas/control/consumer-edge/`。Nginx 配置由 `render_consumer_bridge_nginx` 从仓库的消费者入口模板生成，镜像使用已核验的本地 Nginx 镜像 ID。检查 `maas-consumer-edge.service`、`maas-consumer-api.socket`、`docker network inspect maas-consumer` 和代理的端口映射。从网络内运行能力查询和 OCR 探针。停用时先把下游切回已验收入口，再禁用代理并停止 socket 和转发服务。网络无成员后才可移除。

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
| 2026-11-09 | 计划更新 | 确认下游容器也将迁到新 PC，新增同机接入与网桥重建验收。 | 下游仍在 XZ-31-001。新 PC 下游迁移及同机 OCR 验收待执行。 |
| 2026-11-09 | 方案 B 预验 | 新 PC 临时专用 bridge、服务名、Unix socket 转发及网络重建验证通过。 | 未切流。实际下游应用和真实 OCR 待验。 |
| 2026-11-09 | 方案 B 部署 | 新 PC 专用网络、代理及 socket 已启用。实际 MaaS 鉴权和 OCR 输入校验通过，其他 Docker 网络无法解析或连接，宿主机无 18831 监听。 | 下游尚在旧机。待迁移、Worker 就绪和实际业务 OCR 验收。 |
