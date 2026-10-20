# MaaS P0/P1 最小实现

实现范围：任务API + PostgreSQL持久队列 + 执行器 + 控制端ArtifactStore。**当前只启用 `mock.echo`，真实OCR异步connector、精确排空和公网服务尚未交付。** 既有GPUStack同步OCR不受本实现替换。

## 环境

Python **3.11**、PostgreSQL 16。Mac现有Python3.9不能直接运行本包，请使用Python3.11环境或Docker。运行依赖见根目录requirements-maas.txt；测试需要pytest（目前也固定在该文件）。

## 身份与凭据

复用GPUStack inference key，不提供新密码/用户注册。管理员将一个已有、限定模型的非管理员key以600权限保存；按bindings.example.json登记project映射及该服务允许的能力。**mock.echo是本地受控验证能力，其授权由登记文件明确授予，不代表GPUStack实际部署了mock模型。**

每次请求在本地匹配已登记key后，仍调用GPUStack `/v1/models`校验有效性；不接受客户身份头，不信任客户携带的auth cache。未登记/本地禁用的key拒绝访问。P2固定版本OCR、未知执行隔离、常驻collector和权限/漂移/容器重建均已实机验证；worker离线按用户要求暂缓，P2未完整验收。

三个secret文件：

- `bindings.json`：受信任的project/key绑定，secret_file指向容器内`/run/secrets/pilot-key`。
- `pilot-key`：已有GPUStack inference key。
- `pepper`：独立随机HMAC pepper，建议32字节以上随机源。

secret目录权限700，文件600，所有者UID 10001以便应用容器读取；secret不能入Git。当前采用受限只读secret文件，**尚未实现独立加密凭据库**；P4前另行评估部署secret管理。

## Compose（部署模板，未作为线上常驻服务验收）

当前001尚未安装Docker Compose插件，本轮使用独立docker run验证API、执行器和数据库。下面模板需先具备Compose v2；本轮未安装全局插件，也未将新服务常驻上线。

在项目根目录：

```sh
# 准备私有env文件（chmod 600）；不要使用示例密码。
cp deploy/maas/.env.example deploy/maas/.env
# 手动填入随机数据库密码、内部GPUStack URL、secret目录绝对路径。
# 依据上述要求准备secret文件，登记key归属必须由管理员确认。
docker compose --env-file deploy/maas/.env -f deploy/maas/compose.yaml up -d --build
```

API仅绑定控制机 `127.0.0.1:18830`，数据库不发布端口。新增卷postgres/artifacts独立于GPUStack数据；不执行`down -v`清理有价值任务。两进程可并发启动，迁移采用数据库advisory lock及校验和。

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

输入默认24小时TTL，活动任务引用期间保留；结果默认24小时，过期下载410。执行器每分钟清理已登记的过期artifact。崩溃留下的未登记孤儿文件自动巡检/回收仍待P3补齐，当前保留供诊断；磁盘余量检查会阻止继续接单。

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
