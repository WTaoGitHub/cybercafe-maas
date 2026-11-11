# 第二台 VPC 扩容验收准备

2026-11-11 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

策略与实际写入授权上限均已从 1 调整为 2，只授权集群 1、部署 1。单次增加与在途上限仍为 1，显存申报及压力门槛不变。

先保存策略、私有环境及容器定义至 Mac 并核对归档摘要；策略切 Observe 修订 6 后停止 E17 容器，修改持久环境中的授权上限，再以 Apply 修订 7、策略上限 2 重建同镜像 E17 容器。API、执行器、GPUStack 和 worker 均未重建，未重启 VM 或 Docker 引擎。第一次 Compose 检查未启用 e17-daily profile，未读到该服务；补齐 profile 后检查与重建通过。

新进程重新订阅并建立单卡基线，不沿用上一轮 capacity_restored 事件。实测日志依次为等待订阅、等待 GPU 到达，压力恢复至 open，扩容预览为 0、执行 idle；当前仍为 1/1，实例 3 在 worker 3 上 Running，待确认日志为空，压力样本新鲜并覆盖 worker 3。可由管理员启动第二台 VPC。

第二台实际 Ready 后核对容量增加，配置其固定 SSH 主机公钥和受限压力采样，确保新鲜样本覆盖全部参与规划的 worker。等待调节器自动 1→2、原生实例就绪、实例落点、Higress/OCR 及异步唯一成功计量。此处只完成准备，不代表双节点验收已通过。若新节点注册未完成或需要人工输入，暂停对应验收，不拓展 E5 专项。
