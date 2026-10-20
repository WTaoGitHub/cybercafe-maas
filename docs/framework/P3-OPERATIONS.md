# P3/W7：统一配额、文件清理、计量与告警

2026-10-20。GPUStack 固定 v2.2.3；[实测报告](../../results/maas-p3-operations/REPORT.html)。本工作包按可信两节点、统一受控入口、共享 POSIX artifact 卷范围通过。任务 API、执行器和观察进程尚未常驻发布。

## 统一准入与存储

同步 `/v1/chat/completions` 与异步领取共享全局和 project 并发数，在同一个 maintenance 行锁内原子判断。异步已领取的任务（含待确认取消）占槽；同步 DISPATCHING 占槽。UNKNOWN 不释放为可用资源，而是隔离后续推理。同步超额返回 429/SYNC_CAPACITY；异步保持排队；排队超额返回 429/QUEUE_FULL。同步恢复探针也受容量和 UNKNOWN 检查约束。

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| MAAS_MAX_RUNNING / MAAS_MAX_PROJECT_RUNNING | 2 / 1 | 两种入口合计并发 |
| MAAS_MAX_QUEUED / MAAS_MAX_PROJECT_QUEUED | 1000 / 100 | 异步等待队列 |
| MAAS_MAX_STORAGE_BYTES | 10737418240 | 全局未删除 artifact 元数据总字节，10 GiB |
| MAAS_MAX_PROJECT_STORAGE_BYTES | 2147483648 | 单 project 同口径，2 GiB |
| MAAS_INPUT_TTL_SECONDS / MAAS_RESULT_TTL_SECONDS | 86400 / 86400 | 输入/结果保留 24 小时 |
| MAAS_ORPHAN_GRACE_SECONDS | 3600 | 无引用文件最短保留 1 小时 |
| MAAS_CLEANUP_BATCH_SIZE | 200 | 每轮 TTL / 孤儿各最多删除 200 个 |
| MAAS_MIN_FREE_BYTES | 2147483648 | artifact 所在文件系统最低剩余空间 |

超存储配额返回 STORAGE_QUOTA（429，可重试）；磁盘不足返回 STORAGE_FULL（503）。TTL 到期但尚未清理的文件仍占配额，活动任务输入即使到期仍保留。结果写入同样检查配额，写失败不产生成功计量，按既有有限重试处理。

API、执行器和清理程序必须使用同一配置、数据库和同一个支持 `flock` 的 POSIX artifact 根卷。`.lifecycle.lock` 覆盖写文件至数据库提交确认整个窗口，清理与写入互斥；不要手动删除锁文件。当前方案不支持各 API 独立本地 artifact 目录、对象存储或未验证锁语义的网络文件系统。

孤儿回收只处理该根目录下符合 `art_<32位hex>` / `.art_<32位hex>` 的旧常规文件，不追随符号链接、不删除其他文件名；有有效元数据的文件跳过。提交确认丢失时先保留文件，后续按真实数据库引用回收。孤儿不计入逻辑配额，但仍受实际磁盘余量保护。每轮删除量有界，扫描时间随目录文件数增长；本轮不做数据库记录归档或商业账本保留策略。

执行器每 60 秒调用 TTL 与孤儿清理；也可使用私有 CLI。任务服务未运行时不会自动启动这项清理。

## 计量口径

- 异步：`task_usage_events` 每个成功任务一个事件，与成功状态/结果元数据同事务；重试、失败、旧 attempt 不产生额外成功事件。
- 同步：`sync_usage_events` 按持久 token 唯一，只有收到 200 且 JSON 合法的受控请求计 1 次；维护恢复探针不计。已确认结束但失败记 REJECTED，无法确认结束记 UNKNOWN。历史同步记录不会回填为成功用量。
- 同步客户端重发是新请求，可能产生新成功事件；尚无同步幂等协议。事件表示服务端成功提交/取得响应，不保证客户端已收到响应。
- 观察指标中的耗时为墙钟时间，包含探针和已确认失败请求，不是 GPU 执行秒数或电费。商业扣费、GPU 成本分摊和账单对账仍属后续阶段。

## 私有指标与告警

在配置了同一私有环境和 artifact 卷的运行环境执行：

```sh
python -m maas_platform.cli cleanup
python -m maas_platform.cli metrics
python -m maas_platform.cli alerts
python -m maas_platform.observe --loop --interval 60 --output /var/lib/maas/artifacts/.observability
```

Compose 已增加 observer 服务，与 API/执行器共用 artifact 卷。观察进程生成 mode 600 的 `metrics.prom` 和 `status.json`，原子替换；指标失败时移除旧 metrics 并写 critical 状态，日志只记录告警代码/级别和解除事件，避免重复刷屏。不发送外部消息，不开放客户 `/metrics` HTTP 路由。

| 告警 | 条件 | 处理 |
|---|---|---|
| BACKEND_QUARANTINED | 任一异步/同步 UNKNOWN（含同步过期） | 核实后台活动，取得停止证据后人工解除 |
| DISK_LOW | 剩余空间低于最低余量 + 最大输出大小 | 清理/扩容，先停止接纳新数据 |
| STORAGE_QUOTA_NEAR | 全局 artifact 达配额 80% | 清理到期文件或调整容量 |
| QUEUE_NEAR_FULL | 等待队列达到 80% | 检查执行器/容量与流量 |
| QUEUE_WAIT | 最老等待任务超过 300 秒 | 检查暂停、UNKNOWN 或执行器故障 |
| MAINTENANCE_PAUSED | 维护暂停 | 信息提示，按恢复探针流程解除 |
| OBSERVABILITY_UNAVAILABLE | 数据库/指标读取失败 | 检查私有基础服务 |

指标为无 project/key/task ID 的聚合 Prometheus 文本；私有 alerts CLI 同时提供按 project 的成功用量。消费者必须检查 `status.json.generated_at_unix` 新鲜度：观察进程本身退出后不能自行告警，接入独立存活监控、指标采集/持久看板与通知渠道列入 P4。GPUStack 自带看板继续承担 GPU/模型实例观察；本轮 HTML 是验收快照。

API 错误、执行器异常和观察失败日志均不输出请求正文、API key、数据库连接串或原始异常 traceback。日志仍保留 request/task/attempt 关联标识。

## 复测与下一阶段

已有一次性 001 验收入口：`python3 scripts/maas/runtime/test_maintenance.py --edge --operations`，需按既有手册启动专用测试数据库；会占用临时 18830 端口、创建独立测试身份/数据库并在成功后清理，不停止 worker。报告包含同步真实 OCR、跨入口占槽、异步完成、恢复探针计量排除及 TTL/孤儿验证。

回归 125 通过、1 跳过（实际数据库中断专项本轮未重复），一次真实验收通过。P4 下一步：常驻发布 API/执行器/observer、网络规则启动持久性与 HTTPS、独立监控、备份恢复及至少 8 小时负载。001 控制端高可用、实际关 VM/双系统切换仍未验证。
