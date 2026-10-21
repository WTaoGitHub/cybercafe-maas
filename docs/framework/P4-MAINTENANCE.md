# 常驻环境维护：正式绑定与操作

2026-10-21。当前方案为**整个目标集群暂停推理、维护002**，不是E1逐节点持续服务。001控制端、数据库、Nginx与SSH保持在线。新功能代码在001宿主机运行，不把管理key或SSH私钥挂入应用。

## 正式绑定

已将 `/root/wtaobj/maas-live/maintenance/binding.json` 绑定到：

| 项目 | 值 |
|---|---|
| 任务CLI | 当前Compose `exec -T api python -m maas_platform.cli`，读取正式app.env，操作正式maas任务库 |
| 目标worker | 2 / XZ-31-002 |
| 模型范围 | 已批准的模型1、9，必须覆盖整个目标集群；集合变化时拒绝执行，需重新审查配置 |
| 恢复身份/图片 | pilot-ocr / /run/maas-probe/upright.png |
| 可信状态 | /root/wtaobj/maas-runtime/health.json，恢复前等待新鲜健康状态 |
| 节点控制 | 已固定主机公钥、来源受限和forced-command的002专用SSH通道 |

正式绑定文件root/600，仅保存命令参数和私有文件路径，不写入key值。首次安装命令为 `python3 scripts/maas/runtime/bind_maintenance.py bind`；已存在绑定时拒绝覆盖，日常不重复执行。变更模型、路径或节点必须先审查范围。

## 每次维护

在001以root操作，从 `/root/wtaobj/maas-framework` 执行。先确认试点key未过期、collector健康、无其他运维正在改模型；可信节点及SSH直连推理应停止。以下命令会暂停两个节点的模型推理，不关闭VM或切Windows。

```sh
# 为本次窗口分配新配置，保存此路径供所有后续命令使用。
MAAS_MAINT_CONFIG=$(python3 scripts/maas/runtime/bind_maintenance.py new-session)

python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" begin
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" stop
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" status

# 完成节点维护后，先恢复部署，再通过探针恢复接单。
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" recover
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" resume
```

new-session输出本窗口绝对配置路径。`active.json`记录当前路径；每窗口保存独立operator.json和journal.json。上一窗口未RESUMED（包括配置已创建但尚未begin）时，禁止另开窗口。不要覆盖旧journal或删除active记录来绕过检查。

- **begin**：记录原副本/节点分布和配置摘要；暂停受控准入、等待在途；UNKNOWN拒绝部署变更；设置worker maintenance。maintenance本身不代表实例或GPU已停。
- **stop**：集群模型副本归零，等待实例和真实工作负载清除，再停止002原worker/旧Agent并检查GPU。
- **recover**：恢复002同一个worker、原maintenance及副本数/节点分布；有界纠偏；始终保持接单暂停。
- **resume**：等待collector新鲜健康状态，执行真实OCR探针，通过generation复核后开放接单并撤销恢复定时器。

## 恢复保护与异常处理

begin自动在001设置480秒best-effort recover定时器；002在arm后设置600秒独立worker恢复定时器。定时器只尝试恢复部署/worker，不自动resume接单，不自动解除UNKNOWN，也不等于宿主机高可用或带外开机。

命令失败先查看同一配置的status和journal。保留正式库、artifact、配置和恢复材料；不要跳过阶段、盲目重试推理或直接删容器。若无UNKNOWN、无配置漂移且可验证恢复，使用同一窗口recover，再resume。真实探针失败继续保持暂停，先修复权限、版本、collector或服务可用性。若配置已生成但还未begin，可继续使用该窗口配置启动维护，不重新分配。

成功后journal为RESUMED，002恢复保护解除，001恢复timer停止。下一次维护重新new-session，保留历史审计。单窗口文件锁阻止并发阶段操作；allocation锁和active记录阻止重复创建窗口。具备root权限的人工绕过仍需运维纪律约束。

## 本轮验收

详见 [常驻维护演练报告](../../results/maas-p4-maintenance/REPORT.html)。该演练直接使用常驻API、执行器、正式任务库和原命名卷；没有临时API/身份/任务库，没有停止001控制端，没有重启Docker daemon。

E1局部维护/故障隔离、E2批处理、E3多执行器均不包含在本轮实现中；网络规则启动持久性、HTTPS、独立监控、备份恢复和8小时负载仍是P4后续工作。
