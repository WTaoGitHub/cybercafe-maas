# MaaS 多模型真实共库联调

2026-11-18 · [共库 Issue #62](https://github.com/WTaoGitHub/cybercafe-maas/issues/62) · [容量 Issue #60](https://github.com/WTaoGitHub/cybercafe-maas/issues/60)

**临时 MaaS 的真实推理联调通过。** 本地运行一套 API、同一任务库和文件目录。单模型基线用一个执行器，混合测试用两个。GPUStack 和 Higress 实际服务请求，`ocr.default` 用既有 `maas-ocr` 路由，`document.unlimited-ocr-2` 用 `unlimited-ocr-2-nfs`。临时推理 Key 仅允许这两个路由。

| 阶段 | 验收 | 客户端完成时间 |
| --- | --- | ---: |
| 基线 | 图片同步、图片异步、三页 PDF 异步分别成功 | 0.89、1.30、23.62 秒 |
| 混合 | 文档执行期间图片同步成功，随后两张异步图片成功 | 4.07、1.51、1.58 秒 |
| 混合 | 同时排队两份三页 PDF，均完成并各有唯一页收据 | 26.40、48.81 秒 |

最终 6 个异步任务各一次尝试并成功，另有 2 次同步成功。账本有 8 份唯一收据，合计 5 张图片、9 页。异步结果下载和收据互相核对，原幂等键重发返回同一任务，数据库内无不确定活动。

混合阶段使用真实 GPUStack 拓扑计算临时库内的容量预算。观测确认图片和 NFS 文档各有一份符合候选条件的单 GPU 实例，两个池各一槽，全局两槽。实际派发账本显示图片同步和第一份 PDF 的派发时段重叠 0.732 秒。第二份 PDF 等待 26.184 秒才派发，两份文档上游时段无重叠。时间包含入口、等待和轮询开销，样本量不足以估算稳定吞吐或 P95。[结构化结果](VERIFICATION.json)

首轮三项基线成功后，容量筛选将 GPUStack 的 `distributed_servers` 元数据误判为分布式执行，两个池均为零，因此没有发起混合负载。实际返回为 delegated 且无 subordinate worker。修正后按真实从属实例判断，并回归实际存在 subordinate worker 时仍排除。随后从头通过上述混合阶段，实测代码为 `9c4a6e5`。

试验前后核对原 MaaS 目录、GPUStack 模型、路由、路由目标与实例身份一致。临时 Key 已撤销，API、执行器、SSH 通道及临时数据库已停止。

本次真实请求使用不同 GPU 上的图片和 NFS 文档实例。正式迁移、同卡混合容量和真实故障注入待独立验收。[局部隔离候选](../../deploy/maas/INFERENCE-QUARANTINE.md)使用独立 PostgreSQL 回归验证，未在生产服务中启用。
