# Unlimited-OCR-2 M1：受控 JSON 试点

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · **M1 受控 JSON 模型服务试点验收完成。** M2 未开始；本结论不包含 SSE、生产容量或下游 PDF 任务。

最终候选使用模型 revision `0480966938291445591f69f08a13b041c073a96a`（[19 文件大小与 SHA](../20261116-p1-build/model-manifest.json)）、定制 wheel SHA-256 `576c71dab3cacad843f6870739494e13b2e8169a69cc038f84395d6503e801ea`、服务镜像 manifest `sha256:0a4ee2f9351225fe2ff91fe1d7f638b86972b9b2f704ab848325f2d0bc0d2fa6` 和 config `sha256:69a1189e272f63a6218a8067ea8a51171eeba7ea42e8dae4127f14e99a2c2750`。私有 registry writer 发布后，worker 既有 reader 和受信 CA 实际拉取并核对；缓存层未做冷拉。对应[后端、模型和路由模板](../../../deploy/unlimited-ocr-2/gpustack-m1-template.json)中的占位符必须由现场核验填写。

GPUStack model11 当前1/1、instance190 Running。候选为 BF16、窗口3、每次单文档、并发1、CUDA graph关闭、prefill 2048；12 GiB VRAM claim 是调度预算，不是硬限额。资源守卫放行前对唯一实例容器核验镜像、GPU和只读模型挂载，实际设置并读回16 GiB内存、4 CPU、swap 0、4 GiB shm。调度账不能替代实际GPU进程检查。资源守卫不会自行设置限额，自动恢复尚未闭环。

[固定合成样本 SHA-256](synthetic-sample-sha256.txt) 经 Higress 和用户保留 All models 的原 Key，JSON 2/10/11 页均以 `finish_reason=stop` 返回，页数和每页参考号正确；最终镜像2页直连与网关文本 SHA-256 均为 `9d9e56484cb8a762409ac2d0cc941bdefd5fdce7781cc0b911689b707f2d957f`。单页 JSON 四项预期内容通过。P2 的更多合成样本和其局限见[P2报告](../20261116-p2/REPORT.md)；这些不构成业务质量或并发容量验收。错误 Key 和匿名 raw 请求为401、仅旧模型 scope 的临时 Key 为403；后者及私有文件已删除。原 All models 设置未更改。

无在途替换前，连续三次、间隔3秒读取引擎 running、waiting、tokens、pending 均为0；实例189缩至0后恢复为190，Higress 路由指向190的健康目标，替换后实际两页JSON再次通过。旧 `ocr.default` 从原实际下游容器回归，任务 `task_9eb2470fedd4490a84cef9622e84a5ec` 为 SUCCEEDED，`successful_images=1`。

实例189的有界采样在10页请求已开始后启动，约2秒间隔，173次记录跨越其退出，不能代表完整启动峰值；GPU观测最大10816 MiB，容器 memory.current 观测最大13543505920 bytes，cgroup memory.peak 最大13967716352 bytes（该cgroup自实例创建以来累计，不是单个请求峰值）。UOCR2-INC-009 的采样首次启动因shell后台化导致空脚本，子代理一次修复后采样正常；缺失的前段没有补写。

本版服务端拒绝 `stream=true`：Chat 返回400；native generate 返回HTTP200的明确策略错误而非成功生成，三类负向检查前后请求/KV均为0。处理器固定内置类型，非法值调度前拒绝，不反序列化客户端输入。UOCR2-INC-010 的原SSE断连跨窗口残留1280 tokens：主代理第1次 response清理补丁经真实运行验证仍残留；第2次采用 M1 范围内 JSON-only，未声称修好SSE。一次已派发11页JSON在客户端2秒期限退出后未重发，随后连续读取运行/等待/tokens/pending归零，后续不同 ALPHA 文档未出现先前页码；这不证明被中断文档成功或保证立即取消计算。

现有 GPUStack 生成的Higress策略仍允许符合条件的非幂等5xx重试，额外上限2次；应用不得叠加重放，也不承诺GPU恰好执行一次。UOCR2-INC-008 两次禁重试尝试未通过，不重置或改写。route/per-try timeout现场为0s，不能声称服务端提供有限总期限；SSE idle及任意大请求/响应的边界不在本版支持范围，当前证据仅覆盖固定合成样本和900秒客户端预算。

独立 CPU 计数后端复用同一私仓镜像、使用 Python 标准库，不加载模型或调用 CUDA；128 MiB VRAM 是调度声明，实际容器限额为256 MiB/0.5 CPU/禁swap，300秒退出。先以直连正常和503各一次验证探针，再经过真实Higress发送一次各类POST；不配置应用重试、fallback或请求重试控制头。probe的19条generic路由与试点17条API路由的实际 `retry_policy` 完全一致。

| CPU探针场景 | 客户端结果 | 实际后端调用 |
| --- | --- | ---: |
| 正常JSON | 200、完整JSON | 1 |
| 持续503 | 503 | 3 |
| 持续502 | 502 | 3 |
| 响应头前断连 | 503 | 3 |
| 部分JSON响应后断连 | RemoteProtocolError，不记成功 | 1 |
| 后端延迟2秒、客户端0.3秒超时 | ReadTimeout，不记成功 | 1 |

[聚合证据](gateway-probe-evidence.json)及[有界计数后端](probe_backend.py)保留。此为同重试策略的独立CPU探针，未对真实OCR注入5xx，也没有测得每份OCR的GPU执行次数；观察到的3次不能替代其他fallback/模型内部重试预算。正式调用的固定客户端只发送一次，未知完成须暂停派发并核实负载，不自动重放。

探针工具问题均与业务模型隔离：UOCR2-INC-011 为后端名缺少 `-custom`，路由层400且未创建对象，修正后创建成功；UOCR2-INC-012 为辅助脚本把17条API路径数量用于有19条路径的generic路由，修正为动态核对；UOCR2-INC-013 为探针仅读Content-Length，实际Higress使用chunked，初次请求被探针400拒绝。补上1 MiB/1024块/5秒socket限制的分块读取，经本机真实chunked自检及现场12次chunked请求计数验证后通过。每项各一次修正，不将初始拒绝当作业务OCR故障或重试证据。

探针route/model/backend及实例已删除，实际容器不存在、端口释放；试点model11保持1/1，正式OCR保持2/2，最终路由数3。探针运行前后其余受保护路由完整对象摘要相同，未改共享网关、未重启VM、Docker或worker。试点最终运行/等待/tokens/pending全0，限额再次读回通过。

[固定客户端与curl用法](../../../deploy/unlimited-ocr-2/README.md)不包含Key。实际curl已在受信控制机的本机回环推理入口执行，单页JSON `stop`、页标记及四项预期内容正确；私有env和请求/响应文件留在受控目录。新模型从原下游容器直接访问尚未验证，MaaS PDF/文档driver和计量属于后续M2。
