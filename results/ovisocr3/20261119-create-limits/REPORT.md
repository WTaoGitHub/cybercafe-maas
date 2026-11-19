# OvisOCR3 容器创建限额

2026-11-19 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)

GPUStack v2.2.3 的 CustomServer 传入 GPU 设备资源。其 Docker 运行时把常规 CPU 资源数值转换成相对权重，OvisOCR3 的启动守卫需要 cgroup 内存和 CPU 硬限额。[worker 补丁](../../../maas_bootstrap/worker_limits.py) 在 Docker 创建请求中加入硬限额，匹配已验收的 OvisOCR3 镜像摘要及其主容器入口。

当前三台 Ready worker（6、7、8）已应用补丁，并把同一安装逻辑加入 worker bootstrap。对固定 worker 镜像，bootstrap 会核对运行时源码摘要，在 Docker 启动 worker 之前安装补丁。当前 worker 使用独立子进程创建新实例，现场通过新容器验证了代码生效。原有四个推理实例在验收期间持续报告 Running。

## 当前测试环境验收

临时模型复用现有的固定私仓镜像和只读 NFS 权重。未创建路由，worker 和 GPU 选择器为空。GPUStack 将首个实例调度到 worker 6 的 GPU 0，删除该临时实例后自动创建了第二个实例。

| 检查项 | 首个实例 201 | 替代实例 202 |
| --- | --- | --- |
| Docker 容器 ID 前缀 | `c04d1bd76afa` | `c565ddaaa1e1` |
| Docker `Memory` / `MemorySwap` | 均为 17179869184 B | 均为 17179869184 B |
| Docker `NanoCpus` / `ShmSize` | 4000000000 / 4294967296 B | 4000000000 / 4294967296 B |
| cgroup `memory.max` / `memory.swap.max` / `cpu.max` | 17179869184 / 0 / `400000 100000` | 17179869184 / 0 / `400000 100000` |
| Docker 事件及守卫 | `create`、`start`，无 `update`；守卫通过 | `create`、`start`，无 `update`；守卫通过 |
| 就绪及真实图片 | Running；健康检查、模型列表、OCR 请求均为 HTTP 200，匹配 4 个目标字段 | Running；健康检查、模型列表、OCR 请求均为 HTTP 200，匹配 4 个目标字段 |

另用缓存 worker 镜像创建一个未启动的 Docker 容器，验证 bootstrap 能写入并读回补丁，重复安装保持一致，随后删除该容器。Bootstrap 和 OvisOCR3 启动检查合计 291 项通过。

验收后删除临时模型，确认无临时实例、路由、容器和实例日志残留，两个引擎端口均已释放。三台 worker 的 VM 启动标识和 worker 容器 ID 与变更前一致。worker 6 保留已验收推理镜像的常规 Docker 缓存。

当前 `restart_on_error=false`。替代实例验收覆盖 GPUStack 收到实例删除事件后创建新容器的流程。上线范围为当前三台 Ready worker 和更新后的 bootstrap 源码，旧母版及离线 worker 未更新。更换 worker 或 OvisOCR3 镜像摘要时须复验创建限额。
