# P4：私有试点发布、恢复与耐久运行

> E6迁移说明（2026-10-22）：候选版本e6-r1已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


2026-10-21。用户选择暂不开放公网，仅验证私有TLS。短时发布/恢复检查已通过，8小时测试已改为今晚19:00定时启动（北京时间），阶段暂不整体验收。[报告](../../results/maas-p4-release/REPORT.html)。

## 入口与监控

- 原HTTP回环18831保持；新增HTTPS回环18832，独立 `maas-private-tls.service`，同样限制路径与方法。90天自签证书、TLS1.2/1.3，证书及私钥仅在001；本机`.maas/private-tls.crt`为公开证书副本，不是私钥。
- SSH转发18832后，可用 `curl --cacert .maas/private-tls.crt https://127.0.0.1:18832/` 验证TLS，根路径返回404是预期。不要使用跳过证书校验的参数。推理仍须受限key，不能使用管理key。
- `maas-live-monitor.timer`每30秒执行独立宿主机检查。可执行 `systemctl start maas-live-monitor.service`，查看001 `/root/wtaobj/maas-live/monitor/status.json`、`dashboard.html`和journal。
- 指标历史文件每份约5MiB，保留当前及上一份。看板含最近120个采样的成功任务速率和聚合指标，可用SCP导出到本地打开；没有开放客户管理HTTP端点。
- 已真实停止observer验证独立监控可发现并解除告警。该监控与001同机，不覆盖整机离线，不发送外部消息。证书与30天试点key须到期前人工轮换。

## 网络持久化

两节点安装 `maas-boundary-boot.service`，Docker通过drop-in依赖该服务；在Docker启动前写入自有raw/pre-DNAT链，直接保护本机模型/API端口及已批准Docker桥网段。只管理自身链，不全局flush，SSH不在拦截范围。

`maas-boundary-refresh.service/.timer`在Docker就绪及每10秒核对实际容器清单，刷新运行期P3规则和内容固定的server桥地址。源镜像、Docker网段、节点集合或可信网络变更需要审查；当前只批准001/002、单Docker引擎和默认桥网段，不支持任意新worker自动接入。

```sh
python3 scripts/maas/runtime/persist_boundary.py status
systemctl is-active maas-boundary-boot.service maas-boundary-refresh.timer
```

已在正式全局维护STOPPED阶段重启两个既有Docker引擎，IPv4/IPv6 raw拦截分别抽样218/224次，缺失样本0；之后实际监听模型端口、Docker直连及IPv6隔离复测通过，SSH可达。未实际重启VM或切换系统；不能将抽样结果等同于所有启动场景的绝对证明。

## 备份与恢复

备份范围是正式任务DB、artifact及应用/维护配置，不是GPUStack平台或整机灾备。001上执行：

```sh
python3 scripts/maas/runtime/backup_live.py create
```

它要求空闲且无UNKNOWN的基线；暂停准入，停止API/执行器/observer写入，保持数据库运行后做pg_dump和文件归档，再启动应用并通过真实探针恢复接单。恢复watchdog只启动应用，不自动开准入。失败时保留恢复材料和暂停状态；禁止删除正式卷。

输出给出 `snapshot-<ID>`。在本机仓库执行：

```sh
python3 scripts/maas/backup_transfer.py fetch snapshot-<ID>
python3 scripts/maas/backup_transfer.py restore-copy snapshot-<ID>
```

本机`.maas/backups`保存加密归档，`.maas/backup.key`为恢复所需随机密钥，目录/文件私有且Git忽略。OpenSSL AES256-CBC/PBKDF2加密，HMAC-SHA256先认证密文再解密回传；篡改反例已验证。密钥不传到001，不打印或提交Git，需另行安全保管；丢失密钥将无法恢复加密副本。

独立恢复演练需启动已批准测试PostgreSQL容器，然后在001执行：

```sh
python3 scripts/maas/runtime/restore_backup_test.py snapshot-<ID>
```

