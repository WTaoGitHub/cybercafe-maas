# 新 PC 控制端上线计划与执行台账

2026-11-11，调整克隆网络边界单元，在网络与 Docker 就绪后执行，取消早期启动顺序约束，并限制单元启动 45 秒、停止 10 秒。E5 仍在注册前要求边界通过。此变更待安装到独立母版、保存新快照并实测，既有快照的启动顺序继续按原配置运行。当前新 VPC SSH 超时的根因尚未确定。

更新日期 2026-11-10（北京时间）。**新 PC 的 `maas-baremetal-workers`（集群 ID `1`）已接入首台从试点快照启动的无盘 VPC。首启注册已验，接续验收使用同一张 GPU 的替代 VPC。**

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
| P2 母版与首台新 worker | 使用预装新版 E5 的试点克隆系统镜像和新 PC 的 `maas-baremetal-workers`（ID `1`）。核对 bootstrap、HTTPS 原生入口、registry、证书信任及双向连通。首台 VPC 启动后，确认 E5 等待许可，再批准该 GPU 的启动。核对 `name` 和 `external_id` 与实测 GPU UUID 一致，worker 进入 `Ready`，完成冷拉取和真实 CUDA OCR。随后在同一卡上启动替代 VPC，验证原 `worker_id` 恢复及旧凭据失效。 | 首启注册通过，接续待验 |
| P3 扩至目标容量 | 按计划启用新 GPU worker，逐台验收独立身份、Ready、镜像 digest、单节点 OCR 和监控覆盖。按现网基线核对副本与并发，记录吞吐、延迟、失败率和 `UNKNOWN`。从下游实际运行容器验证新私有入口、TLS、鉴权、幂等提交、状态轮询、结果下载和用量。先完成备份恢复演练并确认旧历史处理方式。 | 待 P2 |
| P4 下游迁移和切流 | 确认窗口和回退负责人。准备新 PC 上的下游容器和同机入口，通过下方验收后切换业务。停止旧入口接受新业务请求，核对队列、运行中任务、同步请求及 `UNKNOWN`，保留状态查询与结果下载直至旧任务按约定处理完毕。观察达到约定门槛后停止旧项目服务。保留旧卷、配置和入口恢复步骤。 | 待 P3 和切流窗口 |
| P5 旧环境退役 | 记录稳定观察结果、备份去向和剩余历史访问方式。旧 worker VPC 的不可逆处置由管理员另行安排，并遵守上面的 VPC 约束。 | 待 P4 和单独处置决定 |

