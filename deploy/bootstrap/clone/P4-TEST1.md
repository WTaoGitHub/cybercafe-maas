# E5.F3 无盘接续 P4 试点

目标是在 `test1` 的专用单卡上，验证两台可销毁 VPC 先后接入时保留同一个 GPUStack `worker_id`，并恢复专用测试模型的 OCR。只读预检确认 `test1` 仍承载正式 OCR，9 个副本均已运行。当前正式 selector 只有 `arch` 和 `os`，试点使用的 Linux x86_64 worker 也会匹配。当前 E5 活动发布使用 `native-independent` 和 HTTP 原生地址。 [试点跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)

## 放行前准备

1. **资源归属。** 记录一张专用 GPU、可销毁的 A/B VPC、各自的 SSH 入口及同卡换绑方式。由能操作平台的人提供旧 VPC 的真实退出或隔离证据。XZ-31-001 和 XZ-31-002 排除出换绑实验。
2. **正式与试点放置。** 对拟接入的 worker 复核正式 selector 和副本调节器的容量、压力采样范围。现有 selector 无法隔离时，先在维护窗口验证显式生产池标签及正式 selector 的更新，再复核正式 9/9。试点 worker 单独加试点池标签，测试模型按试点池和精确 `worker-name` 固定到该记录。可先通过 P1 建立未就绪占位记录，在发启动许可前由管理端补齐试点标签。核对副本调节器对新 GPU 的识别和压力门槛后才允许试点 worker 就绪。
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

## 退役与回退

1. 停止发放试点授权。排空专用测试模型并将副本降为零，删除专用模型，复核原生实例、显式绑定、路由和旧 VPC 隧道已清理。
2. 通过同机私有 MaaS 凭据调用 `POST /internal/maas/worker-retire`，提交 `worker_id`、`gpu_uuid`、当前 `session_id` 和 `fence_receipt`。从未发许可的占位记录使用空 `session_id`。接口核对绑定、心跳、隧道和模型引用后撤销身份并硬删除原生记录。校验原 ID 404、旧 token 401、名称已释放。该接口只处置 GPUStack 记录，VPC 平台侧退出由现场人员确认。
3. 需要退回普通接入时，使用独立干净的试点 VPC 和获准的 `native-independent` 配置，验证重新获得自己的 worker ID。保持生产插件与数据库挂载。关闭试点入口前复核正式 OCR 与采集状态。

原生 `DELETE /v2/workers/{id}` 在 v2.2.3 对有 `external_id` 的 Docker worker 走供应商软删除。隔离复现中返回 200 后，名称仍冲突，旧 token 仍可上报。启用受管策略后，运维通过上述退役入口处置。 [退役跟踪](https://github.com/WTaoGitHub/cybercafe-maas/issues/34)
