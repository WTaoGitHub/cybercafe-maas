# Unlimited-OCR-2 P3 管理实例与重试边界

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [零副本准备](../20261116-p3-zero/REPORT.md) · [镜像地址修复](../20261116-p3-image-authority/REPORT.md)

GPUStack 管理的单副本实例已成功加载模型，并在 worker 本机直连下通过固定单页与双页解析。Higress 的实际路由已指向该实例，但发现 GPUStack 生成的路由配置显式启用了非幂等请求重试；在处理这一边界前未向 Higress 提交试点文档，P3 和 M1 尚未通过。

## 管理实例

正式 OCR 保持运行；确认 P2 试点无在途请求后仅停止该独立容器，worker 6 GPU 降至1 MiB、无 compute app。model 11 经一次受控缩容与镜像地址修正后，在新实例 ID 186、worker 6 Running；失败实例和修复过程另见上方报告。

该实例使用 guard 镜像 manifest `sha256:6e7145c45fe1673912451c3734799fca22d00965a71f43a9519c79b79fb52169`，image ID `sha256:0f1e8e1564f98a23a1c0f50f5530ff7b86caae9b48c69afcd2e9352f17a6aa10`。在资源守卫等待期间，按实际 instance name、GPUStack runtime 的 workload/component 标签、镜像、入口、命令、只读模型挂载及 GPU UUID 确定唯一容器，仅对其设置限制；Docker 配置和容器 cgroup 读回 memory 16 GiB、CPU 4核、swap 0，`/dev/shm` 4 GiB。守卫验证限额后才启动 SGLang；这不是 GPUStack 原生在容器创建时设置的限制。实例容器继承的模型卷实际为只读。采样器在加载已开始后启动，不用来估计完整初始化峰值。

固定受信客户端从 worker 本机直接访问该实例：单页 DONE/stop、1页标记、1809字符；双页 DONE/stop、2页标记、1614字符。两份输出均与 P2 已核对的对应原输出逐字节一致。没有精确记录这两次请求的起止时间，不将轮询时间当作延迟。此为直接实例证据，不代表网关数据面已通过。

## 路由与阻塞

现场 embedded Higress 前门为本机私有入口，返回 `Server: istio-envoy`。Envoy 配置中 route 8 的匹配指向 `model-11-186.static`，该 cluster 的唯一目标端口与实例 186 一致且 EDS HEALTHY；该结果证明配置关联，不单独证明请求透传。

实际 Envoy route 配置包含 `retry_on=5xx,non_idempotent,retriable-status-codes`、`num_retries=2` 及502/503，可能自动重放已执行但返回异常的文档。GPUStack 现场源码生成对应 ingress 注解，ModelRoute schema 未发现明确的逐路由重试字段。已暂停试点文档经 Higress 发送，待验证独立、持久且不影响正式 OCR 的禁重试方案；不能将客户端不重试误写成整个链路不重试。

归因更正：这来自现场 GPUStack v2.2.3 生成的路由策略。再次只读核对 `gpustack.gateway.utils` 第621、627–628行，可见它显式生成以下注解：

```yaml
higress.io/proxy-next-upstream-tries: "2"
higress.io/proxy-next-upstream: "error,timeout,http_503,http_502,non_idempotent"
```

Higress 官方默认不对非幂等 POST/PATCH 开启重试，需要显式加入 `non_idempotent`；不能将本部署生成的策略概括为 Higress 产品默认。[Higress 官方说明](https://higress.ai/en/docs/latest/user/annotation-use-case/#retry)

“两次”依据当时实际 Envoy 的 `num_retries=2`，表示符合重试条件时最多额外重试两次，即原始尝试加重试最多三次；它不证明某份 OCR 文档已实际执行三次。后续失败探针未完成完整验证，结论仍只到配置风险。[Envoy RetryPolicy 字段定义](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto#config-route-v3-retrypolicy)

本报告不声明 Higress 多图片/参数透传、长请求超时、断连、实例替换或旧 OCR 网关回归已通过。当前试点实例与资源采样进程仍运行，无已知在途文档；原始日志与输出留在受控私有目录。不得据此扩大并发或对普通下游开放原始 processor 接口。
