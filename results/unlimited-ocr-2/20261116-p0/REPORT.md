# Unlimited-OCR-2 P0 现场只读预检

2026-11-16，关联 [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)。本次仅做现场只读检查，没有安装、修改配置、拉取镜像或启动推理。

## 容量和身份

GPUStack 管理接口显示 worker 6、7 Ready，均上报 RTX 4080 SUPER、驱动 580.95.05、CUDA 上报版本 13.0 和 compute capability 8.9；两者 name 和 external_id 均等于各自 GPU UUID。管理接口显示两者均无已分配显存。worker 4 为 not_ready，其缓存状态不作为可用容量。正式 `maas-ocr` 两实例均在 worker 5 Running，期望和就绪数均为 2。

选择 worker 6 作为独立试点候选。通过既有受信主机密钥的 SSH 只读核对，其 GPU UUID 与管理面一致：RTX 4080 SUPER、16376 MiB、驱动 580.95.05、compute capability 8.9；当时 `nvidia-smi` 显示使用 1 MiB 且无 compute app。Docker runtime 包含 nvidia，运行容器仅 GPUStack worker；主机内存 31 GiB、约 28 GiB 可用，根盘约 431 GiB 可用，宿主 `/dev/shm` 16 GiB。管理接口较早采样显示该卡约 433 MiB 使用，两次采样时点和口径不同。Docker 存在 registry CA 文件，worker 只读挂载 registry 认证目录；这不是本次拉取或 TLS 连通性实测。

新 PC 是控制和下游主机，并非 GPU worker。其 Docker 29.1.3，内存 15 GiB、当时约 9.7 GiB 可用、根盘约 395 GiB 可用；`kc-e2e-aliyun-slave3-server` 健康运行，MaaS API、执行器及 registry 容器运行。下游今天未提交新任务。

## 策略和限制

MaaS 副本调节策略 revision 5 为 Apply，仅授权 cluster 1 的正式 model 1，范围 1–2 副本；旧 GPUStack 插件 revision 1 为 Observe，仅包含 model 1。新模型不在授权范围。计划仍须冻结实际模型、镜像和显存 claim，并在任何共享配置写入前保存和校验对应配置及回退材料。上述显存数值是采样和调度信息，并非显存硬限制或引擎运行证明。

硬件候选、空闲容量和现有调节授权范围已查明；当时尚未核对目标 registry TLS 与拉取能力、控制端到目标模型端口访问边界及当前配置快照；后续只读补充见下。宿主共享内存不等于未来模型容器实际 `/dev/shm`。在相应准备和验证完成前不进入 P2 或修改共享生产配置。驱动及容器 GPU runtime 的存在也不代表 CUDA kernel 已实测兼容。

两个旧 SSH MCP 连接超时；使用既有受信连接及新 PC 上只读管理接口取得上述证据，没有反复尝试旧连接。没有创建试点进程、容器或任务；没有重启 VM、Docker 或 worker，也没有停止正式 OCR。

## 后续只读补充

worker 6 使用现有 CA 和只读认证访问私有 registry 的 `/v2/`：TLS 验证成功、HTTP 200；未拉取镜像，候选镜像 manifest 和 worker 实际拉取放在镜像发布后验证。worker 6 的 IPv4/IPv6 均存在 MAAS-CLONE 访问控制链；IPv4 规则允许受信来源，对受保护端口的其他来源拒绝。新试点实际端口的路由与拒绝行为仍须 P3 验证。

已在控制端受限私有目录保存当前 GPUStack 模型、实例、路由、后端、集群、worker 和 MaaS Apply 策略快照并计算 SHA-256。快照不是数据库备份或恢复演练；具体变更前需复核快照新鲜度、权限及恢复范围。硬件、资源和当前策略足以准备独立依赖镜像，真实 kernel、模型推理、候选镜像拉取及实际端口行为尚未验证。
