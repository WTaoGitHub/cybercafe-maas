# P3 节点维护、全局副本停止与恢复验收

2026-10-20 · GPUStack v2.2.3 · 001控制端，002维护目标。

## 结论

**受控进程级停止和恢复演练通过，113项回归通过、1项跳过。** 原worker容器、maintenance值、副本数、模型配置及两节点分布已恢复；真实OCR探针和排队任务均成功。H3及对应隧道保持停用。

本轮保持VM和SSH在线，未做实际关机、开机或双系统切换，不提供自动安全关机结论。W6按该受控范围验收；P3的W7运营限制仍待完善，P4发布/耐久尚未完成。

## 演练流程与证据

| 阶段 | 实测结果 |
|---|---|
| 暂停与核对 | 受控入口暂停，异步任务保持QUEUED；历史结果在暂停期间仍HTTP200、46行 |
| 启用maintenance | 002状态进入maintenance，但原4个模型实例仍running，验证maintenance不等于停止 |
| 副本停止 | rapidocr-gpu与maas-ocr-p2-r2均由2降为0；同时暂停001上的GPU推理，控制服务保持在线 |
| 002停止 | 原worker停止并临时关闭自动重启；旧Agent停止；GPU工作负载容器与NVIDIA计算进程均为空 |
| 保持停止10秒 | 复核仍为空，没有推理工作负载重新出现；VM、SSH保留 |
| 请求行为 | 新受控同步请求503；原生GPUStack入口的试点请求404，没有继续推理 |
| 恢复部署 | 原worker容器ID不变；maintenance恢复null，原副本数及配置指纹恢复 |
| 恢复分布 | 自动通过GPUStack API纠正2个过量节点实例，最终两模型均各节点一个副本 |
| 验证恢复 | collector正常，真实OCR恢复探针46行后resume；排队任务成功，attempts=1 |

成功演练的时间（单次观测，不是SLA）：暂停到停止证明15.45秒，停止到验证恢复51.50秒，整个维护窗口66.94秒，包含10秒停止保持和副本分布纠偏。

002停止证明包含：原worker ID `6d01ae1bce467ededb8881046d9016376148f69de9838ea31447cbfedefe0066`、空容器列表、空GPU计算进程列表、legacy_agent_active=false、vm_powered_off=false。`safe_to_poweroff`仍为false，不能将这一观察直接当作完整断电授权。

## 发现与修正

首轮中，GPUStack的spread策略将试点的两个恢复副本放在001，导致collector拒绝恢复。经一次人工重建纠正后探针成功；旧验收断言还未计入第二次pause而误判失败。首轮记录保留，未计为最终验收通过。

随后在控制器中补齐：记录原分布，有限预算内通过GPUStack实例API重建过量节点实例，等待分布恢复再允许resume；修正审计期望后，第二轮完整自动流程通过。没有修改selector、镜像或模型固定版本，也没有直接删除模型Docker容器。

## 已交付实现

- 私有维护协调器与持久journal：模型范围、版本/配置/副本漂移检查，维护generation与会话防覆盖。
- 002限定动作代理：只操作原worker，H3必须已停；停止Agent及worker后独立核对GPU进程，保留SSH。
- 001私有CLI与专用固定命令SSH key：不向客户接口或任务容器提供管理密钥；任意命令拒绝实测通过。
- 001/002两层best-effort恢复定时器；恢复失败保留诊断配置，恢复不自动解除UNKNOWN或恢复派发。
- 全流程验收脚本和单元/集成回归，覆盖UNKNOWN拒绝、配置/副本/maintenance所有权漂移、部分失败恢复、分布纠偏和探针失败保持暂停。

## 收尾

- 原模型及P2试点共4个实例逐一请求HTTP200、46行；两worker ready，collector和私有Nginx正常。
- maintenance、worker labels、模型配置SHA及副本数与维护前一致；原worker restart policy和旧Agent状态恢复。
- 两节点H3及cloudflared仍停止，Docker restart=no，模型资源未删除。
- 两个成功异步任务对应两条成功计量，异步/同步UNKNOWN均0；维护审计pause → pause → verified-resume。
- 临时API、用户/key、数据库、artifact及私密验收配置已清理；测试数据库容器停止；控制端与节点恢复定时器均0。

113 passed、1 skipped，9.39秒。跳过项为需单独宿主机编排的数据库停启专项，历史已有通过记录，本轮未重复；另有一条Starlette依赖弃用警告。

## 限定与后续

这是全局GPU推理暂停，不能不停001推理就精确排空002。可信运维直连不纳入任务台账，可能被停副本中断；没有承诺所有旁路请求无损结束。旧Agent在退出维护后恢复，仍须避免从旧管理端重新部署H3。

维护日志和超时恢复不等同于控制端高可用。网络策略持久化、启动窗口验证、常驻任务层、HTTPS、备份和8小时负载仍属P4；实际VM电源与双系统切换仍待验证。下一批P3/W7完善跨入口配额、TTL/孤儿清理、计量与告警。

## 材料

- [最终验收记录](acceptance.json)、[首轮观察记录](first-run-observation.json)
- [原服务复核](final-verification.json)、[配置复核](platform-status.json)、[控制端清理](controller-cleanup.json)、[节点恢复复核](node-cleanup.json)
- [回归日志](regression.log)、[JUnit记录](regression.xml)
- [维护操作手册](../../docs/framework/P3-NODE-MAINTENANCE.md)
