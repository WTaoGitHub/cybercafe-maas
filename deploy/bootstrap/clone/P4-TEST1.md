# E5.F3 无盘接续 P4 试点

目标是在 `test1` 的专用单卡上，验证两台可销毁 VPC 先后接入时保留同一个 GPUStack `worker_id`，并恢复专用测试模型的 OCR。当前 E5 活动发布使用 `native-independent` 和 HTTP 原生地址。 [试点跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)

正式 OCR 常态目标为 4 个副本。2026-11-10 将 GPUStack 部署的 `replicas` 设为 4，调节器策略修订 5 将最小值、最大值和自动缩容下限均设为 4。正式 selector 在原有 `arch` 和 `os` 上增加 `maas-pool=production`。验收时两台生产 worker 共运行 4 个副本，候选 A 上无正式实例，恢复接单的真实 OCR 探针返回预期的 46 行。后续试点验收和回退均以正式 OCR 4/4 为基线。 [副本策略跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

## 放行前准备

1. **资源归属。** 记录一张专用 GPU、可销毁的 A/B VPC、各自的 SSH 入口及同卡换绑方式。由能操作平台的人提供旧 VPC 的真实退出或隔离证据。XZ-31-001 和 XZ-31-002 排除出换绑实验。
2. **正式与试点放置。** 对拟接入的 worker 复核正式 selector 和副本调节器的容量、压力采样范围，确认正式 4/4 仅落在带 `maas-pool=production` 标签的节点。试点 worker 单独加试点池标签，测试模型按试点池和精确 `worker-name` 固定到该记录。可先通过 P1 建立未就绪占位记录，在发启动许可前由管理端补齐试点标签。核对副本调节器对新 GPU 的识别和压力门槛后才允许试点 worker 就绪。
3. **Server 组件。** 锁定现有 GPUStack v2.2.3 Server 镜像和已部署插件。构建带 enrollment 0.3.0 的同版本镜像，保留生产配置、数据库和监控。上线切换沿用暂停接单、排空、保留原容器及回退探针的流程。单独配置 `test1` 的 `required` 策略和 MaaS 服务密钥。 [生产组件切换边界](../../maas/REPLICA-REGULATOR.md)
4. **独立发布。** 单独启动试点配置服务，独立发布目录、签名配置和授权状态。签名 `gpu-external-id` 发布引用试点 HTTPS 原生地址。现有 E5 活动发布继续面向普通 worker。MaaS 的 `worker-enrollment`、`worker-handoff`、`worker-retire` 只通过服务内部链路访问。 [E5 无盘引导配置](README.md#无盘-gpu-接续试点)

## 试点 HTTPS 原生入口

使用 [Nginx 模板](pilot-native.nginx.conf.template) 绑定选定的私网地址和独立端口。`P4_NATIVE_UPSTREAM_PORT` 指向同机回环 GPUStack API，`P4_NATIVE_LISTEN` 填写试点监听地址和端口。Nginx 进程需能访问该回环地址。将证书链、私钥和 `clients.conf` 分别只读挂入 `/run/maas-p4/tls.crt`、`/run/maas-p4/tls.key`、`/run/maas-p4/clients.conf`。证书 SAN 覆盖试点配置中的主机名或 IP。`clients.conf` 只为已核验的试点来源写入 `allow` 规则，防火墙使用同一范围。私钥保留在服务端。

```sh
envsubst '$P4_NATIVE_LISTEN $P4_NATIVE_UPSTREAM_PORT' \
  < pilot-native.nginx.conf.template > "$P4_PRIVATE_NGINX_CONFIG"
```

用试点 Nginx 镜像执行 `nginx -t`。上线前在可销毁环境核对可信 `/version`、错误证书和未授权来源拒绝、只允许 worker 相关路由、原生注册及 `/connect` WebSocket。测试 VPC 获得入口前，核对它只能经试点 HTTPS 地址访问原生 API，并验证 GPUStack 私有接口和登录入口在该监听地址被拒绝。

## 同卡接续验收

| 顺序 | 验收内容 |
| --- | --- |
| A 初次接入 | 核对 GPU 归属、完整 UUID 和空原生数据目录。由 MaaS 发启动授权，记录 `worker_id`、会话、GPU、地址和 `ready`。只启动专用 OCR 部署，核对实例实际落在 A，完成一笔真实 OCR。 |
| A 退出 | 暂停测试模型新请求并排空，核对旧容器、执行、路由和隧道。确认平台侧 A 退出或受控隔离，再等待原生 `not_ready` 和心跳过期。记录实际退出证据编号。 |
| B 接续 | 同一卡分配给空身份目录的 B。凭上一会话、固定 worker ID 和退出证据申请授权。核对同一 `worker_id`、同一 GPU UUID、新实例地址、新凭据及 `ready`，并验证旧凭据失效。确认测试模型和路由已在 B 恢复，完成真实 OCR，记录注册和业务恢复耗时。 |
| 正式负载复核 | 每阶段比较正式模型的副本、放置和真实 OCR 探针。出现偏离时停止试点并按切换窗口回退。 |

### 2026-11-10 VPC A 首次接入

现网 GPUStack v2.2.3 已启用 enrollment 0.3.0 的受管注册。独立 HTTPS 入口与 E5 配置服务仅放行 A，A 已完成签名验核，连接原生 HTTP 端口被拒绝。使用 A 的原生凭据通过试点入口发起 WSS 握手，返回 101。

停止并保留 A 上的旧 E5 容器和身份卷。等原生状态变为 `not_ready` 后，从空 tmpfs 原生目录启动受管 worker。MaaS 发放单次启动许可，GPUStack 确认 `name`、`external_id` 与实际 GPU UUID 一致。新记录以 `worker_id=11` 进入 `ready`。

独立 OCR 部署固定到试点池与该 worker。实际 GPU OCR 返回 HTTP 200 和预期的 46 行。正式 OCR 复核仍为 4/4，MaaS 正式 OCR 请求也返回 HTTP 200 和 46 行。调节器采集和状态检查已切到新 worker ID，修订 5 保持生效。

### 2026-11-10 005 重启后的身份恢复

管理员重启 005 前，试点 OCR 模型 44 降到 0，试点 worker 停止并进入 `not_ready`。重启后直连核对 `boot_id` 已变化，完整 GPU UUID 与基线一致，tmpfs 原生身份目录为空。手动下发 E5 引导配置和绑定上一会话的一次性接续许可后，GPUStack 原记录 `worker_id=11` 恢复 `Ready`。新凭据查询返回 HTTP 200，旧凭据在同一查询上返回 HTTP 401。

模型 44 恢复 1 个副本，新实例落在 worker 11，真实 CUDA OCR 返回 HTTP 200 和 46 行。正式 OCR 复核为 4/4，正式请求也返回 HTTP 200 和 46 行。试点压力采样已恢复，调节器修订 5 状态检查通过。

操作方补充确认，005 启动前后对应两个不同的 VPC 实例，来宾主机名沿用 005。管理员说明平台会分配原 GPU，两次主机实测的完整 GPU UUID 一致。结合上述 worker、凭据和 OCR 结果，P4 的 A→B 跨实例接续功能验收通过。本次使用人工下发 E5 引导配置和接续许可。 [试点跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)

### 2026-11-10 P5 启动许可自动触发

管理员再次启动 005 后，核对 `boot_id` 已变化、GPU UUID 一致且原生身份目录为空。在现有克隆镜像启动后手动安装 `d09d274` 版 E5，启用 `maas-worker-bootstrap.path`。许可交付前，路径监视处于活动状态，引导服务和 worker 均未启动。

交付工具用错误的 `boot_id` 验证时返回 `DELIVERY_TARGET_CHANGED`，未签发许可。用核对过的启动 ID 交付许可后，systemd 路径单元自动触发引导服务，记录 `trigger=boot`，原 `worker_id=11` 恢复 `Ready`。新凭据查询返回 HTTP 200，旧凭据从 HTTP 200 变为 HTTP 401。重复交付复用同一份许可和同一会话。

试点模型 44 恢复 1/1，实例落在 worker 11，真实 CUDA OCR 返回 HTTP 200 和 46 行。正式 OCR 保持 4/4，真实请求返回 HTTP 200 和 46 行。试点压力采样已恢复，调节器修订 5 的状态检查通过。本次范围为安装 E5 后的许可交付与自动引导，镜像预置 E5 的开机流程待单独验证。 [试点跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)

## 退役与回退

1. 停止发放试点授权。排空专用测试模型并将副本降为零，删除专用模型，复核原生实例、显式绑定、路由和旧 VPC 隧道已清理。
2. 通过同机私有 MaaS 凭据调用 `POST /internal/maas/worker-retire`，提交 `worker_id`、`gpu_uuid`、当前 `session_id` 和 `fence_receipt`。从未发许可的占位记录使用空 `session_id`。接口核对绑定、心跳、隧道和模型引用后撤销身份并硬删除原生记录。校验原 ID 404、旧 token 401、名称已释放。该接口只处置 GPUStack 记录，VPC 平台侧退出由现场人员确认。
3. 需要退回普通接入时，使用独立干净的试点 VPC 和获准的 `native-independent` 配置，验证重新获得自己的 worker ID。保持生产插件与数据库挂载。关闭试点入口前复核正式 OCR 与采集状态。

原生 `DELETE /v2/workers/{id}` 在 v2.2.3 对有 `external_id` 的 Docker worker 走供应商软删除。隔离复现中返回 200 后，名称仍冲突，旧 token 仍可上报。启用受管策略后，运维通过上述退役入口处置。 [退役跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/34)