安装参考[实体机手册](BARE-METAL.md)。该手册的同机 NVIDIA worker 步骤适用于具备 GPU 的主机，本次用独立新 GPU VPC 替代。其他参考包括[母版网络隔离](../bootstrap/clone/network/README.md)、[仓库服务](REGISTRY-SERVICE.md)、[下游容器入口](CONTAINER-HTTP.md)、[备份与恢复](../../docs/handbooks/OPERATIONS.md#8-备份异机保管与恢复)和[消费者 OCR 契约](../../docs/api/CONSUMER-OCR.md)。切流以实际运行契约和下游验收为准，尤其核对快捷 OCR 接口的同步或异步响应。

## 下游同机接入验收

### 方案 C 固定 HTTP 消费者入口

新 PC 的业务消费者入口为 `http://maas-service:18831`。下游可保留自己的 Compose 网络，通过新 PC 固定私网地址上的 Nginx 访问回环 MaaS API。使用新控制端登记的服务凭据。宿主机程序继续使用原有回环 HTTPS `127.0.0.1:18832`。

入口由 `maas-consumer-private.service` 管理，镜像配置、地址、生成的 Nginx 配置和刷新脚本均位于 `/srv/cybercafe-maas/control/consumer-private/`。宿主机 `/etc/hosts` 提供名称。方案 A 的网关监听与防火墙表已移除，旧部署配置已备份。

`maas-consumer-private-policy.timer` 每约 5 秒发现当前本机非 internal Docker bridge，将接口规则原子更新到独立 nftables 表。策略允许这类本机桥接容器访问固定 HTTP 入口，排除 `maas-backend`、内部网络和物理接口。Docker 网络重建后等一次成功刷新即可恢复。容器仍须提供有效的服务凭据。HTTP 仅用于这条同机链路。

新 PC 上两张自建 bridge 均能通过 Docker 内置 DNS 解析 `maas-service`，其中一张重建后复验通过。Docker 自带默认 `bridge` 的测试容器未继承该名称，可直接使用方案 C 的私网 IP，或为容器配置能解析该名称的 DNS。从旧控制端走物理网络访问固定入口被拦截。停用时先禁用并停止私网代理和策略定时器，停止可能在执行的策略单元，然后移除私网入口专属 nftables 表和 hosts 条目。

### GPUStack 和仓库后端网络

新 PC 上的 GPUStack Server 和镜像仓库后端只接入专用 Docker bridge `maas-backend`，继续分别发布到宿主机回环 `127.0.0.1:18080` 和 `127.0.0.1:18500`。MaaS 和 Worker 私网代理继续通过原回环地址访问。该后端网络保留出站路由，以支持 GPUStack 回连 Worker。下游通过方案 C 调用 MaaS，可保留自己的 Docker 网络。

重建后端容器时显式指定 `--network maas-backend` 并保留各自的回环端口映射。若启用历史 GPUStack 回退容器，也要在接流量前核对其网络归属。验收应从默认 `bridge` 确认 MaaS 入口和鉴权正常，并确认 GPUStack、仓库后端及宿主机内部端口无法直连。首台 Worker 加入后再验收注册、镜像拉取、私网回连和真实 OCR。参见 [隔离问题 #53](https://github.com/WTaoGitHub/cybercafe-maas/issues/53)。

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
- 新 PC 的 `maas-control-worker-edge` 将三个 worker 入口转发到本机回环，由 `control/worker-edge/nginx.conf` 限定来源。初始只允许母版地址，母版已验证 GPUStack、registry 与 bootstrap 连通。2026-11-10 将三个入口的来源范围调整为 `10.11.0.0/16`，范围外连接由 Nginx 拒绝。P2 从首台克隆核对实际来源和三个入口的连通性，再进行首启验收。
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
| 2026-11-09 | 切换到方案 A | 备份后撤下方案 B，在默认 bridge 网关部署代理。默认 bridge 实测匿名 `401`、授权能力查询 `200`、无效图片 `422`、内部健康路径 `404`。其他 Docker 网络的连接被 nftables 拦截。 | 下游仍在旧机。实际业务 OCR 待下游迁移及 Worker 就绪后验收。 |
| 2026-11-09 | 后端网络隔离 | 在线将 GPUStack 和仓库后端迁入 `maas-backend`，核对容器身份、启动时间、端口映射和挂载。默认 bridge 的 MaaS 鉴权和 OCR 输入校验通过，后端直连及内部端口均不可达。 | 无宿主机、Docker 或容器重启。实际 Worker 注册、拉镜像和推理待验。 |
| 2026-11-09 | 固定 HTTP 入口 | 并行部署 `maas-service:18831`，两个自建 bridge 的 DNS、鉴权及 OCR 输入校验通过，重建其中一张网络后复验通过，物理网卡来源被拦截。 | 默认 bridge 仍使用原网关 IP。实际下游迁移和真实 OCR 待验。 |
| 2026-11-09 | 方案 C 独立运行 | 移除方案 A 的网关监听、服务和防火墙表。方案 C 单独管理镜像配置，来自自建 bridge 的名称接入及默认 bridge 的固定 IP 接入复验通过。 | 实际下游迁移和真实 OCR 待验。 |
| 2026-11-09 | 方案 C 下游网络验证 | 从新 PC 上实际运行的下游后端网络命名空间探测，`maas-service` 解析成功，根路径 `404`，能力查询匿名 `401`、授权 `200`，无效 OCR 输入 `422 IMAGE_BASE64`。 | 此次探针覆盖实际下游网络及 API 校验。下游应用的真实 OCR 流程未在该次探测中执行。 |
| 2026-11-10 | 控制端版本更新 | 部署 `bdd5f52` 到 MaaS API、执行器、观察器、E17 采样与日报及 Bootstrap，并升级 GPUStack 副本调节插件到 0.16.0，沿用 Observe 模式。MaaS、Bootstrap、GPUStack 健康接口均返回 `200`。实际下游容器的匿名能力查询返回 `401`，内部健康路径返回 `404`。 | 旧镜像、应用环境文件备份和手动管理的旧容器保留作回退。部署未启用 MaaS Apply 或新的无盘启动授权，真实 OCR 不在本次验证范围。参见 [构建修复 #56](https://github.com/WTaoGitHub/cybercafe-maas/issues/56)。 |
| 2026-11-10 | 副本调节器空集群试点 | 只重建 E17 日报容器并叠加 MaaS Observe 与 Apply Compose 配置。只授权新集群的 `maas-ocr`，上限为 1，全局与集群在途预算各为 1。MaaS 日志进入等待 GPU，期望和就绪均为 0，worker 与实例均为 0，待确认日志为空。GPUStack 内旧插件保持 Observe，MaaS 与 GPUStack 健康检查均为 `200`。 | 压力采集已安装并启用，空来源时按条件保持待机，MaaS 压力门槛关闭。待首台克隆注册并核验身份后接入真实样本，再验证目标 `0 → 1`、实例就绪与 OCR。 [试点追踪 #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39) |
| 2026-11-10 | Worker 入口准入 | 将 Nginx 的三个 worker 入口放行范围调整为 `10.11.0.0/16`。`nginx -t` 通过并重载原容器。由网段内来源实测 GPUStack `/version` 和 Bootstrap `/healthz` 均返回 `200`，registry 匿名 `/v2/` 返回 `401`。 | 从网段外的本机源地址访问三个 worker 入口均被断开。原配置已备份，首台新克隆连通性待首启核对。 [迁移跟踪 #14](https://github.com/WTaoGitHub/cybercafe-maas/issues/14) |
| 2026-11-10 | P2 无盘启动授权 | GPUStack v2.2.3 加装 enrollment 0.3.0，`required` 绑定 `maas-baremetal-workers`（ID `1`）。独立 Bootstrap `18841`、HTTPS 注册 `18444` 以及 `baremetal-diskless-r1` 发布已启用。保留普通发布 `baremetal-pilot-r1`，两份发布均通过 HTTPS 取回和验签。 | 合成占位验证预登记、授权签发和重复请求。错集群授权和普通集群 token 尝试注册受管 worker 均返回 `403`，无许可返回 `401`。占位记录已退役，试点副本为 0。真实首启和接续待验。 [无盘身份跟踪 #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16) |
| 2026-11-10 | P2 默认集群 | 新 PC 的无盘 Bootstrap 部署 `dd9b041`。省略集群 ID 的镜像设置、授权项与发布输入重建出了原 `baremetal-diskless-r1` 签名发布，`cluster_id=1`。 | HTTPS 取配置并验签成功，无启动许可返回 `401`。容器内复核缺省授权项匹配集群 `1`，错误集群触发 `403`。原 Bootstrap 容器保留作回退，真实 VPC 首启待验。 |
| 2026-11-10 | P2 试点母版准备 | 在母版安装 `dd9b041` 版 E5 和指向新 PC 的试点配置，启用等待启动许可的 `.path`。母版实测签名发布、HTTPS 原生 `/version` 和仓库鉴权通过，固定摘要的 worker 镜像已缓存。 | systemd 校验通过，母版无容器、原生身份或启动许可。旧配置与两份旧引导状态文件已异机备份。待管理员保存独立试点快照并交付首台克隆的实例 ID 和 SSH 入口。 [快照与首启跟踪 #20](https://github.com/WTaoGitHub/cybercafe-maas/issues/20) |
| 2026-11-10 | P2 首台无盘克隆 | 首启继承 E5，许可交付前路径单元活动，引导服务未启动，原生身份为空。核对 `baremetal-diskless-r1` 验签、HTTPS 原生入口、仓库鉴权和固定镜像摘要。交付许可后，systemd 自动引导，记录 `APPLIED`、`trigger=boot`。 | 集群 `1` 的 `worker_id=2` 进入 `Ready`，`name` 与 `external_id` 均匹配实测 GPU UUID。压力采样覆盖 worker 2，调节器确认副本目标 `0 → 1`。 [无盘身份跟踪 #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16) |
| 2026-11-10 | P2 首台克隆 OCR | 公共辅助镜像拉取遇到连接重置，从旧控制端缓存补齐 `runtime:pause` 后，固定摘要的 OCR 镜像从新仓库冷拉取成功。试点模型在 worker 2 上达到 1/1，本机真实 CUDA OCR 返回 `200` 和 2 行。 | MaaS 同步 OCR 入口返回 `401 UPSTREAM_REJECTED`，消费者端到端请求待核对上游鉴权。 [控制端迁移 #14](https://github.com/WTaoGitHub/cybercafe-maas/issues/14) |
| 2026-11-10 | Bootstrap 版本对齐 | 核对 `2229ea5`。普通 Bootstrap 切换到该提交的源码，无盘 Bootstrap 已运行相同代码。抽查 MaaS API、E17 日报、GPUStack 副本调节和预登记模块，与该提交一致。 | 原签名发布内容未变，普通 Bootstrap 鉴权请求返回 `200`、匿名返回 `401`，两套 Bootstrap、MaaS 和 GPUStack 健康检查通过。原普通 Bootstrap 容器保留作回退，真实克隆首启待验。 [验收跟踪 #48](https://github.com/WTaoGitHub/cybercafe-maas/issues/48) |
| 2026-11-10 | 停止旧项目服务 | 停止旧入口和 MaaS 服务，旧 GPUStack 模型 18、44 的期望副本及实例数均降至 0，再停止 001 上剩余的旧 MaaS 和 GPUStack 容器。三个现役 Worker（7、10、11）最终遥测的显存分配和 GPU 利用率均为 0，002 的主机侧计算进程数为 0。 | 001 与 002 仍可通过 SSH 触达，002 Worker 管理容器仍运行。003、005 缺少主机侧计算进程证据。未操作 VPC 或平台 GPU 分配，新 PC 的 MaaS、GPUStack 健康检查均为 `200`。 [接管跟踪 #48](https://github.com/WTaoGitHub/cybercafe-maas/issues/48) · [SSH 入口 #54](https://github.com/WTaoGitHub/cybercafe-maas/issues/54) |

### 无盘启动试点接力

- 母版已使用新 PC `baremetal-diskless` 下的 `operator/image-settings.json` 安装 E5。私有操作目录保留安装清单和母版回退包。发布使用 `gpu-external-id`，原生身份和启动许可分别位于 `/run/maas-bootstrap` 与 `/run/maas-start/boot-grant`。管理员封版后，首台克隆按实测 GPU UUID 核对并批准启动。接续试验保留该 GPU 的 worker 记录，核对旧实例退出后再发接续许可。
- 两个试点入口允许 `10.11.0.0/16`。从网段内复核健康检查，网段外探测被拒绝。普通入口 `18080` 改为 HTTP 代理以拦截 `/internal/`，普通入口和试点 HTTPS 入口均拒绝内部登记、接续和退役路径。Bootstrap 经本机回环调用这些接口。
- 切换前备份了 GPUStack PostgreSQL 并核对归档清单，保留停止的原 GPUStack 容器、Nginx 原配置与部署记录。回退前先停止试点授权，核对受管记录与会话状态，再按恢复点处理。

### 自动发放启动许可方案

2026-11-10 确定试点信任前提。受管 worker 从本次运行环境读取实际 GPU UUID，并按该 UUID 注册。MaaS 在已认证的试点克隆范围内使用实测 UUID 匹配预登记记录，目标为新 PC 的 `maas-baremetal-workers`（ID `1`）。

| 场景 | 计划行为与验收 |
| --- | --- |
| 首次注册 | 预登记记录存在且无已批准会话时，MaaS 自动签发绑定集群、GPU UUID 和本次启动 ID 的短期许可。未预登记的 GPU 保持等待。验收从快照启动后无需运维下发文件，原生 worker 在许可生效后进入 `Ready`。 |
| 重试与并发 | 同一启动重复请求复用同一个有效许可。按集群与 GPU 绑定串行处理，不同启动 ID 竞争时只允许一个会话获得许可。验收网络重试和并发时仍只有一条 worker 记录。 |
| 同卡接续 | 已认证的克隆提交当前 GPU UUID。GPUStack 锁定原记录后核对绑定唯一，已有会话且状态为 `not_ready` 时签发新许可、保留原 `worker_id` 并撤销旧凭据。其他状态拒绝。旧实例临时断网并进入 `not_ready` 也可能触发接续。 |

自动接续已部署到新 PC（`c6a79dd`，enrollment 0.4.0）。集群 1 已授权 worker 2 的 GPU，实测 `Ready` 时申请接续返回 `409`，未授权 GPU 返回 `403`，OCR 副本为 1/1。控制端保留升级前容器。自动启动安装包已在离线文件系统样本验证。原母版内网入口当前对应运行中的 worker，待确认独立母版入口或离线系统盘后安装并封版，再对 004 实测自动接续。 [无盘身份跟踪 #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)

### 副本调节试点接力

- 试点集群为新 PC 上的 `maas-baremetal-workers`（GPUStack 集群 ID `1`），试点模型为 `maas-ocr`（部署 ID `1`）。首台克隆的注册与 GPU 到达事件均以该集群为准。核对 worker 的 `cluster_id=1` 后，再配置其压力采样来源并验证 `0 → 1`。
- 新 PC 的试点使用控制目录下独立的 `replica-maas-pilot` 目录，保留 `replica.env`、策略输入和持久日志。E17 日报后续重建须继续使用 `compose.yaml`、`compose.replica-observe.yaml`、`compose.replica-apply.yaml` 和该私有环境覆盖。专用管理凭据为 30 天有效期。写入日志为空表示当前没有待对账操作。
- 首台试点克隆启动后，先核对实际来源准入、GPUStack worker ID 和主机公钥。配置压力采样专用公钥的受限采样命令、固定主机公钥及 `WORKER_ID=USER@HOST` 来源，启动 `maas-replica-pressure.service`。核对样本确实来自该 worker、覆盖其 ID 并持续刷新。当前试点每约 3 秒采样，最大样本年龄 20 秒，恢复观察期 10 秒。
- 准备好首台克隆接入与压力采样的操作窗口。默认上线事件有效期为 600 秒。若真实样本在窗口内未就绪，保持停写，待条件满足后安排新的试点上线事件。记录一次原生目标更新、实例就绪与真实 OCR 后再考虑提高副本上限。

2026-11-11，独立母版已安装 `c6a79dd` 版 E5 并配置自动申请许可，集群 ID 为 1。旧文件已异机备份。systemd 开机服务已启用且当前未运行，签名发布与 HTTPS 原生入口验证通过，固定 worker 镜像已缓存，母版无容器、原生身份或启动许可。下一步由管理员保存新试点快照后安排 004 的同卡自动接续验收。

2026-11-11，新快照启动的克隆获得不同 GPU。首次开机网络边界服务因供应商尚未把 tower 主机名更新为克隆名而拒绝执行，E5 未启动。主机名变为克隆名后，在集群 1 完成新 GPU 预登记与自动授权，重试 systemd 链路，E5 自动申请许可并以 worker_id=3 进入 Ready，未人工下发许可文件。本次证明自动发证链路可用；首次开机无需干预的验收待修复主机名初始化时序后复测，同卡接续待下一轮。

2026-11-11，新克隆进一步核验为 RTX 4080 SUPER，GPU UUID 与原生上报一致，worker 3 为 Ready / direct，未用本地 CLI 或环境变量覆盖 proxy_mode。Observe 期间手动恢复一个 OCR 副本后，常驻 GPUStack 切换为 embedded Higress，保留同版原生 API 容器和经校验的数据库回退材料。授权推理 200、两行，匿名 / 错误 Key 401、无模型权限 403，Envoy 实际后端匹配实例 2。MaaS 异步任务 SUCCEEDED，唯一成功计量 1，UNKNOWN=0，私有 HTTP/HTTPS 入口恢复。压力样本已覆盖 worker 3；MaaS Apply 修订 3 仍限部署 1、最多 1 副本，旧插件 Observe。私有备份已复制到 Mac 并核对摘要。未重启 VM、worker 或 Docker 引擎。真实首启时序、同卡接续、多节点和路由刷新 / 性能对照边界见[验收记录](../../results/maas-higress-direct-20261111/LIVE-WORKER3.md)。

2026-11-11，用户决定暂缓处理首启主机名更新时序问题。等待重试与事件触发方案保留在 Issue #20。现场遇到此类失败时，核对克隆主机名、GPU 和集群配置后，由运维手动重试现有 E5 上线流程，许可沿用 MaaS 自动申请。首启失败证据保留，接受该场景需要人工恢复的限制。

2026-11-11，用户先安排 003 的维护及相同快照启动，再准备第二台 VPC。003 对应当前 worker 3，身份和旧凭据已在 Mac 私有留存，前后配置与数据库归档均已校验。已拦截新提交并排空暂停 MaaS（generation 3），Observe 修订 4 下将 OCR 降至 0/0，实例和 GPU 计算进程清零后仅停止 worker 容器；原记录保留，原生状态 not_ready。调节器随后恢复 Apply 修订 5、上限 1，空容量基线成立，模型目标仍为 0，压力采样计划性停止。等待管理员操作后验证调节器自动 0→1；不独立验收 E5 接续，不由本会话执行 VM 操作。[003 交接](../../results/maas-replica-003-restart-20261111/HANDOFF.md) · [调节器 #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。

2026-11-11，同卡自动接续补充验收通过。复用安全留存的重启前 worker token 查询原生身份返回 HTTP 401，新凭据经受信 HTTPS 查询返回 HTTP 200，worker_id=3 保持 Ready。OCR 已由调节器恢复至 1/1，本轮再次向该 worker 的实际运行实例发送双语图片，CUDA 健康检查及推理均通过，返回 HTTP 200 和预期 2 行。此前独立恢复验收已验证 MaaS 异步任务和唯一计量。关联 Issue #16。

2026-11-11，对当前四台新克隆重新只读核验启动时序。内核启动日志显示初始主机名继承自 tower，供应商 `swcfghostname.service` 随后成功设置克隆名。该命名单元与旧边界单元均在 `network-pre.target` 之前，彼此没有启动顺序依赖，四台均出现边界先运行的竞态。下表使用当前启动的单调时间，以秒为单位，避免主机墙钟差异。

| 克隆 | 边界程序启动 | 命名单元完成 | 边界失败、E5 依赖失败 | Docker 就绪 |
| --- | --- | --- | --- | --- |
| 002 | 10.788 | 10.957 | 11.772 | 15.876 |
| 003 | 9.359 | 9.564 | 10.379 | 14.522 |
| 004 | 6.146 | 6.324 | 7.166 | 11.262 |
| 005 | 5.948 | 6.143 | 6.966 | 11.105 |

四台均以 root 执行边界程序，触发主机保护后在约一秒内退出，日志随后明确记录 E5 启动任务因 `dependency` 失败。E5 的主程序开始时间均为零，`Restart=on-failure` 未使这次依赖失败自行恢复。主机名在错误日志写入前已更新，journal 的主机名字段不能当作保护检查瞬间的采样值。

四台后续均进入 `multi-user.target`，Docker 正常运行，SSH socket 在开机约 6 至 11 秒已经监听。部分 `ssh.service` 较晚启动属于 socket 激活，不能据此判断系统较晚开机。只读防火墙检查未发现本程序的自有链或钩子。本批证据明确支持 E5 上线受阻，未发现边界程序长期卡住或安装规则阻断 SSH 的证据。管理员此前报告的平台启动异常、公网连接超时仍缺少相应控制台与映射证据。

当前四台程序摘要匹配 `c6a79dd`，均未安装 `645132a` 启动顺序补丁。该补丁及 45 秒超时用于调整顺序和限制等待，尚无真实首启对照证据。新证据将问题定位为命名单元与边界检查的启动竞态，当前四台全部复现，不能据此批次沿用低发生率判断。等待重试方案仍按用户决定暂缓；本次仅补充分析记录，部署及现场恢复按单独指令执行。关联 Issue #20。

2026-11-11，用户确认 XZ-31-001 已不再承担控制平面，允许作为 worker 注册。源码移除 E5 对 001 主机名与旧控制端地址的注册阻断，以及网络边界入口对 001 主机名的阻断；母版保护、Linux root 与原有 GPU 身份校验保留。Bootstrap 本地测试 128 项通过，覆盖旧名称与地址放行及母版、非 root 拒绝。此变更不涉及维护执行器、系统启动延后方案或现场部署，不解除 001、002 禁止整机重启的约束。真实注册与新快照继承尚待验证，关联 [无盘身份跟踪 #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)。
