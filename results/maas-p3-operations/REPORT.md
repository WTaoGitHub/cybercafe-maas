# P3/W7 验收报告

2026-10-20 · GPUStack v2.2.3 · XZ-31-001 控制端 + 两个既有 worker。

## 结论

W7 在受控入口、共享 POSIX artifact 卷的范围通过。统一配额、TTL/孤儿回收、成功计量及私有本地告警已实现并分别提交。任务 API、执行器和 observer 本轮均未常驻发布。下一阶段为 P4 发布与耐久验收。

## 自动化回归

125 通过、1 跳过、1 个现有 Starlette 弃用警告，耗时 13.39 秒。跳过项为实际数据库中断专项，本轮没有重复该专项。测试在 001 的独立可销毁数据库运行，未操作 GPUStack 数据库。

新增覆盖：同步/异步全局和项目并发互斥、字节配额、到期文件释放、旧孤儿删除与符号链接保留、GC 等待上传提交、结果配额失败不记成功、同步事件唯一、探针/失败不计费、指标和告警触发、观察失败状态及日志脱敏。既有进程崩溃与提交确认丢失用例全部通过。

## 真实 OCR 验收

| 检查 | 实测 |
|---|---|
| 私有 Nginx → 同步 OCR | 200，46 行 |
| 持有 project 并发槽时发同步请求 | 429 / SYNC_CAPACITY |
| 同槽竞争的异步任务 | 保持等待；释放后执行 |
| 配额测试槽位 | 测试直接登记准入意图，未向后端发送推理；不是吞吐压测 |
| 异步 OCR | 两任务 SUCCEEDED，每任务一个成功事件 |
| 暂停期间同步 | 503 / MAINTENANCE_PAUSED |
| 恢复探针 | 46 行，不记成功请求用量 |
| 同步活动记录 | 3 个 COMPLETED：客户成功 1、测试槽位 1、恢复探针 1 |
| 同步成功用量 | 1 次，重复 finish 不重复记录 |
| TTL 清理 | 删除 1 个过期文件 |
| 孤儿清理 | 删除 1 个旧孤儿，保留新文件 |
| 验收结束指标 | UNKNOWN=0；队列=0；告警=[]；有效 artifact 540227 字节 |

## 运行结果与边界

两模型各两个副本，四个实例最终均直接调用返回 200、46 行；两个 worker ready，collector 健康。临时 API、测试身份、专用测试数据库和 artifact 已清理；测试 PostgreSQL 容器停止，保留其测试卷。两节点 H3 与 cloudflared 保持停止且 restart=no。

指标以私有 Prometheus 文本及 JSON 状态提供。观察进程每 60 秒更新、只在状态变化时写本地日志；未连接外部通知或独立监控。HTML 是验收快照，GPU/模型实例仍使用 GPUStack 看板。观察进程退出需由 P4 的独立存活监控识别。

Compose 模板已增加配额变量和 observer，共享卷的 YAML 结构解析通过；目标主机缺 Compose 插件，未执行 Compose 启动验证。本轮 API 测试使用既有 docker run 临时容器路径。

当前计量为成功任务/成功请求，不等于 GPU 秒、电费或客户端确认收货；同步重发无幂等保证。原生 GPUStack、Playground 和 worker 直连不属于该配额/计量入口，应继续遵守现有网络边界。未测试实际 VM 关机/双系统切换。

## 下一步

P4：先补 Compose 环境并常驻发布 API、执行器和 observer；随后补网络规则启动持久性、HTTPS、独立指标采集与告警存活检查、备份恢复和至少 8 小时负载。暂不扩大模型种类。

## 原始证据

[验收 JSON](acceptance.json) · [指标快照](metrics.prom) · [回归日志](regression.log) · [JUnit](regression.xml) · [最终 OCR/worker 核对](final-verification.json) · [001 清理核对](cleanup-001.json) · [操作手册](../../docs/framework/P3-OPERATIONS.md)
