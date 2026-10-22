# MaaS 受控 OCR 部署

> E6迁移说明（2026-10-22）：正式版本e6-r1已上线，已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](../../docs/framework/E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


实现范围：任务API、独立PostgreSQL、执行器、控制端ArtifactStore及观察进程。固定版本OCR、受控维护及配额已验收；公网服务、精确逐节点无损排空未交付。

当前运行状态以 [P4运行手册](../../docs/framework/P4-LIVE.md) 为准：四个新增服务已常驻001私有入口。下方P1/P2/P3分批验收章节保留历史背景，不能据旧章节判断当前是否部署。

## 环境

Python **3.11**、PostgreSQL 16。Mac现有Python3.9不能直接运行本包，请使用Python3.11环境或Docker。运行依赖见根目录requirements-maas.txt；测试需要pytest（目前也固定在该文件）。

## 身份与凭据

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

`POST /v1/chat/completions`仅支持目录中的OCR逻辑模型名（例如`ocr.default`）和`stream=false`；凭据绑定也使用逻辑模型名，原生GPUStack模型ACL仍会验证。请求不能直接传原路由名绕过绑定。该入口需要与异步执行器共享维护数据库；超时等未知执行会同时隔离同步和异步派发。

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
