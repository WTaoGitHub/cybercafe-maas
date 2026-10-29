# 普通用户手册

> **2026-10-29读者说明**：本手册中的调用方指持有MaaS接入凭据的下游服务/集成开发者；最终用户使用下游自身的账号、Key和业务入口。MaaS只校验已登记服务凭据及模型权限，并按项目共享容量保护。项目及额度由管理员交付，当前没有公开身份/额度查询接口；参见[接入约定](../framework/SERVICE-ACCESS-POLICY.md)及[最新API](../api/README.md)。

> E6迁移说明（2026-10-22）：正式版本e6-r1已上线，已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](../framework/E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


适用于当前私有OCR服务（2026-10-21），不需要GPUStack管理员账号。此服务做中英文普通文字提取，不提供对话、翻译、文档版式还原、图像/视频生成或批处理ZIP下载。

## 1. 使用前准备

向管理员取得：

- 可访问的私有API地址（下例 `https://127.0.0.1:18832`；若是隧道，需先由管理员安排访问）。
- 用于校验该服务的公共证书，以及受限推理API key和到期时间。
- 允许的逻辑模型名，当前为 `ocr.default`；项目额度及维护时间。

没有公开注册/登录门户。密钥标识你的project，不需要也不能用请求头自行指定project。同project成员可访问该project的已知任务/文件ID；不要把project当个人私有空间。

把key单独保存在私有文件、权限600，不写进源码、命令行参数、Git或截图。公共证书不是TLS私钥。不要关闭证书验证；证书失配或过期联系管理员。

图片要求：PNG/JPEG，单图不超过10MiB、解码不超过2000万像素（最终以上游校验为准）。建议清晰、文字完整、不过曝；PDF先在本地逐页渲染为图片。当前每任务一张图片，不支持一次上传100张形成批次；多文件需分别提交并自行记录ID。

方向保护会对部分文本行尝试180°识别纠正，不能保证任意旋转或模糊文本都正确；重要结果仍需人工复核。

## 2. 两种调用方式

| 方式 | 使用场景 | 返回内容 |
|---|---|---|
| 同步OCR | 单张图片，希望等待一次响应 | Chat completion中的OCR JSON |
| 异步任务 | 需要任务ID、排队、重查与持久结果 | 先返回任务，再查状态并下载结果 |

同步只支持 `stream=false`，没有自动幂等。异步提交使用Idempotency-Key，适合网络异常后重查同一请求。两种方式都受同一项目并发限制；异步被受理不代表立即推理。

示例使用Python3.11+与httpx。把证书、key和图片放在自己的私有工作目录，修改下面路径；**不要把真实key直接填进代码**。如需依赖，在自己的虚拟环境安装httpx。

## 3. 同步OCR示例

同步接口不先上传artifact，而是把图片编码成base64 data URL。示例只发送一次：

```python
from pathlib import Path
import base64
import json
import ssl
import httpx

BASE_URL = "https://127.0.0.1:18832"
KEY_FILE = Path("/private/path/inference-key")
CERT_FILE = Path("/private/path/server.crt")
IMAGE_FILE = Path("/private/path/sample.png")
media = "image/png"  # JPEG图片改为image/jpeg
raw = IMAGE_FILE.read_bytes()
if not raw or len(raw) > 10 * 1024 * 1024:
    raise ValueError("图片必须非空且不超过10MiB")
context = ssl.create_default_context(cafile=str(CERT_FILE))
headers = {"Authorization": "Bearer " + KEY_FILE.read_text().strip()}
body = {
    "model": "ocr.default",
    "stream": False,
    "messages": [{"role": "user", "content": [{
        "type": "image_url",
        "image_url": {"url": "data:" + media + ";base64," + base64.b64encode(raw).decode()}
    }]}]
}
with httpx.Client(base_url=BASE_URL, verify=context, headers=headers,
                  timeout=40, trust_env=False, follow_redirects=False) as client:
    response = client.post("/v1/chat/completions", json=body)
    response.raise_for_status()
    result = json.loads(response.json()["choices"][0]["message"]["content"])
    print(result["text"])
```

网络超时/断连时，不要写自动重试循环：服务端可能已执行。保留时间和响应中的X-Request-ID，联系管理员确认。若必须保证可重查，优先使用下面的异步任务方式。

## 4. 异步任务：上传 → 提交 → 查询 → 下载

协议步骤：

1. `POST /maas/v1/artifacts`，Content-Type为image/png或image/jpeg，body为原始图片字节（不是multipart）。保存返回的 `id`。
2. `POST /maas/v1/tasks`，发送逻辑模型、artifact引用和参数，附稳定Idempotency-Key。新任务202，同内容重提200。
3. `GET /maas/v1/tasks/{id}`，轮询直到SUCCEEDED/FAILED/CANCELLED；等待时适当间隔。
4. SUCCEEDED后取 `result_artifact_id`，`GET /maas/v1/artifacts/{id}`下载完整JSON结果。

下面示例把幂等键及**完全相同的请求体**先落盘，提交响应丢失后重跑可找回原任务。它不会因为OCR失败而换键重做。每张新图片使用新的工作目录；同目录重跑表示查同一个请求。

```python
from pathlib import Path
import hashlib
import json
import os
import ssl
import time
import uuid
import httpx

BASE_URL = "https://127.0.0.1:18832"
KEY_FILE = Path("/private/path/inference-key")
CERT_FILE = Path("/private/path/server.crt")
IMAGE_FILE = Path("/private/path/sample.png")
MEDIA = "image/png"  # JPEG改为image/jpeg
WORK = Path("/private/path/ocr-job-001")
WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
JOB = WORK / "request.json"
MODEL = "ocr.default"

def save_private(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)

raw = IMAGE_FILE.read_bytes()
if not raw or len(raw) > 10 * 1024 * 1024:
    raise ValueError("图片必须非空且不超过10MiB")
image_sha = hashlib.sha256(raw).hexdigest()
context = ssl.create_default_context(cafile=str(CERT_FILE))
headers = {"Authorization": "Bearer " + KEY_FILE.read_text().strip()}
with httpx.Client(base_url=BASE_URL, verify=context, headers=headers,
                  timeout=40, trust_env=False, follow_redirects=False) as client:
    if JOB.exists():
        job = json.loads(JOB.read_text())
        if (job["image_sha256"] != image_sha or job["base_url"] != BASE_URL
                or job["body"]["model"] != MODEL):
            raise ValueError("工作目录已属于其他请求，请为新图片使用新目录")
    else:
        uploaded = client.post("/maas/v1/artifacts", content=raw,
                               headers={"Content-Type": MEDIA})
        uploaded.raise_for_status()
        job = {
            "base_url": BASE_URL, "image_sha256": image_sha,
            "idempotency_key": "manual-" + uuid.uuid4().hex,
            "body": {"model": MODEL,
                     "input": {"image": {"artifact_id": uploaded.json()["id"]}},
                     "parameters": {}, "timeout_seconds": 300, "max_attempts": 1}
        }
        save_private(JOB, json.dumps(job).encode())
    response = client.post("/maas/v1/tasks", json=job["body"],
                           headers={"Idempotency-Key": job["idempotency_key"]})
    response.raise_for_status()
    task = response.json()
    job["task_id"] = task["id"]
    save_private(JOB, json.dumps(job).encode())
    print("task_id:", task["id"])
    until = time.monotonic() + 310
    while task["state"] not in ("SUCCEEDED", "FAILED", "CANCELLED"):
        if time.monotonic() >= until:
            raise TimeoutError("本地等待结束；请求已保存，稍后重跑查同一任务")
        time.sleep(2)
        response = client.get("/maas/v1/tasks/" + task["id"])
        response.raise_for_status()
        task = response.json()
    if task["state"] != "SUCCEEDED":
        raise RuntimeError((task["state"], task.get("error_code")))
    result = client.get("/maas/v1/artifacts/" + task["result_artifact_id"])
    result.raise_for_status()
    save_private(WORK / "result.json", result.content)
    print("已保存:", WORK / "result.json")
```

同一个幂等键必须重发**完全相同**的请求体；重新上传取得新artifact ID后再复用旧键会409。上传本身没有幂等保证，中断后重传可能产生额外未引用文件，由TTL清理；不会因此自动创建推理任务。

示例假定同一工作目录只由一个客户端进程操作。不要同时运行两份脚本争用request.json；不要用同目录处理新图片或更换project。业务系统应把幂等记录纳入自身持久存储。

## 5. 能力查询、取消与结果结构

使用同一client/Authorization，可请求 `GET /maas/v1/models/ocr.default/capabilities` 获取revision、输入schema和运行预算。当前返回execution_modes只列async；同步OCR按本手册的单独接口调用。没有公开模型列表或任务列表接口，需要管理员交付模型名，并自行保存task ID。

取消请求：`POST /maas/v1/tasks/{task_id}/cancel`，无需JSON body。QUEUED/RETRY_WAIT可转CANCELLED；RUNNING先转CANCEL_REQUESTED，不能保证立即中断GPU。客户端关闭轮询也不会自动取消任务。

| 状态 | 含义 |
|---|---|
| QUEUED / RETRY_WAIT | 等待执行/有限重试机会，已消耗总deadline时间 |
| RUNNING | 执行器已领取，不代表已完成GPU推理 |
| CANCEL_REQUESTED | 已请求取消，仍可能有后台工作 |
| SUCCEEDED | 结果已提交，可在保留期内下载 |
| FAILED / CANCELLED | 终态；检查error_code，别盲目重做UNKNOWN相关失败 |

异步下载JSON的主要结构：

```json
{
  "outputs": {
    "text": "识别文字",
    "lines": [{"text": "识别文字", "score": 0.98, "box": [[0, 0], [100, 0], [100, 20], [0, 20]]}],
    "elapsed_ms": 120.0,
    "node": "执行节点标识"
  },
  "model_revision": "已登记版本",
  "attempt": 1,
  "usage": {"unit": "task", "quantity": 1}
}
```

示例数值只解释格式，不是性能承诺。lines为文本框/文字/置信度，空图可能返回空文字/空列表；score不保证识别正确。没有token用量；elapsed_ms不代表整个排队和网络耗时，也不是计费GPU秒。

## 6. 配额、等待与保留

默认每project同步/异步合计并发1、等待队列100；全局限制可能先达到。受理异步任务后会排队，默认总截止120秒，示例显式设为300秒；排队也计时。模型推理另有25秒预算，增加任务总截止不会无限延长单次OCR运行预算。

默认上传输入24小时保留，活动任务引用期间不会被清理；结果从创建起24小时。请及时下载到自己的安全存储。文件过期下载410，即使任务仍显示SUCCEEDED也不代表结果还可下载。已上传但没有提交任务的文件也会占用额度直至到期清理。

同步重复发送是新请求；异步同project同幂等键同内容返回原任务。成功用量代表服务端成功，不等于你已收到结果。当前没有客户账单/付费页面或用户侧额度查询API，需向管理员确认。

## 7. 错误处理与求助

业务错误示例：

```json
{"error":{"code":"QUEUE_FULL","message":"Task queue capacity reached","retryable":true,"request_id":"req_..."}}
```

| 现象/错误 | 处理建议 |
|---|---|
| TLS错误/连接失败 | 检查授权私有访问和证书；不要关闭证书校验 |
| 401 / AUTH_DENIED / AUTH_REVOKED / MODEL_FORBIDDEN | 请管理员检查key登记、到期及模型权限，不换成管理key |
| 404 | 检查逻辑模型、task/artifact ID与project；根路径404属预期 |
| 409 IDEMPOTENCY_CONFLICT | 使用保存的原请求体查原任务；新业务请求用新幂等键 |
| 413 / INPUT_SIZE / REQUEST_SIZE | 缩小图片/请求体；不要把多图塞入单任务 |
| 429 QUEUE_FULL / SYNC_CAPACITY / STORAGE_QUOTA | 有界退避或联系管理员；已知未受理请求与发送不明的情况分开处理 |
| 410 RESULT_EXPIRED | 文件超过保留期；向管理员咨询，不能保证找回 |
| MAINTENANCE_PAUSED / PREFLIGHT_* | 暂停提交新推理，等待管理员确认恢复 |
| UNKNOWN_BACKEND_ACTIVITY / BACKEND_QUARANTINED | 后台可能仍在执行；不要自动重发，联系运维确认 |
| FAILED / DEADLINE_EXCEEDED | 总等待/执行截止已到，查看原因；不要把所有失败都当作可安全重试 |

`retryable=true`不代表任何超时都可以无限重试。异步提交响应丢失时用原幂等键和完全相同的body重查；拿到task ID后优先GET查询。同步发送结果不明不应自动重发。Nginx层错误可能不是JSON，先检查HTTP状态及Content-Type。

联系支持时提供：时间、逻辑模型、HTTP状态、error.code、X-Request-ID、task ID及是否已下载结果。**不要提供API key**。图片/识别文本如含敏感信息，先确认支持人员授权再提供。

GPUStack Playground是管理员诊断工具，不能绕过暂停/额度作为普通用户的备用入口。正式服务异常由管理员与运维处理。

## 消费者兼容同步OCR（2026-10-27）

新增POST /ocr（别名/v1/ocr），请求model=ocr.default及image_base64或image_url二选一，直接返回顶层lines；不需要先上传artifact。只接受PNG/JPEG，远程URL仅公开HTTP(S)80/443且无重定向；原Chat/异步方式保留。request_id只是关联字段，没有同步幂等；不要对未知完成直接重发。参数、示例和错误见[完整契约](../api/CONSUMER-OCR.md)。私有访问地址与受限key由管理员提供，不将001回环地址直接填给另一台机器。
