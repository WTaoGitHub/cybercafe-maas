# 新克隆通过 Higress 的真实验收

2026-11-11 · GPUStack v2.2.3 · [Issue #59](https://github.com/WTaoGitHub/cybercafe-maas/issues/59)

新克隆已注册为集群 `maas-baremetal-workers`（ID 1）的 worker 3，原生状态为 Ready / direct。真实 CUDA OCR 经常驻 Higress 和 MaaS 异步接口均已成功，HTTP、HTTPS 私有业务入口已恢复。此轮使用真实注册记录与真实模型实例，补上了此前隔离试验的这一项限制。

## 验证结果

| 检查 | 实测结果 |
| --- | --- |
| 新克隆 GPU | RTX 4080 SUPER，本机 UUID 与 GPUStack 上报一致 |
| 启动配置 | 集群默认 direct；worker 无显式 CLI / 环境变量覆盖，最终上报 direct |
| E5 引导与边界服务 | 引导 Result=success、退出码 0；边界服务 active |
| 模型 | maas-ocr（部署 1），期望 / 就绪 1/1；实例 2 在 worker 3 上 Running |
| 镜像取得 | 本轮实例日志显示 pause 辅助镜像与固定 OCR 镜像拉取完成，未人工补缓存 |
| 引擎健康 | HTTP 200；检测、方向分类、识别均报告 CUDAExecutionProvider |
| 常驻网关 | embedded Higress，版本 v2.2.3；响应 Server=istio-envoy |
| 实际路由 | Envoy 后端地址和端口匹配当前真实模型实例 |
| 授权原生 OCR | HTTP 200、2 行识别结果 |
| 匿名 / 错误 Key | 均 HTTP 401 |
| 有效 Key 但无模型权限 | HTTP 403；测试临时 Key 已撤销 |
| MaaS 恢复探针 | 注册逻辑模型 ocr.default 推理成功，2 行 |
| MaaS 异步 OCR | 202 受理，最终 SUCCEEDED、2 行、successful_images=1 |
| 数据库计量 | 对该任务核对一条成功用量、数量 1；未重放请求 |
| 私有 HTTP / HTTPS | 授权能力查询 200；空 OCR 请求体 422，已解除原维护 503 拦截 |
| 非受信来源直连模型端口 | 3 秒内无法建立请求；未扩大模型端口访问范围 |

Higress 沿用当前 enrollment 镜像与数据挂载，替换的是控制端 GPUStack 容器的启动参数。未取消 worker 代理鉴权；direct 模式使网关直接访问受网络边界保护的模型端口。MaaS 的推理凭据及模型权限校验继续生效。

## 收尾运行状态

MaaS 已解除暂停，维护 generation 为 2，UNKNOWN=0，应用监控无告警。压力采样已使用新节点的固定 SSH 主机公钥及来源受限命令，样本覆盖 worker 3 并持续更新。

MaaS 调节策略从维护时的 Observe 修订 2 恢复为 Apply 修订 3，只授权集群 1、部署 1，最多 1 个副本，待确认写入日志为空。GPUStack 内旧调节插件保持 Observe 修订 1。**本轮 0→1 是 Observe 期间的受控手动恢复，不作为调节器自动扩容证据。**

切换前的 GPUStack PostgreSQL 归档已通过目录校验，当前同版原生 API 容器保留为停止状态的回退点。私有容器定义、数据库及验收原始响应已归档到 Mac 的 `.maas/direct004-maintenance-20261111/`，SHA-256 与控制端一致，文件权限 600。回退时先阻止新提交并排空，再切回容器并验证推理；不能只启动两个共享数据库的 GPUStack 容器。

本轮未重启 VM、worker 或 Docker 引擎，未开放公网入口。

## 尚未覆盖

- 新克隆获得了不同 GPU，因此本轮是新 GPU 的 worker 3 注册，不是原 worker 2 的同卡接续；原 worker 2 仍为 not_ready，未删除。旧会话撤销与同 GPU 接续仍待独立验收。
- 首次开机曾因供应商尚未更新 tower 主机名而被边界服务拦截；主机名更新、完成新 GPU 预登记和自动授权后，经 systemd 重试成功。不能据此宣称首次开机完全无人干预。按用户最新决定，自动修复此时序问题暂缓，现场确认克隆身份后由运维手动重试既有引导。[首启时序 #20](https://github.com/WTaoGitHub/cybercafe-maas/issues/20)
- 尚未验证 Higress 运行期间实例重建或地址变化后的路由刷新、多台真实 worker 分发与剔除，以及同条件性能对照。本轮证明功能可用，未证明性能优于原生 API。
- 单副本试点限制、当前 OCR 引擎及私有入口仍然适用，其他模型引擎的鉴权需分别核验。

脱敏机器证据见 [live-validation.json](live-validation.json)。原隔离试验与维护准备记录保留，不用本轮结果改写历史结论。