只创建独立`*_test`数据库及私有恢复目录，检查归档路径、文件哈希、任务与成功事件、UID10001读取凭据；完成后删除测试DB和恢复副本，不覆盖正式库。验证且加密下载后删除对应明文staging文件，保留manifest/status审计。首份备份实测恢复6个成功任务、6个成功事件、9个文件；这是该快照时点，不覆盖之后新产生的任务。当前为手动快照，没有生产备份周期/RPO承诺；报告恢复耗时仅数据阶段，不是整机RTO。

## 应用镜像回退

Compose通过 `MAAS_TASK_IMAGE`选择版本，默认仍为p4-r1。已验证兼容候选p4-r2升级并回退p4-r1，原卷与排队任务保留、真实探针后一次执行成功；不降级schema、不删除卷。此演练不证明任意未来迁移兼容。实际回退前暂停、核对在途/UNKNOWN及备份，再停止三个应用进程、用批准镜像启动，最后通过探针恢复；不要在8小时测试中重复演练。

## 8小时测试与完成门槛

001服务 `maas-endurance-night-20261021`，进度 `/root/wtaobj/maas-live/endurance/night-8h-20261021/status.json`，逐任务记录 `requests.jsonl`。40张固定中英文测试图片，每5秒一个任务，单执行器和当前配额不变，验证持续可靠性而非最大吞吐。60秒预检57/57成功；一次4客户端提交全部成功，P95约45.06ms，样本量4不作为SLA。

正常进度由当前线程每小时安静跟进，完成或新故障才通知。不要重启服务重置计时；失败时先核对原因，不自动解除UNKNOWN或重放推理。

完成后运行：

```sh
python3 scripts/maas/runtime/verify_endurance.py night-8h-20261021
```

验收要求：持续时间≥28800秒、所有接受任务成功、错误为空、唯一成功事件与任务一致、请求覆盖整个窗口且最大提交间隔≤60秒。把最终status和verification取回结果目录，执行 `python3 scripts/maas/render_p4_release.py`；门槛未满足时报告不会将8小时标为通过。

P4剩余当前仅长时结果及最终收尾核对。公网开放、实际整机/双系统切换、控制端HA和生产RPO另按条件验证；E1/E2/E3继续保留为后续增强。

## 夜间测试计划（2026-10-21更新）

白天测试已按用户要求停止：1276.26秒，256提交/256成功，无错误，计量一致，不计入夜间8小时。

- 服务器001定时器：`maas-endurance-night-20261021.timer`；2026-10-21 19:00北京时间启动，目标连续28800秒，预计2026-10-22 03:00结束，收尾核对可能稍晚。
- 40张样本，每5秒一个任务，单执行器；理论约5760次，实际以记录为准。
- 单元文件保存在 `deploy/maas/systemd/`，已安装并启用；不依赖本机在线。`Persistent=false`，若服务器错过启动窗口，不自动补跑，需重新安排。
- 查看：`systemctl list-timers maas-endurance-night-20261021.timer`；日志：`journalctl -u maas-endurance-night-20261021.service`。
- 取消尚未启动计划：`systemctl disable --now maas-endurance-night-20261021.timer`；停止已启动负载：`systemctl stop maas-endurance-night-20261021.service`。这些操作不停止正常OCR服务。
- 正常进展静默；完成后核对持续时间、连续性、任务和计量并更新报告，出现新问题才提示。单次运行不自动重启；最终验收仍待实测。

## 2026-10-21 22:16 夜间耐久失败更新

2026-10-21夜间耐久测试在22:16:38以FAILED结束：2360接纳/2359成功，1个PREFLIGHT_UNAVAILABLE；仅运行约3小时16分钟，P4未通过。collector与002 SSH限流窗口吻合，当前服务已恢复健康，原因仍需验证。保留失败记录，不自动重新计时；先修复并重新安排完整8小时，P4通过前不启动E5。见 [失败分析](../../results/maas-p4-release/ENDURANCE-FAILURE.md) 和 [更新报告](../../results/maas-p4-release/REPORT.html)。
