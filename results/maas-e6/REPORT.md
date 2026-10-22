# E6 collector退役：发布与验证

2026-10-22。正式运行e6-r1，自建collector停止、禁用并永久mask，专用运行期配置/证明/SSH资产已清理；GPUStack原生采集保留。P4尚未通过，等待独立完整8小时复测。

| 项目 | 结果 |
|---|---|
| 候选镜像回归 | 独立测试库194通过、1跳过；另有P4门槛专项1通过 |
| 同步/异步OCR | 停用collector、无证明文件及挂载、无inventory私钥时均46行成功；异步幂等复查为原任务 |
| UNKNOWN与监控 | UNKNOWN=0，宿主监控无告警 |
| 正式维护 | 完整begin→stop→recover→resume，窗口60.512秒，停止保持10秒；旧结果可读，排队任务一次成功 |
| 计量 | 维护验收任务+2/同步+1，恢复探针不计费 |
| 备份与恢复 | 新基线一致性备份、加密异机与独立恢复，2686成功任务/计量和2404文件匹配，正式库未覆盖 |
| 回退 | 私有旧源码/配置/collector资产及002原授权备份已保留并核对；未做生产回退演练 |
| 模型与节点 | 两Worker ready，两个OCR模型各2副本，均跨两节点各1实例；H3和隧道停止 |
| 白天复测 | 用户主动停止，96.47秒、20/20成功、errors为空，INTERRUPTED；不计为完整8小时 |
| 夜间计划 | 新run e6-night-8h-20261022：2026-10-22 20:00至2026-10-23 04:00（北京时间），服务器timer已启用 |

保留鉴权、模型/revision目录、共享配额、任务/结果、维护generation、发送活动账本、UNKNOWN和唯一成功计量。不再逐请求独立核验实际容器镜像、固定worker分布或网关动态证明；按需发布审计不作为运行期门禁。旧catalog字段为fingerprint兼容暂留，运行时不读取。

原night-8h-20261021维持FAILED。新复测不拼接白天时长。私有TLS不代表公网开放；未做实际整机/双系统切换，无控制端HA及生产RPO承诺。

证据：[发布](deployment.json)、[OCR验收](live-acceptance.json)、[维护](maintenance.json)、[备份](backup-status.json)、[独立恢复](restore-result.json)、[模型状态](final-model-state.json)、[白天中断](daytime-interrupted.json)。操作和依赖见[执行记录](../../docs/framework/E6-RETIREMENT.md)。
