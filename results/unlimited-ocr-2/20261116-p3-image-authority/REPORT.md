# Unlimited-OCR-2 P3 镜像地址与运行限额

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)

E07 的镜像获取问题由主代理第1次处理解决。首次受控激活的实例185在拉取镜像阶段失败，没有模型容器或文档请求；worker既有 reader 的 HTTPS manifest 与拉取检查成功。差异来自后端镜像地址采用控制端本机监听地址，未使用 worker 的既有 CA/reader 对应地址；镜像摘要和内容本身正确。

主代理将独立 model11 先降至0副本并确认失败实例已删除，保存快照后仅更正 backend14 镜像的 registry authority。镜像 manifest 保持 `sha256:6e7145c45fe1673912451c3734799fca22d00965a71f43a9519c79b79fb52169`，未更换凭据、CA、registry 或 worker 配置；其他后端字段及正式 OCR 2/2 均读回不变。

随后仅进行一次受控0→1验证。GPUStack 实际拉取成功，生成新实例186和唯一目标运行容器，最终进入 Running；未依赖手工容器冒充 GPUStack 管理。配置镜像 ID 为 `sha256:0f1e8e1564f98a23a1c0f50f5530ff7b86caae9b48c69afcd2e9352f17a6aa10`，19层。

在守卫等待时，核对实际 instance/worker、三项 runtime 标签、镜像、命令行、只读模型卷及容器内单 GPU UUID 后，才对精确容器设置资源限额。实际读回：

- `memory.max=17179869184`，16 GiB RAM。
- `cpu.max=400000 100000`，4 CPU。
- `memory.swap.max=0`，Docker memory+swap上限同为16 GiB。
- Docker `/dev/shm` 为4 GiB，模型挂载 `RW=false`。

因此 E05 的守卫也在实际 GPUStack 管理容器中验证，而非仅无GPU测试。每个新容器仍需受控核对和设置限额，不能宣称无人值守恢复；CPU/RAM claim 也不能替代 cgroup。GPU采样在本次加载开始后才启动，不能据此给出完整初始化峰值。

P3 尚未完成。Running和限额不替代实际 Higress 请求、完整输出、无在途实例替换及旧OCR回归。后续检查已发现试点路由存在非幂等请求重试策略，已在提交网关样本前暂停并按故障协议处理；该项未解决前不进行网关文档请求。
