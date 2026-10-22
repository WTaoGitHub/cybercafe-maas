# P4 首批：001 常驻私有 OCR 服务

> E6迁移说明（2026-10-22）：候选版本e6-r1已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


2026-10-21。完成进入P4前建议的第1–3项：[验收报告](../../results/maas-p4-bootstrap/REPORT.html)。P4整体尚未验收。

## 当前部署

| 组件 | 位置/方式 | 状态 |
|---|---|---|
| Compose | 001 Ubuntu docker-compose-v2，2.40.3+ds1-0ubuntu1~24.04.1 | 已安装，仅新增插件，未升级或重启Docker引擎 |
| API | maas-tasks-api-1，UID10001，127.0.0.1:18830 | 常驻，健康检查通过 |
| 执行器 | maas-tasks-executor-1，UID10001 | 常驻，真实OCR任务通过 |
| observer | maas-tasks-observer-1，UID10001 | 常驻，私有指标/状态文件持续生成 |
| 独立PostgreSQL16 | maas-tasks-db-1，127.0.0.1:18835 | 常驻，独立maas数据库和命名卷 |
| 私有Nginx | 既有maas-edge，回环18831 | 同步/任务请求入口 |
| collector | 既有maas-collector.service | 每5秒发布精简证明 |

应用镜像 `cybercafe-maas-tasks:p4-r1`，实际image ID见deployment-status.json。依赖使用已测试lock约束，构建上下文白名单不包含本地secret/数据集。四个新服务均配置unless-stopped；本轮验证容器重启，未重启宿主机或Docker daemon。host网络避免新增网桥和修改推理防火墙；API/数据库只监听回环，不提供公网入口。

## 配置与信任边界

001私有根目录 `/root/wtaobj/maas-live/`：

- `app.env`：数据库密码和部署路径，root/600，不打印完整Compose展开配置。
- `secrets/`：UID10001/700；推理key、pepper、bindings文件600，只读挂载。
- `catalog.json`：仅ocr.default，绑定既有pin的revision/route，gpustack.ocr、25秒模型运行预算。
- `probes/upright.png`：无凭据探针，供真实恢复验证。
- `identity.json`：非管理员试点用户/key ID、创建时间和轮换提醒时间，root/600。key仅允许既有P2模型路由，有效期30天，第29天前轮换；没有自动轮换或外部提醒。

collector原始配置、SSH私钥、完整快照保持在原root私有目录。只导出 `/root/wtaobj/maas-runtime/consumer/` 下的pin、精简preflight、health；root:10001，目录750、文件640，应用只读挂载。精简证明只包含schema、观测时间、pin摘要和网关尝试上界，不含模型env/后台配置。collector完整验证后才发布，失败撤销证明；应用检查摘要与30秒新鲜度。

每个应用进程共用同一artifact命名卷。正式任务库独立于GPUStack库和可销毁测试库。不要将测试环境变量指向正式maas数据库，不要把管理key登记为客户key。

## 常用操作

在001 `/root/wtaobj/maas-framework` 执行，以下仅引用私有env文件路径：

```sh
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml ps
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli status
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli alerts
```

CLI pause用于受控入口暂停；resume仍必须携带 `--credential-id pilot-ocr --probe-image /run/maas-probe/upright.png` 并通过真实探针。完整GPUStack节点维护操作器已绑定常驻环境，使用每窗口新配置，不复用历史临时验收配置；命令与恢复步骤见 [正式维护手册](P4-MAINTENANCE.md)。

本地可使用SSH隧道转发001回环18831进行手动测试。未把新key复制到本地或聊天，需按既有安全文件方式取得受限推理key；不要用collector管理key做客户请求。

遇到发布故障先暂停接单/核对在途，再停止应用或回退镜像，保留数据库与artifact卷。禁止 `down -v`。备份恢复和正式版本回退演练仍待下一批完成。

## 结果与剩余工作

实际应用镜像、UID10001回归126通过、1跳过；最终collector权限发布修正专项19通过。Nginx真实同步OCR46行、异步46行；证明持续刷新且不可写；四个新增服务重启后排队任务/暂停状态/旧结果保留，探针后队列成功。观察状态正常、UNKNOWN=0；原有两模型四实例仍各46行。测试PG已停止，H3/隧道未启动。

首次验收脚本曾按Docker挂载列表原始顺序比较而误判，修正为按目标路径排序；探针恢复后保留首次两条任务，重新验收新增两条，正式库共4条成功记录，无重复成功事件的证据见usage.json。

正式维护配置已绑定且常驻演练通过，见 [维护手册](P4-MAINTENANCE.md)。接下来：发布文档统一、网络规则启动持久性/HTTPS、独立监控、数据库与artifact配套备份/恢复、至少8小时负载。实际VM断电/双系统切换和控制端高可用仍未验证。

## P4最新发布状态

2026-10-21已补私有TLS、独立监控、指标历史、备份恢复、网络持久化/Docker重启与镜像回退；8小时测试已改为今晚19:00定时启动（北京时间）。本文保留常驻部署基线，最新操作与未验证边界见 [P4发布手册](P4-RELEASE.md)。

## 2026-10-21 22:16 夜间耐久失败更新

2026-10-21夜间耐久测试在22:16:38以FAILED结束：2360接纳/2359成功，1个PREFLIGHT_UNAVAILABLE；仅运行约3小时16分钟，P4未通过。collector与002 SSH限流窗口吻合，当前服务已恢复健康，原因仍需验证。保留失败记录，不自动重新计时；先修复并重新安排完整8小时，P4通过前不启动E5。见 [失败分析](../../results/maas-p4-release/ENDURANCE-FAILURE.md) 和 [更新报告](../../results/maas-p4-release/REPORT.html)。
