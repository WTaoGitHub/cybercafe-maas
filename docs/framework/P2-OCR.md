# P2 首批：固定版本 OCR connector 与未知执行隔离

2026-10-20。实现及受控实机验证完成；P2 整体仍在进行。

报告：[HTML](../../results/maas-p2/REPORT.html) / [Markdown](../../results/maas-p2/REPORT.md)。

## 接入方式

任务 API、执行器、PostgreSQL、artifact 存储保持原有职责。新增 `gpustack.ocr` 适配器通过 GPUStack 的 `/v1/chat/completions` 发起非流式 OCR，不直接选择模型实例，不使用管理员 key 推理。

```json
{
  "model": "ocr.default",
  "input": {"image": {"artifact_id": "art_由上传接口返回"}},
  "parameters": {},
  "timeout_seconds": 60,
  "max_attempts": 3
}
```

先以 `image/png` 或 `image/jpeg` 上传图片，再使用 `Idempotency-Key` 提交任务。查询任务，成功后下载 `result_artifact_id`。结果 `outputs` 包含 `text`、`lines`（box/text/score）、`elapsed_ms` 和 `node`，外层保留模型 revision、attempt 和任务用量单位。客户不能指定图片 URL、任意上游地址或模型实例。

目录配置示意（路径指向执行器内的受信任只读文件）：

```json
{
  "ocr.default": {
    "driver": "gpustack.ocr",
    "revision": "ocr-p2-r1",
    "upstream_model": "maas-ocr-p2-r1",
    "max_runtime_s": 25,
    "pin_file": "/trusted/pin.json",
    "preflight_file": "/trusted/preflight.json"
  }
}
```

还需在 credential binding 的 `models` 中授权逻辑模型名，并给客户 inference key 授权对应 GPUStack 路由名。执行前重新验证客户 key 与模型权限；不把管理员凭据挂载进任务容器。

## 版本与派发预检

- 专用标签 `quay.io/cybercafe-maas-ocr:p2-r1` 在两个节点具有完全相同的 image ID，未覆盖原 `0.3` 标签。
- pin 绑定 GPUStack v2.2.3、route/target/model/backend 配置摘要、revision、镜像和节点集合；目录模型名/revision 必须与 pin 一致。
- 受信任 collector 读取管理员控制面元数据、真实 Higress 策略及节点 Docker 镜像/运行容器 ID，原子更新预检文件。任务进程只读文件，不读取 SSH 或管理 key。
- 控制面快照最多 30 秒，镜像观察最多 60 秒；缺失、过期、漂移、非批准节点集合或非批准网关策略均阻止发起推理。
- 这是有界新鲜度预检，仍有观察到发送之间的竞态窗口，并非 GPUStack 提供了原子的不可变部署。运维修改须先暂停派发，禁止在请求中静默重绑版本。

本轮 `refresh_preflight.py --loop` 是本地临时测试 collector，退出后文件会过期。当前没有交付常驻 collector 或长期开放的 OCR 任务服务；常在线控制端采集及最小权限节点清单通道是下一批部署前置。

## 执行与隔离语义

1. 本地验证、权限和 pin 通过后，先持久提交 `backend_activity=DISPATCHING`，提交结果不确定时不发送 HTTP。
2. HTTP 客户端发送一次，不跟随重定向、不读环境代理、不重试。请求含绝对总时限，慢速分块响应也不能无限延长；结果大小有界。
3. 当前 v2.2.3 Higress 仍保留 P0 实测的 `non_idempotent` 和额外 2 次尝试配置，单个任务 attempt 最多可能触发 3 次上游发送。不能宣称 GPU 计算只执行一次，不能把成功任务计量当作实际 GPU 调用数。
4. 完整成功响应和明确的 400/401/403/404/413/415/422 拒绝可确认此同步调用已结束。503/502、传输超时、响应未读完、派发后执行器失联都不能确认后台停止；不自动重放。
5. 未确认的任务终态为 `FAILED`，错误码 `UNKNOWN_BACKEND_ACTIVITY`；独立持久活动记录保持 `UNKNOWN`，全局阻止新领取/远端派发，包括 mock；隔离提交和派发许可使用同一事务锁，避免并发越过隔离。调用普通 resume 不能清除此隔离。
6. 完整响应已确认、结果尚未提交时执行器崩溃，恢复后报告 `RESULT_NOT_COMMITTED`，也不重新执行 GPU。

`FAILED` 或 `CANCELLED` 不应被当作可以关闭机器的充分证据。维护前必须同时检查未知活动。实际 HTTP 已返回时取消可落为 CANCELLED；仍无法确认后台活动时优先保留 UNKNOWN，不能把取消请求当作后端停止确认。

## 人工解除隔离

在私有管理环境中读取 `backend_activity` 的 UNKNOWN token，核实对应专用后端进程已停止、不会再恢复执行，然后运行：

```sh
python -m maas_platform.cli resolve-backend --token ATTEMPT_TOKEN \
  --reason '已将专用模型缩容到零，并在两个节点确认所有对应容器进程已停止'
```

CLI 使用相同私密配置，要求明确原因并记录到数据库，只解除活动占用，不改变原失败任务或自动重试。单纯等待 lease 过期、网关断开、健康检查成功，都不是停止证明。本轮实机验证是在缩容到零并检查两个 Docker 引擎没有专用运行容器后才解除隔离。

## 工具和边界

`scripts/maas/p2/` 中包含镜像一次性 LAN 传输、独立测试部署、受信任快照、双节点暂停故障测试和清理工具。均针对本轮两个节点的受控环境，不能当作生产部署器直接复用。`prepare_target.py` 拒绝覆盖已有登记；本轮清理后旧 pin/target 文件仅作为证据，不能重新绑定新部署继续使用。

下一批：真实 GPUStack worker 离线/实例重建故障矩阵；任务链路中的权限撤销和部署漂移实机验证；把 collector 放到常在线控制端并收紧节点清单访问权限。之后进入 P3 维护与运营边界，P4 再做发布、备份恢复和 8 小时耐久。
