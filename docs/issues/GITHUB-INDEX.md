# GitHub Issue 索引

2026-10-29已将现有增强计划与未关闭问题登记到私有仓库 [WTaoGitHub/cybercafe-maas](https://github.com/WTaoGitHub/cybercafe-maas/issues)。通过GitHub CLI创建并回读核对，共20项：19项OPEN、1项CLOSED。

Issue正文包含当前状态、既有优先级/排期、待办、验收条件及固定提交的源文档链接；不将历史待办误当最新状态。GitHub记录不改变现网、不授权复测，也不更改既定排期。

| 计划/问题编号 | GitHub Issue | 登记时状态 |
|---|---|---|
| GW-20261028-001 | [#1 [GW-20261028-001] 副本重建后GPUStack原生网关首次OCR请求503](https://github.com/WTaoGitHub/cybercafe-maas/issues/1) | OPEN |
| E1 | [#2 [E1] 逐 worker 排空与突发故障隔离](https://github.com/WTaoGitHub/cybercafe-maas/issues/2) | OPEN |
| E2 | [#3 [E2] 批次处理与结果打包服务](https://github.com/WTaoGitHub/cybercafe-maas/issues/3) | OPEN |
| E3 | [#4 [E3] 完成多执行器并发、显存预算与容量验收](https://github.com/WTaoGitHub/cybercafe-maas/issues/4) | OPEN |
| E4 | [#5 [E4] 同步持久幂等与原结果复用](https://github.com/WTaoGitHub/cybercafe-maas/issues/5) | OPEN |
| E5 | [#6 [E5] 克隆自动接入收尾与异常矩阵](https://github.com/WTaoGitHub/cybercafe-maas/issues/6) | OPEN |
| E6 | [#7 [E6] 退役自建 collector（已完成归档）](https://github.com/WTaoGitHub/cybercafe-maas/issues/7) | CLOSED |
| E7 | [#8 [E7] GPUStack 1000 worker 部署策略与规模验证](https://github.com/WTaoGitHub/cybercafe-maas/issues/8) | OPEN |
| E8 | [#9 [E8] GPUStack v2.3.0 适配和升级验证](https://github.com/WTaoGitHub/cybercafe-maas/issues/9) | OPEN |
| E9 | [#10 [E9] 生产组件编排优化和 MaaS 入口收敛](https://github.com/WTaoGitHub/cybercafe-maas/issues/10) | OPEN |
| E10 | [#11 [E10] OCR选型、PDF文档解析与大图片优化](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) | OPEN |
| E11 | [#12 [E11] CPU OCR 纳入 GPUStack 统一管理](https://github.com/WTaoGitHub/cybercafe-maas/issues/12) | OPEN |
| E12 | [#13 [E12] KubeEdge 多网吧边缘管理候选调研](https://github.com/WTaoGitHub/cybercafe-maas/issues/13) | OPEN |
| E5.F1 | [#14 [E5.F1] 配置服务稳定寻址及控制端迁移](https://github.com/WTaoGitHub/cybercafe-maas/issues/14) | OPEN |
| E5.F2 | [#15 [E5.F2] 凭证管理与自动分发增强](https://github.com/WTaoGitHub/cybercafe-maas/issues/15) | OPEN |
| E5.F3 | [#16 [E5.F3] GPU资源、VM和原生worker关联](https://github.com/WTaoGitHub/cybercafe-maas/issues/16) | OPEN |
| E5.F4 | [#17 [E5.F4] 清理旧业务凭据与残留](https://github.com/WTaoGitHub/cybercafe-maas/issues/17) | OPEN |
| E5.F5 | [#18 [E5.F5] 按节点角色实施worker启动保护](https://github.com/WTaoGitHub/cybercafe-maas/issues/18) | OPEN |
| AUTH | [#19 [AUTH] MaaS服务身份与GPUStack内部凭据解耦](https://github.com/WTaoGitHub/cybercafe-maas/issues/19) | OPEN |
| E5.NET | [#20 [E5.NET] 母版网络隔离补丁的新快照继承与首启验收](https://github.com/WTaoGitHub/cybercafe-maas/issues/20) | OPEN |

## 状态和职责

- GW-20261028-001仍未关闭：已修复控制端可复现的返回包误拦截，三轮回归通过；原始503缺源端口证据，保持观察，generation48已接单。详见[后续报告](../../results/maas-gateway-503-followup-20261029/REPORT.md)，不上传原始私有规则。
- E3部分实现及实测，剩余矩阵/最终选型未验收；与网关问题关联，不自动续跑负载。
- E5正常首启/冷拉取/双克隆与当前拓扑隔离已通过，异常矩阵及E5.F1–F5、新快照网络继承另列。E5.NET是跟踪编号，不新增或重排增强范围。
- E6已完成，创建后按completed关闭归档，不能当作待开发任务。
- AUTH为服务级认证解耦的跟踪编号，低优先级暂缓；实际下游联调不属于本系统执行队列。
- priority标签仅表达已存在的决策，未明确分级的任务不擅自指定等级。status:planned表示已登记，具体部分完成/待验收等状态以正文为准；暂缓/候选使用status:deferred。
- 本文件为登记快照，后续实施应同步更新Issue和项目计划；GitHub状态不会自动反写文档。

依据：[增强计划](../framework/ENHANCEMENTS.md)、[本地问题索引](README.md)、[当前执行顺序](../framework/NEXT-STEPS-20261027.md)。

2026-10-29 E3新复测因测试采样工具对零副本可选指标处理错误而中断，未出现新503、未完成新容量点。修正已发布，服务恢复且未自动重开，见[记录](../../results/maas-e3-final-20261029/REPORT.md)。

2026-10-29新增[PP-OCRv4部署参数优化专项](../framework/ENHANCEMENTS.md#pp-ocrv4-tuning)，在E10/#11跟踪参数配置化、线程、缩放、质量和能效验证，与E3/#4的副本/并发验收互相引用。当前仅登记，先取得E3固定参数基线，不改变主线顺序，不启动线上调参。
