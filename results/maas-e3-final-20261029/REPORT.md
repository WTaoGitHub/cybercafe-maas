# E3复测中断：零副本时的可选指标缺失

2026-10-29。**本轮按异常中断要求停止，未完成任何新的容量矩阵点。服务已恢复，脚本已修正并发布，未自动重新开始压测。**

## 实际经过

1. 只读确认控制端mangle返回方向修复有效，双worker READY、4个模型副本running、4个候选executor正常；MaaS接单，generation46、UNKNOWN=0。
2. 为满足“失败后停止新请求”，补充有界并发派发及剩余矩阵参数，提交并push `7a6924e`。旧脚本及原始模型配置已优先备份Mac私有目录。
3. 启动独立run `native-final-20261029`，计划双卡4/8副本各3轮。原生预检成功，然后暂停、排空到generation47，按原生流程将模型副本降至0。
4. GPUStack在无实例占用时省略`worker_node_gram_allocated_bytes`指标。测试代码使用`next(...)`强制取值产生`StopIteration`，新增的严格停止逻辑将它当作采样故障，**在重新扩到4之前停止**。这是本轮测试工具对合法零副本状态处理不完整，不是新的网关503或worker网络故障。
5. 只读诊断确认两个指标接口HTTP200、worker READY，无活动/UNKNOWN。保留原run的INTERRUPTED_REQUIRES_INSPECTION状态及采样记录，不拼接或覆盖旧测试。
6. 按测试结束恢复原配置的约定，在维护锁和generation保护下恢复4副本、原选择器及配置哈希；双卡各2 running。真实OCR恢复探针46行通过，MaaS恢复接单，generation48、UNKNOWN=0、监控正常。
7. 修正采样解析并通过21项本地回归，提交/push `0159162`，仅更新服务器测试工具，未重启测试。VM、Docker、GPUStack worker及MaaS容器没有重启或重建。

## 修正内容

- 已分配显存、GPU温度等可选指标未导出时记录为JSON null，并附`missing_optional_metrics`，明确“未知”；不把缺失值写成0或宣称采到了峰值。
- CPU/GPU实际使用量等必需指标缺失、非法/非有限数字及真正的HTTP/网络异常仍按失败处理。
- 支持有标签/无标签及可选时间戳的Prometheus样本。该工具只用于当前已验证的单GPU worker拓扑，不声明多GPU指标聚合已支持。
- 保留有界派发：最多4个提交中的客户端请求，任一失败后不再补派新请求，已发出的请求收尾并保留记录，不盲目重放。

## 结果边界

| 项目 | 结果 |
|---|---|
| 本轮新增容量点 | **0**；dual-4-r1停在PROCESS_COLD_START，目标4尚未应用；dual-8未开始 |
| 原生预检 | 200，46行；不是正式容量测量 |
| 中断原因 | 测试采样工具误判可选指标缺失；非新503/网络断连 |
| 业务恢复 | 注册OCR探针46行；generation48、未暂停、UNKNOWN=0 |
| 模型布局 | 原4副本/配置哈希，worker7/8各2；2GiB调度声明未改 |
| 执行器 | 原4候选执行器及额度保持；本轮未执行新的执行器扩缩容 |
| 测试状态 | 中断原样保留；脚本已修正，负载未自动重新开始 |
| E3总状态 | 部分实现和历史实测有效，最终验收未完成；待新run补跑 |

双卡4/8的剩余矩阵、重复稳定性、同步/异步竞争及最终参数选择仍未完成。后续重新开始时使用新的run_id，不复用或改写本次目录；先短轮验证采样覆盖零副本转换，再做重复矩阵。依然遵守遇503/UNKNOWN/连接异常中断、不重启VM、不恢复collector、每feature验证后commit并push。

## 证据

- [开始前基线](baseline.json)
- [中断状态](interrupted-status.json)、[采样](interrupted-samples.jsonl)
- [只读诊断：HTTP200与指标缺失](diagnosis.txt)
- [受控恢复](recovery-after-abort.json)、[最终状态](final-after-abort.json)
- 修正测试：`tests/maas/test_benchmark_metrics.py`、`test_bounded_benchmark.py`、既有矩阵guard与TCP元数据测试，共21项通过。

原始模型完整配置只保存在Mac私有备份和控制端原run目录；不将环境变量、凭据、连接入口或原始私有配置提交到Git或Issue。
