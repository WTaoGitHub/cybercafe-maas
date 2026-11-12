# 实体机全新部署与验收手册

核对基线为 2026-11-06 的 MaaS 源码 `d58158581da121f5676b6e124e1a77928ba0fa32` 和项目已采用的 GPUStack v2.2.3。本文按一台独立的 Linux x86_64、NVIDIA GPU 实体机编写。GPUStack Server、一个原生 Worker、OCR 单副本、MaaS 和私有入口同机运行，各自使用新环境的凭据和数据卷。实体机到位后需执行第 6 节验收。[实机验收跟踪 #48](https://github.com/WTaoGitHub/cybercafe-maas/issues/48)

所有宿主机步骤都在新实体机上执行。XZ-31-001、XZ-31-002 禁止整机重启及等效操作。它们的地址、注册身份、凭据和数据仅属于原环境。

## 1. 准备主机与部署版本

- 安装受支持的 Ubuntu LTS、时间同步、Docker Engine（至少 28，使用本机 rootful daemon）、Compose v2 或兼容版本、Git、curl、jq、OpenSSL 和 Python 3。Docker 与 Container Toolkit 按主机发行版配置。[Docker Ubuntu 安装](https://docs.docker.com/engine/install/ubuntu/) · [NVIDIA Container Toolkit 安装](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- 按实际 GPU 选驱动，满足 GPUStack v2.2.3 的 CUDA 12.8 或以上驱动要求，再用 OCR 实际镜像验证兼容性。检查磁盘能同时容纳 Docker 镜像/缓存、GPUStack 数据、PostgreSQL 与 MaaS artifact。确认可访问 Quay、Docker Hub、Ubuntu 包源和 PyPI，或已安排等效的受控镜像源。[GPUStack v2.2.3 安装要求](https://github.com/gpustack/gpustack/blob/v2.2.3/docs/installation/requirements.md)
- 给新机分配稳定私网 IPv4。GPUStack Server 容器须能通过该地址访问本机 Worker 和推理端口。先在实体机的隔离网络及主机防火墙中安排访问边界，仅允许授权 SSH 和本机需要的 Docker 流量。Worker 使用 host 网络，重点核对 TCP 10150/10151、40000–40063 以及实际新增监听。容器内的 `127.0.0.1` 指容器自己，Server 回连 Worker 要用其私网地址。

以下命令使用新实体机上的 root Bash。先核对 SSH 目标及主机身份，后续代码块沿用同一 shell 和仓库根目录。记录版本与监听输出时保存在本机私有目录。

```bash
set -euo pipefail
set +x
umask 022
unset DOCKER_CONTEXT
export DOCKER_HOST=unix:///var/run/docker.sock
test "$(uname -s)/$(uname -m)" = Linux/x86_64
hostname
ip -br addr
timedatectl status
docker version
docker compose version
nvidia-smi
ss -lntp
df -h / /var/lib/docker
ip -6 addr show dev lo

git clone git@github.com:WTaoGitHub/cybercafe-maas.git /opt/cybercafe-maas-lab
cd /opt/cybercafe-maas-lab
git checkout --detach d58158581da121f5676b6e124e1a77928ba0fa32
REV=$(git rev-parse HEAD)
SHORT_REV=$(git rev-parse --short=12 HEAD)
umask 077
LAB_ROOT=/srv/cybercafe-maas-lab
install -d -m 0700 "$LAB_ROOT"
GPUSTACK_IMAGE=quay.io/gpustack/gpustack@sha256:55ecc762950a32641b6a4d96195c0662ef72998eaeeda31bba0e8e218d41afef
docker pull --platform linux/amd64 "$GPUSTACK_IMAGE"
docker image inspect "$GPUSTACK_IMAGE" --format '{{.Os}}/{{.Architecture}} {{json .RepoDigests}}'
docker run --rm --gpus all --entrypoint nvidia-smi "$GPUSTACK_IMAGE" -L
```

克隆使用已授权的仓库访问方式。首次安装前核对 18080、18830、18831、18835 空闲，且本手册的容器名、卷和 Compose 项目 `maas-lab` 未被占用。镜像需显示 `linux/amd64` 和指定摘要，容器应看到与宿主机相符的 GPU UUID。换源码或 GPUStack 版本时重新核对构建与接口契约。

## 2. 启动 GPUStack Server 和注册 Worker

Server 使用内置 PostgreSQL，其数据保存在独立卷中。Docker bridge 模式只向宿主机回环发布管理及原生推理端口。

```bash
docker run -d --name maas-lab-gpustack-server --restart unless-stopped \
  -p 127.0.0.1:18080:80 \
  --mount type=volume,src=maas-lab-gpustack-server,dst=/var/lib/gpustack \
  "$GPUSTACK_IMAGE" --system-default-container-registry quay.io

# 等待服务启动后检查版本。冷启动过程中先查容器日志。
curl -fsS --noproxy '*' http://127.0.0.1:18080/version
docker exec -it maas-lab-gpustack-server cat /var/lib/gpustack/initial_admin_password
```

在授权终端通过 SSH 本地转发访问管理界面，远端目标填新实体机的 SSH 地址。使用 `admin` 首次登录并更换初始密码。在 GPUStack 的 **Clusters** 新建 Docker 集群，取得该集群生成的 Worker 注册 token。[GPUStack v2.2.3 安装与注册](https://github.com/gpustack/gpustack/blob/v2.2.3/docs/installation/installation.md)

```bash
# 从运维终端执行。LAB_SSH_TARGET 为新机的 SSH 目标。
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18080:127.0.0.1:18080 "$LAB_SSH_TARGET"
```

回到新机 shell，将 `LAB_IP` 设为上一步确认的本机私网 IPv4，再输入新集群 token。Worker 使用单独的身份卷。镜像默认让推理容器继承 Worker 的相关挂载，下面保留模型目录并排除原生身份卷。

```bash
: "${LAB_IP:?set the private IPv4 address of the new machine}"
MODEL_PATH="$LAB_ROOT/models/ocr"
install -d -m 0755 "$MODEL_PATH"
printf 'source_commit=%s\n' "$REV" > "$MODEL_PATH/release.txt"
chmod 0644 "$MODEL_PATH/release.txt"

read -r -s -p 'New GPUStack worker token: ' worker_token
printf '\n'
test -n "$worker_token"
printf 'GPUSTACK_TOKEN=%s\n' "$worker_token" > "$LAB_ROOT/worker.env"
unset worker_token
chmod 0600 "$LAB_ROOT/worker.env"

docker run -d --name maas-lab-gpustack-worker --restart unless-stopped \
  --network host --privileged --gpus all --env-file "$LAB_ROOT/worker.env" \
  --env GPUSTACK_RUNTIME_DEPLOY_MIRRORED_NAME=maas-lab-gpustack-worker \
  --env 'GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES=/var/run/cdi;/var/lib/gpustack' \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount type=volume,src=maas-lab-gpustack-worker,dst=/var/lib/gpustack \
  --mount "type=bind,src=$MODEL_PATH,dst=$MODEL_PATH,readonly" \
  "$GPUSTACK_IMAGE" --system-default-container-registry quay.io \
  --server-url http://127.0.0.1:18080 --advertise-address "$LAB_IP" --worker-name maas-lab-worker

docker exec maas-lab-gpustack-worker test -r "$MODEL_PATH/release.txt"
```

在 GPUStack **Workers** 确认 Worker 属于新集群、状态为 Ready、GPU UUID 和显存正确。注册不成功时先查地址和 token 所属集群，再查驱动和 Worker 日志。持有 Docker 管理权限的人员可以读取容器配置，Worker 环境文件和完整 `docker inspect` 输出按凭据保管。

## 3. 部署一个 OCR 副本并验证原生路由

从当前源码构建 GPU OCR 镜像。镜像含 RapidOCR 权重，`MODEL_PATH` 是用于 GPUStack Local Path 校验的独立目录。此处采用同机 Docker 缓存，`localhost/` 标签用于保留完整镜像名。GPUStack v2.2.3 所用运行时默认按 `IfNotPresent` 使用已存在镜像。若改为多机，先准备每台 Worker 可达的镜像仓库及一致的模型路径。[OCR Dockerfile](../../gpu-ocr/Dockerfile) · [v2.2.3 镜像分发核对](../../docs/framework/GPUSTACK-223-DISTRIBUTION-REVIEW.md)

```bash
OCR_IMAGE="localhost/maas-ocr:git-$SHORT_REV"
OCR_REVISION="ocr-git-$SHORT_REV"
OCR_ROUTE=maas-lab-ocr
docker build --pull -t "$OCR_IMAGE" gpu-ocr
docker image inspect "$OCR_IMAGE" --format '{{.Id}}'
```

在 GPUStack UI 配置以下内容。界面中的版本、镜像名和路径填入上面变量的实际值。

| 位置 | 新环境配置 |
| --- | --- |
| Inference Backends | 建自定义后端 `MaaS-Lab-OCR`，健康检查路径 `/health`。创建一个版本 `$OCR_REVISION`，镜像 `$OCR_IMAGE`，框架 `cuda`，Entrypoint `python3 /app/server.py`，Execution Command `--port {{port}} --model {{model_name}}` |
| Models | 部署 `maas-lab-ocr` 到新 Docker 集群，类别 `llm`，来源 Local Path，填绝对路径 `$MODEL_PATH`，使用上述后端版本，副本 1，关闭 CPU offload 和跨 Worker 推理。调度显存申报可从 `GPUSTACK_MODEL_VRAM_CLAIM=2147483648` 起测，实际显存另测 |
| Model Routes | 核对同名路由 `$OCR_ROUTE` 指向该部署，访问策略要求认证。自动创建时复用同名路由 |
| Users / API Keys | 给新环境非管理员身份签发有有效期的推理 key，范围仅 `inference`，Allowed Models 仅原生路由 `$OCR_ROUTE`。记录过期时间 |

等实例 `running` 且 `ready_replicas=1` 后，从新实体机检查 GPUStack 实例详情所示的 Worker 地址和实例端口。请求该实例的 `/health`，确认 `providers` 中检测、分类、识别三项均含 `CUDAExecutionProvider`。核对推理容器的镜像 ID 与构建一致。若实例未就绪，按 GPU 运行时、镜像名称/拉取、Local Path、显存分配和网络可达性逐层检查。

然后使用新建 key 检查原生 `/v1/models` 及真实 OCR。下面的函数把 key 传给 curl 的标准输入。

```bash
install -d -m 0700 -o 10001 -g 10001 "$LAB_ROOT/secrets"
install -d -m 0755 "$LAB_ROOT/probes"
install -m 0644 e2e/data/ocr-bilingual.png "$LAB_ROOT/probes/ocr-bilingual.png"
read -r -s -p 'New GPUStack inference API key: ' inference_key
printf '\n'
test -n "$inference_key"
printf '%s\n' "$inference_key" > "$LAB_ROOT/secrets/pilot-key"
unset inference_key
chmod 0600 "$LAB_ROOT/secrets/pilot-key"
chown 10001:10001 "$LAB_ROOT/secrets/pilot-key"

inference_curl() {
  printf 'Authorization: Bearer %s\n' "$(<"$LAB_ROOT/secrets/pilot-key")" |
    curl --noproxy '*' --silent --show-error --max-time 45 --header @- "$@"
}
make_chat_request() {
  python3 - "$1" "$LAB_ROOT/probes/ocr-bilingual.png" <<'PY'
import base64
import json
import sys
from pathlib import Path

model, image = sys.argv[1:]
image_url = "data:image/png;base64," + base64.b64encode(Path(image).read_bytes()).decode()
print(json.dumps({
    "model": model, "stream": False,
    "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_url}}]}],
}))
PY
}
inference_curl -f http://127.0.0.1:18080/v1/models |
  jq -e --arg route "$OCR_ROUTE" 'any(.data[]; .id == $route)'
make_chat_request "$OCR_ROUTE" > "$LAB_ROOT/native-chat.json"
inference_curl -f -H 'Content-Type: application/json' --data-binary @"$LAB_ROOT/native-chat.json" \
  http://127.0.0.1:18080/v1/chat/completions |
  jq -e '.choices[0].message.content | fromjson | .text | contains("MAAS") and contains("中文")'
```

若原生推理未通过，先修复该阶段再启动 MaaS，避免在后端未就绪时产生待核查的 UNKNOWN。超时后先查请求、实例与日志，确认执行结果后再决定是否重测。

## 4. 从源码构建 MaaS 并初始化空卷

MaaS 通过同机回环调用 GPUStack。目录以逻辑模型名 `ocr.default` 对外，映射到已验收的原生路由和 revision。绑定使用同一个非管理员推理 key、独立 project 和逻辑模型授权。E6 之后使用静态模型目录，安装路径只需要下列目录、凭据和探针文件。

```bash
openssl rand -hex 32 > "$LAB_ROOT/secrets/pepper"
cat > "$LAB_ROOT/secrets/bindings.json" <<'JSON'
{
  "credentials": [
    {
      "id": "lab-ocr",
      "project": "bare-metal-lab",
      "enabled": true,
      "secret_file": "/run/secrets/pilot-key",
      "models": ["ocr.default"]
    }
  ]
}
JSON
chown 10001:10001 "$LAB_ROOT/secrets/"{bindings.json,pepper}
chmod 0600 "$LAB_ROOT/secrets/"{bindings.json,pepper}

jq --arg revision "$OCR_REVISION" --arg route "$OCR_ROUTE" \
  '.["ocr.default"] |= (.revision = $revision | .upstream_model = $route)' \
  deploy/maas/catalog.ocr.example.json > "$LAB_ROOT/catalog.json"
chown 10001:10001 "$LAB_ROOT/catalog.json"
chmod 0644 "$LAB_ROOT/catalog.json"

unset COMPOSE_PROFILES MAAS_API_IMAGE MAAS_TASK_IMAGE
MAAS_ENV_FILE="$LAB_ROOT/app.env"
cat > "$MAAS_ENV_FILE" <<ENV
MAAS_DB_PASSWORD=$(openssl rand -hex 32)
MAAS_GPUSTACK_URL=http://127.0.0.1:18080
MAAS_SECRET_DIR=$LAB_ROOT/secrets
MAAS_CATALOG_PATH=$LAB_ROOT/catalog.json
MAAS_PROBE_DIR=$LAB_ROOT/probes
MAAS_TASK_IMAGE=cybercafe-maas-tasks:git-$SHORT_REV
MAAS_EXECUTOR_REPLICAS=1
MAAS_MAX_RUNNING=1
MAAS_MAX_PROJECT_RUNNING=1
ENV
chmod 0600 "$MAAS_ENV_FILE"

maas_compose() {
  docker compose -p maas-lab --env-file "$MAAS_ENV_FILE" -f deploy/maas/compose.yaml "$@"
}
maas_compose config --quiet
maas_compose config --images
maas_compose build --pull api
maas_compose pull db
maas_compose up -d --no-build --pull never --wait --wait-timeout 180
maas_compose ps
maas_compose images
curl -fsS --noproxy '*' http://127.0.0.1:18830/healthz
maas_compose exec -T api python -m maas_platform.cli drain-status
```

等 observer 写出首份状态后检查：

```bash
maas_compose exec -T observer cat /var/lib/maas/artifacts/.observability/status.json
```

核对 `config --images` 中应用使用唯一 Git 标签，特别检查 shell 或旧环境文件注入的 `MAAS_API_IMAGE` 等覆盖变量。用 `config --quiet` 做配置校验，完整展开会包含数据库密码。首次启动会创建 `maas-lab_postgres`、`maas-lab_artifacts`，API/执行器会应用迁移。API 与数据库分别监听 `127.0.0.1:18830` 和 `127.0.0.1:18835`。检查 API/DB healthy、执行器和 observer 持续运行、observer 状态时间戳新鲜、没有 UNKNOWN。`/healthz` 检查 API 到 DB 的连通性，推理另按第 6 节验收。

## 5. 启用私有 Nginx 入口

用仓库的 [edge.nginx.conf](edge.nginx.conf) 启动单独的 Nginx 容器。配置监听 IPv4/IPv6 回环 18831，主机需有 `::1`。镜像经审查后按摘要运行。保留路径、方法、请求体限制和认证头处理。

```bash
docker pull nginx:stable-alpine
NGINX_IMAGE=$(docker image inspect nginx:stable-alpine --format '{{index .RepoDigests 0}}')
install -m 0644 deploy/maas/edge.nginx.conf "$LAB_ROOT/edge.nginx.conf"
docker run --rm --network host --user 101:101 --read-only --tmpfs /tmp:mode=1777 \
  --mount "type=bind,src=$LAB_ROOT/edge.nginx.conf,dst=/etc/nginx/nginx.conf,readonly" \
  --entrypoint nginx "$NGINX_IMAGE" -t
docker run -d --name maas-lab-edge --restart unless-stopped \
  --network host --user 101:101 --read-only --tmpfs /tmp:mode=1777 \
  --mount "type=bind,src=$LAB_ROOT/edge.nginx.conf,dst=/etc/nginx/nginx.conf,readonly" \
  --entrypoint nginx "$NGINX_IMAGE" -g 'daemon off;'
```

本机或授权 SSH 转发后，客户 Base URL 使用 `http://127.0.0.1:18831`。运维终端执行 `ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18831:127.0.0.1:18831 "$LAB_SSH_TARGET"`。需要私有 TLS 时，按新域名/地址签发匹配 SAN 的证书，保留同一代理白名单，按 `nginx -t`、客户端校验证书、业务探针顺序验收。

## 6. 验收与证据

在新机 shell 用第 3 节的函数验证客户入口。同步调用使用 Chat，`POST /maas/v1/ocr` 在本源码版本是异步提交。任务详情用 `status`，下载用 `result_artifact_id`。

```bash
MAAS_URL=http://127.0.0.1:18831
CAPABILITIES="$MAAS_URL/maas/v1/models/ocr.default/capabilities"
test "$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' "$CAPABILITIES")" = 401
inference_curl -f "$CAPABILITIES" | jq -e 'type == "object"'
test "$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' "$MAAS_URL/healthz")" = 404

make_chat_request ocr.default > "$LAB_ROOT/maas-chat.json"
inference_curl -f -H 'Content-Type: application/json' --data-binary @"$LAB_ROOT/maas-chat.json" \
  "$MAAS_URL/gpustack/v1/chat/completions" |
  jq -e '.choices[0].message.content | fromjson | .text | contains("MAAS") and contains("中文")'

python3 - "$LAB_ROOT/probes/ocr-bilingual.png" <<'PY' > "$LAB_ROOT/ocr-task.json"
import base64
import json
import sys
from pathlib import Path

image = base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()
print(json.dumps({"model": "ocr.default", "image_base64": image}))
PY
smoke_id="bare-metal-$(openssl rand -hex 12)"
printf '%s\n' "$smoke_id" > "$LAB_ROOT/smoke-id"
test "$(inference_curl -f -o "$LAB_ROOT/ocr-submit.json" -w '%{http_code}' \
  -H "Idempotency-Key: $smoke_id" -H 'Content-Type: application/json' \
  --data-binary @"$LAB_ROOT/ocr-task.json" "$MAAS_URL/maas/v1/ocr")" = 202
task_id=$(jq -er '.id | select(type == "string" and length > 0)' "$LAB_ROOT/ocr-submit.json")
deadline=$((SECONDS + 150))
while (( SECONDS < deadline )); do
  inference_curl -f "$MAAS_URL/maas/v1/tasks/$task_id" > "$LAB_ROOT/ocr-task-status.json"
  status=$(jq -er '.status' "$LAB_ROOT/ocr-task-status.json")
  case "$status" in SUCCEEDED|FAILED|CANCELLED) break ;; esac
  sleep 2
done
jq -e '.status == "SUCCEEDED" and .attempts == 1 and .error == null' "$LAB_ROOT/ocr-task-status.json"
result_id=$(jq -er '.result_artifact_id' "$LAB_ROOT/ocr-task-status.json")
inference_curl -f "$MAAS_URL/maas/v1/artifacts/$result_id" |
  jq -e --arg revision "$OCR_REVISION" \
    '.model_revision == $revision and (.outputs.text | contains("MAAS") and contains("中文"))'
inference_curl -f "$MAAS_URL/maas/v1/tasks/$task_id/usage" |
  jq -e --arg task "$task_id" '.source_id == $task and .successful_images == 1'

maas_compose exec -T api python -m maas_platform.cli drain-status
```

验收记录包含源码 commit、镜像摘要或本地 image ID、OS/驱动/GPU、集群/Worker/模型/路由及实际 revision、同步结果、任务 ID、尝试次数、结果下载和计量。另从未授权的主机或网络确认 18080、18830、18831、18835 以及 Worker/推理端口不可达，尤其要检查同网段来源。回环 18831 通过 SSH 转发后的可用性单独验证。

如果提交响应未知，保留已记录的 `smoke-id` 和请求体，以原幂等键核对任务。同步结果未知时先检查活动记录和上游实例。UNKNOWN 的解除需按[运维手册](../../docs/handbooks/OPERATIONS.md#6-全局暂停与unknown处置)取得执行终止证据。

## 7. 后续测试组件与持久数据

| 测试目标 | 基线通过后按需增加 |
| --- | --- |
| 多机与冷缓存 | 新增 Worker 需规划私网 GPUStack 地址、Worker 回连端口、可信 CA 和镜像/模型分发。克隆自动入网及私有仓库各有独立部署步骤。[Bootstrap](../bootstrap/README.md) · [Registry](REGISTRY-SERVICE.md) · [模型存储](MODEL-STORAGE-V1.md) |
| GPU 上线调节与身份 | 调节器按当前提交构建并从 `observe` 开始验收。默认 worker 使用 GPU UUID 的 name/external_id，保留 enrollment/handoff；不安装、不部署旧 `maas-gpustack-worker-retirement` 插件，长期离线不触发自动删除身份。[规模调节器](REPLICA-REGULATOR.md) · [旧退役插件归档说明](../../plugins/gpustack-worker-retirement/README.md) |
| E17 统计和日报 | Compose 的 `e17`、`e17-daily` profile 独立启用。设置可由 UID 10001 读取的私有 `E17_GPUSTACK_KEY_PATH`，使用独立的 GPUStack 管理读取凭据。统计页另需 `E17_QUERY_KEY_FILE` 指向 API 容器内可读的私有文件。日报查询本身由私有入口限制，接口无需 key。[采样与统计](../../docs/framework/E17-GPU-TIME.md) · [日报协议](../../docs/api/GPU-DAILY-CONSUMER.md) |
| 容器内下游、宿主机监控和备份 | 先按目标网络生成专用入口和访问规则，或配置独立监控/备份。现有安装脚本包含原试点路径及目标约束，先移植配置并复核目标。[容器入口](CONTAINER-HTTP.md) · [发布、备份与恢复](../../docs/framework/P4-RELEASE.md) |

暂时停用时先停止客户端流量和 `maas-lab-edge`，再执行 `maas_compose exec -T api python -m maas_platform.cli pause --reason 'lab shutdown'`。用 `drain-status` 确认排空且无 UNKNOWN，再用 `maas_compose stop` 停止任务服务。按 GPUStack UI 受控停用 OCR 部署后，依次停止 Worker、Server。

恢复时依次启动 Server、Worker 和 OCR，等原生推理通过再启动 MaaS。用 `maas_compose exec -T api python -m maas_platform.cli resume --credential-id lab-ocr --probe-image /run/maas-probe/ocr-bilingual.png` 恢复准入后启动 `maas-lab-edge`。保留 MaaS 的 PostgreSQL 与 artifact 卷、GPUStack Server 与 Worker 卷以及私有配置。备份时成对保留 MaaS 数据库与 artifact，另行备份 GPUStack 内置数据库、数据卷及发布资产。容器级恢复的通过记录与整机启动验证分开保存。
