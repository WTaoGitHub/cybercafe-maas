# Unlimited-OCR-2 从只读 NFS 启动的部署验证

2026-11-18 · [Unlimited-OCR-2 服务 Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [NFS Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6)

新 worker 上的 GPUStack 部署 `unlimited-ocr-2-nfs` 已达 **1/1 Ready**。Higress 用既有试点凭据访问同名路由，单页 OCR 返回 HTTP 200 和完整结果。

| 对象 | 核对结果 |
| --- | --- |
| GPUStack | 部署 13、实例 195、worker 8、路由 11。路由仅指向部署 13 |
| GPU | RTX 4080 SUPER，单卡，SGLang BF16，每实例并发 1 |
| 镜像 | 复用已验证的后端版本和固定 manifest `sha256:0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6` |
| 权重 | `/srv/maas/models/unlimited-ocr-2-0480966`，对应 [已校验的 NFS 发布版本](../20261117-nfs-weights/REPORT.md) |
| 容器资源 | 16 GiB RAM、4 CPU、swap 0、4 GiB 共享内存 |

宿主、GPUStack worker 容器和推理容器均确认使用同一只读 NFSv4 来源。推理容器内核对两片权重大小及配置文件 SHA-256，SGLang PID 1 使用上述路径启动。设置 Docker 限额前核对了容器标签、镜像、GPU、挂载及守卫等待状态。限额设置后从容器 cgroup 读回，`/health` 返回 200，`/v1/models` 返回 `unlimited-ocr-2-nfs`。

经 Higress 发起一次受信 JSON 请求，耗时 9.193 秒，`finish_reason=stop`，输出 1 个页标记、1809 个字符，预期标题、数值、耗时文本和编号均命中。该结果覆盖一页样本上的启动与可调用性。统计见 [VERIFICATION.json](VERIFICATION.json)。

本次使用独立模型和路由。原 `unlimited-ocr-2` 与 `maas-ocr` 的配置、运行实例和原有路由读回一致，MaaS `document.unlimited-ocr-2` 继续使用原入口。新路由沿用原试点的访问策略。

GPUStack 当前关闭此部署的出错自动重启。新容器仍需在守卫等待期间核验并应用限额。多页与并发容量、NFS 故障时的恢复以及换容器后的限额接续待另行验收。
