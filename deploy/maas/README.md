# MaaS 受控 OCR 部署

新实体机从空卷安装请使用[实体机全新部署与验收手册](BARE-METAL.md)。本页保留原试点与 P0–P4 部署记录。

在现有 MaaS 上登记更多模型，或验证图片与文档 OCR 共用一个任务库，请参见[多模型配置接入](MODEL-ONBOARDING.md)。

> **2026-10-29服务接入约定**：当前按单业务下游使用，最终用户/用户Key/用户限流和收费由下游负责；MaaS保留服务凭据映射、项目隔离和全局/项目容量保护。现有代码已支持，本次只完善文档，未修改容器、凭据或额度。当前4执行器及并发4/4，队列1000/100；模板默认值不等同实际部署。[接入策略](../../docs/framework/SERVICE-ACCESS-POLICY.md) · [当前API](../../docs/api/README.md)。

> E6迁移说明（2026-10-22）：正式版本e6-r1已上线，已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](../../docs/framework/E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


实现范围：任务API、独立PostgreSQL、执行器、控制端ArtifactStore及观察进程。固定版本OCR、受控维护及配额已验收；公网服务、精确逐节点无损排空未交付。

当前运行状态以 [P4运行手册](../../docs/framework/P4-LIVE.md) 为准：四个新增服务已常驻001私有入口。下方P1/P2/P3分批验收章节保留历史背景，不能据旧章节判断当前是否部署。

## 环境

Python **3.11**、PostgreSQL 16。Mac现有Python3.9不能直接运行本包，请使用Python3.11环境或Docker。运行依赖见根目录requirements-maas.txt；测试需要pytest（目前也固定在该文件）。

## 身份与凭据

本节的凭据交付对象是下游服务；下游新增最终用户时不要求新增MaaS绑定。多份Key可共用一个project并共享额度；保留已有测试/维护探针绑定。合并或重命名既有project会影响历史任务、文件及幂等范围，不属于本次单项目约定。当前仍复用GPUStack推理Key，独立MaaS服务凭据解耦保持后续增强。

复用GPUStack inference key，不提供新密码/用户注册。管理员将一个已有、限定模型的非管理员key以600权限保存；按bindings.example.json登记project映射及该服务允许的能力。**mock.echo是本地受控验证能力，其授权由登记文件明确授予，不代表GPUStack实际部署了mock模型。**

每次请求在本地匹配已登记key后，仍调用GPUStack `/v1/models`校验有效性；不接受客户身份头，不信任客户携带的auth cache。未登记/本地禁用的key拒绝访问。P2固定版本OCR、未知执行隔离、常驻collector、既有002离线及P3受控维护已按对应范围验收。

三个secret文件：

- `bindings.json`：受信任的project/key绑定，secret_file指向容器内`/run/secrets/pilot-key`。
- `pilot-key`：已有GPUStack inference key。
- `pepper`：独立随机HMAC pepper，建议32字节以上随机源。

secret目录权限700，文件600，所有者UID 10001以便应用容器读取；secret不能入Git。当前采用受限只读secret文件，**尚未实现独立加密凭据库**；P4前另行评估部署secret管理。

## Compose（部署模板，未作为线上常驻服务验收）

P4使用Compose v2。host网络下API仅监听127.0.0.1:18830，独立数据库仅监听127.0.0.1:18835；部署前检查端口空闲。不创建新Docker网桥，不改变既有推理防火墙。

在项目根目录：

```sh
# 准备私有env文件（chmod 600）；不要使用示例密码。
cp deploy/maas/.env.example deploy/maas/.env
# 填入随机hex数据库密码、回环GPUStack URL及secret/模型目录/证明/探针路径。
# 依据上述要求准备secret文件，登记key归属必须由管理员确认。
docker compose --env-file deploy/maas/.env -f deploy/maas/compose.yaml up -d --build
```

API仅绑定控制机 `127.0.0.1:18830`，数据库仅绑定回环18835。新增卷postgres/artifacts独立于GPUStack数据；不执行`down -v`清理有价值任务。迁移采用数据库advisory lock及校验和。

本地管理访问可通过001的SSH转发18830。此模板不提供公网TLS、网关旁路封锁、worker系统切换或生产备份。

## 调用流程

API文档：`http://127.0.0.1:18830/docs`。以下示例从私有文件读取key，不把key写入文档或终端历史：

```python
from pathlib import Path
import httpx

headers = {"Authorization": "Bearer " + Path("/private/path/pilot-key").read_text().strip()}
with httpx.Client(base_url="http://127.0.0.1:18830", headers=headers) as client:
    uploaded = client.post("/maas/v1/artifacts", content=b"sample input",
                           headers={"Content-Type": "text/plain"})
    uploaded.raise_for_status()
    task = client.post("/maas/v1/tasks", headers={"Idempotency-Key": "sample-001"}, json={
        "model": "mock.echo", "input": {"text": "hello", "artifact_id": uploaded.json()["id"]},
        "parameters": {}, "timeout_seconds": 120
    })
    task.raise_for_status()
    print(task.json()["id"])
# GET /maas/v1/tasks/{id}轮询；SUCCEEDED后下载result_artifact_id。
```

同project同幂等键同内容返回原任务（200）；不同内容409；新任务提交成功202。结果是JSON artifact，带outputs、model_revision和真实可用的usage，不伪造token。

## 私有运维

```sh
docker compose --env-file deploy/maas/.env -f deploy/maas/compose.yaml exec api python -m maas_platform.cli status
docker compose --env-file deploy/maas/.env -f deploy/maas/compose.yaml exec api python -m maas_platform.cli pause --reason maintenance
docker compose --env-file deploy/maas/.env -f deploy/maas/compose.yaml exec api python -m maas_platform.cli drain-status
# resume needs a registered credential ID and a read-only mounted probe image:
# ... exec api python -m maas_platform.cli resume --credential-id pilot --probe-image /probe.png
```

`pause` 同时停止本任务服务派发与新增受控同步入口的准入；`drain-status`核对在途/UNKNOWN，`resume`要求真实探针。它不停止直接GPUStack/Playground请求，不能作为安全切换Windows的证明。完整流程与尚未落地的旁路边界见 [P3维护手册](../../docs/framework/P3-MAINTENANCE.md)。

输入默认24小时TTL，活动任务引用期间保留；结果默认24小时，过期下载410。执行器周期清理已登记的过期artifact及超过宽限期的孤儿文件；磁盘余量和存储配额检查会阻止继续接纳超额数据。

## 动态容量只观测

在隔离环境把 `compose.capacity-observe.yaml` 叠加到 `compose.yaml`，沿用现有 `observer` 服务。私有环境变量 `MAAS_CAPACITY_POLICY_PATH` 与 `MAAS_CAPACITY_GPUSTACK_KEY_PATH` 分别指向权限为 `0600` 的策略和只读 GPUStack 凭据。observer 读取原生列表和事件，向现有任务库发布带有效期的观测预算，并写出私有 `.observability/capacity.json`。使用 `expires_at` 判断文件中的预算是否仍有效。现有同步和异步入口继续使用原静态额度。

以下是任意单模型的配置形状。其中逻辑模型名在 catalog 注册，部署、集群、归属、路由 ID 及 GPU 名称用隔离环境的原生值。新增模型可按相同结构增加池。多个逻辑名共用一个部署时加入同一个 `routes` 字典。

```json
{
  "global_limit": 8,
  "sync_percent": 25,
  "shared_gpu_vram_budgets": {"GPUStack 返回的已验证 GPU 名称": 16106127360},
  "pools": {
    "example-pool": {
      "cluster_id": 1,
      "model_id": 2,
      "owner_principal_id": 3,
      "routes": {"example.model": 4},
      "gpu_names": ["GPUStack 返回的已验证 GPU 名称"],
      "slots_per_replica": 1,
      "shared_gpu_vram_bytes": 6442450944,
      "hard_limit": 4,
      "queue_limit": 25,
      "gateway_verified": false
    }
  }
}
```

通过隔离试点确认路由、就绪判据及单副本并发后，才将对应池的 `gateway_verified` 设为 `true`。当前原生判定支持同一 GPUStack 来源内的单部署路由及单卡副本。

自动放置场景可在顶层启用 `shared_gpu_vram_budgets`，用 GPUStack 报告的 GPU 名称映射每卡已验收的推理显存预算上限（字节）。各模型池填写 `shared_gpu_vram_bytes`，记录按配置中的 `slots_per_replica` 和受控推理参数实测的单副本显存上界（字节）。示例使用每卡 15 GiB、每副本 6 GiB。每张卡的可用预算取策略上限和原生总显存扣除系统预留后的较小值。每个已落位实例按实测上界和 GPUStack 已分配显存的较大值计入占用，启动中的实例也计入。上界或原生显存证据缺失、同卡存在未登记工作负载或总量超预算时，相关 GPU 暂停计额。聚合路由可能派发到被排除的 GPU，对应模型池也暂停派发，显示 `gpu_budget_unverified`。固定路由可继续使用其他已核对的 GPU。

启用预算前，以代表性共卡负载验证显存余量和延迟，将这些结果用作模型档案及 GPU 预算。每个 GPU 型号的预算校验适用于该型号的所有落点，并优先于旧配置的 `shared_gpu_layouts`。未启用此字段的型号沿用已验收精确布局。详见[设计与实施方案](../../docs/framework/DYNAMIC-CONCURRENCY-IMPLEMENTATION.md)。

### PP-OCRv4 聚合路由优先空闲副本

在 GPUStack 2.2.3 的内置 Higress 容器中运行 [路由同步器](../../scripts/maas/runtime/reconcile_gateway_capacity.py)。用实际的 GPUStack 路由 ID、模型部署 ID 和上游模型名替换环境变量。

```sh
python reconcile_gateway_capacity.py \
  --route-id "$PP_ROUTE_ID" --model-id "$PP_MODEL_ID" --model-name "$PP_UPSTREAM_MODEL"
```

同步器跟随 GPUStack 维护的 Ingress 及 McpBridge 注册表，把同一模型的直连 HTTP 实例汇入一个 Envoy 集群。Envoy 对实例做 `/health` 检查，并优先选择在途请求更少的健康端点。相同 worker 上的不同实例按各自端口处理。原聚合入口仍使用 GPUStack 鉴权和 MaaS 执行回执。模型放置和端点增删继续由 GPUStack 管理。

一个路由运行一个同步器。启用后从 Envoy 生效配置核对目标集群、健康实例数和其他路由。维护窗口内用 SIGTERM 退出同步器，待它删除自己的 EnvoyFilter 后确认 native 权重路由恢复。异常退出时先核验进程和资源归属，再清理该路由的 `maas-least-inflight-<route-id>` 过滤器。当前支持已由 GPUStack 注册的私网 IPv4 直连实例。Envoy 的在途请求数用于选路，MaaS 动态准入负责同步预留、异步上限与账本。

长时对照使用[合成大输入](../../tests/maas/make_peak_capacity_fixture.py)和[混合负载脚本](../../tests/maas/benchmark_peak_gateway.py)。脚本在仅绑定试验身份的测试 API 容器中运行，导入路径指向 MaaS 安装目录。先在暂停且排空的任务库配置 `8/2/6`，PP 池设 5 路总额度并预留 2 路同步。依次运行 `baseline` 和 `least_inflight` 两个阶段，每阶段默认 180 秒。脚本结束后暂停派发。切换阶段前排空并清理上一阶段的测试数据，最后恢复原配置。

### 隔离环境启用动态准入

在只观测叠加之外使用 `compose.capacity-admission.yaml`，把同一份策略只读挂载给 API 和所有执行器。配置中需覆盖目录中所有有上游的模型。先确保同一任务库的 API 和执行器均已运行支持动态准入的版本，暂停并排空在途请求，核对只观测预算与配置版本。通过现有私有 CLI 执行 `capacity-enforce`，然后完成维护探针并恢复派发。模式存于任务库，迁移后的默认模式为静态。启用后，策略不匹配、缺失及过期预算会阻止相关派发。

回退时暂停并排空，再执行 `capacity-static`，用原静态限额和探针恢复。两个命令都要求已暂停、无在途及 `UNKNOWN`。配置、任务、预算和计量仍可保留检查。在动态模式下，可能归队的已领取任务保留等待位，以免降额后归队突破队列硬上限。

### 现有测试控制端的常态发布

沿用同一任务库、路由、容量观察器、API 和执行器。为每个模型在 GPUStack 自定义后端中增加版本，以固定镜像摘要作为该版本的镜像。部署显式选择新增版本，保留原版本用于回退。模型级镜像覆盖在副本重建后曾丢失。发布后实际将模型缩至零并恢复，核对 GPUStack 后端版本、worker 上运行的镜像和 MaaS 收到的 GPU 终态。 [现场故障 #67](https://github.com/WTaoGitHub/cybercafe-maas/issues/67)

深度学习后端分别核对显存余量，按通过验证的 GPU 布局配置容量策略。现有调节器继续写入同一个原生部署 ID。只有确认就绪、可路由且符合策略的副本获得额度。此前 PP-OCRv4 试验使用的 worker 调度标签已撤销。 [历史调度记录 #66](https://github.com/WTaoGitHub/cybercafe-maas/issues/66)

### 三模型副本权重准备（2026-11-20）

[三模型策略示例](replica-policy-three-ocr.example.json)对应当前测试部署ID：Unlimited-OCR-2为13、OvisOCR3为14、PP-OCRv4为1；其他环境先核对ID、集群及归属，不能直接套用。示例默认Observe。权重为1:2:4、同优先级、最小副本各1；`max_replicas=32`是当前实现的每模型安全硬边界，运行时写授权也须覆盖相同范围，不能把它当目标数量或使用1/2/4封顶。当前资源声明分别为12、6、3.5GiB，由每轮原生部署读取，不在策略重复维护。

权重用于选择资源约束下的整数分配，不要求精确倍数或固定启动顺序；1/2/5可作为合适结果。三台空闲16GB卡、现有PP安全选择器的只读假设预演为1/2/5，这不是三台实际Ready或真实GPU混跑证据。实际目标还受逐卡已有占用、选择器、保留显存和未就绪实例约束。资源不足时不能为凑比例超配或降低服务保护下限。

三套部署使用同一调节器，当前为 **Apply 且全局暂停**。生产模拟允许任意 worker 上下线，三套部署的 `worker_selector` 和 `gpu_selector` 均为空，由 GPUStack 在可用资源中调度。已移除 worker 6、7 的 `maas-capacity-pp-ready` 标签，系统身份标签仍可用于识别节点。GPUStack API 回读确认 Unlimited、Ovis、PP 的目标/Ready 副本仍为 0/0、2/2、2/2，实例所在 worker 一致，调节器无未确认写入。执行回执及已验证共享布局要求继续生效。

改变写授权需重建同镜像 E17。实际 E17 的独立发布文件由控制端活动版本台账的 `replica_regulator.compose_file` 记录。使用该发布文件可保留当前 MaaS API 版本。

正式试验前先冻结受理并排空，在worker仍在线时将三部署归零、确认实际推理容器退出；不能只停worker来认定GPU空闲。worker下线后确认可调度GPU为零、模型目标仍为零及无未确认写入，再显式恢复调节器，随后由管理员准备允许的三台worker上线。必须核对最终Ready和真实推理，未验证的同卡模型组合不会自动加入MaaS准入白名单。本操作不授权对受保护VM整机重启。

沿用[Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)。本段仅为配置准备与运行顺序，不代表三worker加权实测已完成。

## 验证

```sh
# URL必须指向可销毁的独立测试数据库，库名以_test结尾。
MAAS_TEST_DATABASE_URL=postgresql://.../maas_test python -m pytest tests/maas -q
```

测试会DROP测试库public schema，禁止使用GPUStack数据库。`scripts/maas/start_test_db.py`在001创建独立测试容器/私有env文件，`smoke_live.py`以临时非管理员用户/key验证真实上游鉴权、独立API/worker进程重启与SIGKILL租约恢复；退出时删除临时身份及smoke容器。

证据与当前限制见 [实施报告](../../results/maas-p0-p1/REPORT.md)。这些探针不修改既有OCR模型/backend配置，测试数据库和新代码文件与原服务隔离。

## P1 收尾验证

[验收报告](../../results/maas-p1/REPORT.md)：47项回归与1项真实数据库停机/恢复专项通过。适配器注册表位于 `maas_platform/adapters.py`；模型目录 driver 可选 `mock` 或 `local.sha256`，摘要模型输入为 `{"source_artifact":"art_..."}`，参数为空。注册摘要模型时还需将逻辑模型名加入对应 credential 的 models 允许清单。

数据库中断专项由 `scripts/maas/test_db_interruption.py` 在控制端运行，仅操作指定标签的独立测试容器。测试会重建测试库 schema，不可用于业务库。测试结束后可停止测试数据库，不影响现有GPUStack。

## P2 首批 OCR

[接入与隔离操作说明](../../docs/framework/P2-OCR.md) · [首批实机报告](../../results/maas-p2/REPORT.html)。驱动 `gpustack.ocr` 只使用已上传的PNG/JPEG artifact，通过GPUStack入口推理，必须配置pin和新鲜的受信任预检文件。常驻collector已补齐，但任务API/执行器还未常驻发布，首批测试脚本不是长期发布包。

## 常驻collector（P2收尾）

001已启用 `maas-collector.service`，002使用固定命令只读SSH清单。操作说明见 [P2-COLLECTOR.md](../../docs/framework/P2-COLLECTOR.md)。任务API/执行器仍是临时验收服务；用户授权后已完成既有002停止、强停和保留SSH网络隔离测试，见 [离线报告](../../results/maas-worker-offline/REPORT.html)。不得在现有两节点追加GPUStack worker或Docker daemon做故障实验。

## 受控同步入口（P3首批）

`POST /gpustack/v1/chat/completions`仅支持目录中的OCR逻辑模型名（例如`ocr.default`）和`stream=false`；凭据绑定也使用逻辑模型名，原生GPUStack模型ACL仍会验证。请求不能直接传原路由名绕过绑定。该入口需要与异步执行器共享维护数据库；超时等未知执行会同时隔离同步和异步派发。

新增数据库迁移只作用于独立任务库。实际Nginx配置为 `edge.nginx.conf`，已部署到001私有18831入口并通过真实OCR验收；`edge.nginx.conf.example`为历史示例。两节点另已应用运行期网络策略，范围和回退见 [边界说明](../../docs/framework/P3-BOUNDARY.md)。现有Playground保留SSH运维访问，不支持直接迁移到当前非流式试点入口。原H3隧道仍不经过MaaS门禁；尚不能宣称整机全局维护或安全关机。任务API/执行器仍未常驻发布。

最新维护状态：H3/隧道已停用；全局副本降0、002原worker及GPU进程停止/恢复的私有控制工具和演练已通过，见 [节点维护手册](../../docs/framework/P3-NODE-MAINTENANCE.md)。VM和SSH始终保留，不做自动关机。W7已验收，P4常驻部署进展见下文及最新报告。

### P3/W7 运营控制

同步与异步共享并发配额；新增全局/项目存储配额、孤儿宽限期和单轮清理数量。Compose 的 observer 将私有指标和告警状态写入共享 artifact 卷下 `.observability/`，不开放 HTTP 端口。完整配置、计量口径及处置步骤见 [P3-OPERATIONS.md](../../docs/framework/P3-OPERATIONS.md)。W7历史报告记录临时验收状态；P4已完成Compose安装与常驻私有服务发布，见 [P4首批报告](../../results/maas-p4-bootstrap/REPORT.html)。

## P4 正式配置

Compose 必须指定 `MAAS_CATALOG_PATH`，按 `catalog.ocr.example.json` 及已登记 pin 填入实际 revision/route；mock 示例不再被自动加载。`MAAS_TRUSTED_DIR` 只能指向 collector 的独立 consumer 导出目录，`MAAS_PROBE_DIR` 指向不含凭据的探针目录。三个应用使用同一 artifact 卷。secret 目录/文件属 UID 10001、权限 700/600，模型目录和探针只读。

collector 通过 `consumer_root`、`consumer_gid=10001` 开启精简证明导出：目录root:10001、750，文件root:10001、640。应用仅有只读挂载，不能修改证明。完整快照/SSH/管理key不导出。每5秒刷新，失败撤销preflight；应用仍检查30秒新鲜度和pin摘要。不得通过开放完整私有目录来解决读取权限。

`prepare_live.py` 仅在001以root运行一次，准备 `/root/wtaobj/maas-live/`：独立非管理员推理身份、30天key、secret、正式OCR目录、探针及私有env；已有目录时拒绝覆盖。需要在30天前轮换key，轮换提醒时间保存在私有identity.json。此脚本不删除已有业务数据、不修改模型或worker。

```sh
# 001上从部署目录执行，先完成collector配置并刷新导出。
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml config --quiet
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml up -d --build --wait
```

host网络适用于当前受信任001试点；客户入口仍通过既有回环18831 Nginx。不要将API改为0.0.0.0，不挂载Docker socket或管理凭据。首次启动会创建独立命名卷；重启保留数据，禁止使用 `down -v`。

### 正式维护命令

当前已绑定常驻环境。每次先执行 `python3 scripts/maas/runtime/bind_maintenance.py new-session` 获得本窗口配置路径，再以相同配置执行maintenance_control.py的begin/stop/recover/resume。上一窗口未完成时拒绝另建窗口。操作会暂停整个目标集群推理，不停止001控制端；完整命令、恢复保护和异常处理见 [正式维护手册](../../docs/framework/P4-MAINTENANCE.md)，本轮 [演练报告](../../results/maas-p4-maintenance/REPORT.html)。

### P4发布与长时验收

私有TLS回环18832、独立宿主机监控和有界指标历史、任务数据加密异机备份/独立恢复、两节点启动前拦截/Docker重启、兼容镜像回退均已验证。Compose可通过MAAS_TASK_IMAGE选择已批准应用镜像。8小时测试已改为今晚19:00定时启动（北京时间），未标记P4整体验收；使用 [P4发布手册](../../docs/framework/P4-RELEASE.md) 与 [进展报告](../../results/maas-p4-release/REPORT.html) 查看当前状态。公网按用户选择保持关闭，不在负载测试期间随意重启或变更服务。


## worker 关机事件（2026-11-18）

本模块只接收并保存 worker 关机预告，按接收时间保留 48 小时，定期删除过期记录；不修改节点、模型副本、任务或调度状态。[完整设计与实施状态](../../docs/framework/WORKER-SHUTDOWN-EVENTS.md) · [Issue #2](https://github.com/WTaoGitHub/cybercafe-maas/issues/2)。

- 接收：`POST /maas/internal/worker-shutdown-events`，JSON 最大 4 KiB，节点专用 Bearer，提交数据库事务后返回 201；每次新 UUID 独立保存，同一 worker 可有多条事件。
- 默认关闭：不设置 `MAAS_SHUTDOWN_EVENT_KEY_FILE` 时返回 404。启用时，该变量指向现有只读 secret 挂载中的私有文件，例如 `/run/secrets/shutdown-event-key`；内容为同一专用随机密钥的 64 个小写十六进制字符。MaaS 副本由 UID 10001 持有、0600；Bootstrap 的独立副本按其服务 UID 持有、0600。两端内容相同，不要求共用文件。密钥不属于注册 token、消费者 key 或发布签名私钥，不放进镜像/worker。
- 迁移：新增 `011_worker_shutdown_events.sql`，只在独立 MaaS 数据库建事件表与过期索引，沿用已有迁移机制；不改历史迁移，不向 GPUStack 数据库写入。
- 清理：API 内启动先执行一批、每 60 秒一批、每批最多 500 条；关闭接收或没有 executor 时仍清理。事件过期后由下一批删除，服务停机/积压会延后，恢复补清理，不永久归档。

私有运维手工执行一批清理，沿用已审核的 Compose/env 配置：

```sh
docker compose --env-file <private-env> -f deploy/maas/compose.yaml exec -T api \
  python -m maas_platform.cli shutdown-events-cleanup
```

输出仅为 `deleted_events` 数量。正常记录通过数据库运维查询取证，本次不增加查询 API/UI。接收入口的默认部署模板已有精确 POST 路径、小体积限制和关闭上游重试；实际对外 HTTPS 与批准 worker 来源的限制仍由现有 TLS/网络部署层配置，不以修改模板等同已完成网络发布。客户端总等待最多 1 秒，服务端迟到提交可能已经成功，客户端不重试。

发布顺序：先服务端/迁移和精确入口，再 Bootstrap 可选签发，再客户端程序/凭据准备及单元启用。回退先停用客户端通知单元，关闭接收/签发；保留事件表和清理直到满 48 小时后删完。如回退旧 API 镜像不再有清理功能，使用候选镜像的同一私有命令安排按分钟执行，直到表空，再撤去临时清理。

接收端已部署。候选 Docker 镜像、Nginx 现场语法及私有 HTTPS 到数据库验证通过。用户确认暂无独立测试机，真实 Ubuntu/systemd 关机验收保留待办，不在 XZ-31-001/002 上通过整机关机/重启补验。[现场部署记录](../../results/maas-worker-shutdown-events-20261118/DEPLOYMENT.md)
