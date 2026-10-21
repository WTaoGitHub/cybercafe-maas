# P4：正式维护绑定及常驻环境演练

2026-10-21 · GPUStack v2.2.3 · 001控制端与既有两worker。

## 结论

正式维护配置已绑定，使用常驻Compose API、正式任务库、原artifact卷及pilot-ocr身份完成一次 begin → stop → recover → resume 演练。最终RESUMED、UNKNOWN=0、告警=[]，两个模型均恢复各两个副本、每节点一个实例。

本轮是全局推理暂停、维护002的既定方案；不是E1逐节点持续服务。没有关闭VM、重启Docker daemon或增加worker。P4仍待网络启动持久性、HTTPS、独立监控、备份恢复和8小时负载。

## 配置与保护

- 正式绑定保存在001 root私有目录，配置600；每窗口独立operator.json和journal.json，历史会话不覆盖。
- 活动窗口未RESUMED时拒绝新建窗口；阶段命令使用会话锁，维护generation阻止旧探针误恢复新窗口。
- 绑定现有模型1/9、worker2；模型集变化或配置漂移拒绝继续，不能直接用于其他节点或集群。
- 001自动设置480秒best-effort recover定时器，002保持600秒独立worker恢复定时器；都不自动开放接单或解除UNKNOWN。
- 恢复接单前等待collector新鲜健康状态，再执行真实OCR探针。管理配置和专用SSH私钥不挂入应用。

## 实测

| 检查 | 结果 |
|---|---|
| 正式服务基线 | 同步OCR200/46行，异步任务成功 |
| 设置GPUStack maintenance之后 | 四个实例仍运行，再次确认maintenance不等于排空/停止 |
| begin到STOPPED | 12.970秒，模型全局归零，002原worker及实际GPU工作负载停止 |
| 停止保持 | 10秒后仍确认停止；001 API健康，排队任务等待，已有结果可下载 |
| 停止期间同步请求 | 503；预检证明不可用时先返回PREFLIGHT_UNAVAILABLE，不伪报可执行 |
| recover | 恢复同一002 worker、原maintenance/副本/节点分布；接单仍暂停 |
| 副本恢复纠偏 | 通过GPUStack实例API有限纠偏2次，最终恢复原节点分布 |
| resume | collector恢复且真实探针46行后开放准入 |
| 总维护窗口 | 60.626秒（单次实测，包含10秒保持；不是SLA） |
| 排队任务恢复 | SUCCEEDED，attempts=1，结果46行 |
| 常驻服务 | API、DB、执行器、observer容器身份不变，持续运行，数据卷未替换 |
| 计量 | 异步成功从4到6、同步成功从2到3；本轮新增2任务/1同步，恢复探针不计 |
| 收尾 | 两端恢复timer停止，002原restart policy/旧Agent状态恢复；H3及隧道仍停用 |

回归129通过、1跳过、1个既有Starlette弃用警告，11.87秒。实际数据库中断专项本轮未重复。新增验证覆盖会话互斥、未完成窗口保护、私有配置权限及collector等待/超时。正式维护工具在宿主机执行，未改动应用镜像或服务并发配置。

## 最终状态及后续

原OCR与P2模型四个实例直接请求全部200/46行；两个worker ready，collector健康。正式任务与结果保留，测试PostgreSQL停止。历史与本轮journal、正式绑定留在001供下次维护使用；下次必须生成新会话，不复用本轮配置。

下一步继续P4：发布文档统一、网络规则启动持久性与HTTPS、独立监控、数据库及artifact备份恢复、至少8小时负载。E1/E2/E3另列后续增强。

## 证据

[正式维护手册](../../docs/framework/P4-MAINTENANCE.md) · [验收记录](acceptance.json) · [阶段日志](journal.json) · [回归日志](regression.log) · [JUnit](regression.xml) · [四实例核对](final-verification.json) · [002恢复证据](node002-final-proof.json) · [控制端收尾](controller-final-proof.json)
