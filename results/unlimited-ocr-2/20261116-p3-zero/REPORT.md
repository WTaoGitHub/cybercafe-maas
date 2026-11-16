# Unlimited-OCR-2 P3 零副本准备

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [P2](../20261116-p2/REPORT.md)

GPUStack v2.2.3 已创建独立试点后端和零副本模型；没有创建新路由或试点 Key，没有激活新副本。P3、M1 和普通下游访问尚未验收。

## 当前配置

| 对象 | 实际读回 |
| --- | --- |
| 自定义后端 | `Unlimited-OCR-2-SGLang-custom`，ID 14，版本 1.0；修复后镜像私仓 manifest `sha256:f86ed85cbf7676e6168339e7f617ebe96025f202ff7fc59d2c628d52e7760c70` |
| 模型 | `unlimited-ocr-2`，ID 11，0期望 / 0就绪，模型实例数0；`restart_on_error=false`，创建时关闭自动模型路由 |
| 调度 | 显式选择 worker 6 的单张 GPU，显存 claim `12884901888` bytes（12 GiB），模型容器共享内存配置4 GiB；claim 是调度预算，不是显存硬限制 |
| 正式 OCR | `maas-ocr` 仍为2/2；创建前后模型路由数均为2，未修改正式路由 |

候选后端与模型通过现场 Pydantic schema 校验；入口为 `python3 -m sglang.launch_server`，参数沿用 P2 的 BF16、单请求、3页窗口和禁用 CUDA graph 配置，模型路径、模型名及端口使用 GPUStack 占位符。以上为零副本配置，不是容器实际参数验收。

worker 容器内原 `/var/tmp` 模型目录不可见。已将19个固定模型文件复制到既有受控模型目录下的独立 revision 目录，在临时目录逐项验证大小和 SHA-256 后原子命名；worker 容器内同路径实际读取19文件再次逐项核对，文件无写权限位、worker 的该挂载为只读。新模型实例容器实际挂载仍须验证。创建前后快照、创建意图及返回记录保存在受控私有目录，未将连接信息或凭据提交仓库。

## 激活前阻塞与回退

1. 现场 GPUStack custom backend 资源构造路径仅配置 GPU 设备；模型 schema 未发现 CPU/RAM cgroup 硬限额字段。P2 的16 GiB/4 CPU限额不能由12 GiB显存 claim 替代；需先验证加载前资源守卫及其受控执行，再决定激活，当前尚未激活。
2. 现有一个 inference key 的允许模型列表为空；现场源码将空列表视作不按 key 列表缩限，再与用户可访问模型集合相交。现有下游用户实际角色/访问范围及新模型隔离仍待核对；在受信身份隔离和拒绝行为证明前不创建可供普通下游访问的原始路由。原始 processor 反序列化风险见 [Issue 记录](https://github.com/WTaoGitHub/cybercafe-maas/issues/11#issuecomment-6908238136)。
3. P2 独立模型容器仍运行并占用 GPU，而 GPUStack 调度账为0；激活前须确认无在途、停止该试点容器并实测 GPU 释放，不能仅看调度账。GPUStack host-network 端口的边界和实际模型容器挂载、资源限额、Higress 路由/鉴权及替换均未验证。

当前回退是保留或删除这两个零副本试点对象和新增 revision 目录，不触及正式模型、历史任务或共享缓存。具体删除前先核对对象 ID、引用及无实例，保存快照；没有执行回退演练。未重启任何 VM、Docker 引擎或 worker。
