# 运维人员手册

新增模型或更新推理镜像时，从[第 13 节「新增模型上线与回退」](#13-新增模型上线与回退)开始。该节按测试环境的 GPUStack、多模型动态准入和 worker 本机回执流程编写。第 6、9 节记录了早期试点的全局隔离与发布流程。

> **2026-11-02当前已发布ocr-metering-r2**：规范字段image_base64/image_url、旧别名兼容、异步远程URL及E16两个计量查询均已完成真实验收。4异步+3同步成功，7条唯一收据；当前tokens实际null/unavailable，不能按tokens实际收费。generation488、监控健康、UNKNOWN=0，8副本profile保持，无VM/worker/Docker引擎重启。OpenAPI已内嵌真实脱敏响应；以下待部署状态为历史。[发布报告](../../results/maas-ocr-release-20261102/REPORT.md)。


> **2026-11-02内嵌图片异步提交已部署**：应用inline-task-r1上线，generation486，接单恢复。data_url四并发同键只1任务，旧artifact方式兼容，两任务唯一尝试/成功计量和46行下载通过；同步OCR、能力schema、监控及UNKNOWN=0正常。8副本/8执行器/并发8/8、Q25/25和3.5GiB保持，无VM/worker/Docker引擎重启。下方待部署描述为历史。[发布报告](../../results/maas-inline-deployment-20261102/REPORT.md)。


> **2026-11-02 8副本候选已受控启用并收尾**：常驻R8/E8/G8/P8、全局/项目等待队列25/25、每副本3.5GiB，两卡4+4。generation484、监控正常、UNKNOWN=0；真实同步1次、异步2次（唯一尝试/计量及46行结果）通过，原配置已备份Mac。沿用10月31日完整分段T6证据，本次未跑新8小时，不宣称SLA；5%余量仍待观察。下一步E8独立适配，不自动升级现网。[部署与回退记录](../../results/maas-e3-promotion-20261102/REPORT.md)。


> E6迁移说明（2026-10-22）：正式版本e6-r1已上线，已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](../framework/E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


基线：2026-10-21当前P0–P4私有试点，不含E1。文档命令供授权运维按窗口执行，本次编写未在服务器运行它们。默认不在耐久测试期间重启、发布、维护或更改配额；确需变更时先停止负载并重新安排完整验收。

## 0. 空环境安装与初始化

从空卷部署独立实体机时，使用[实体机全新部署与验收手册](../../deploy/maas/BARE-METAL.md)，按 GPUStack Server、Worker、OCR、MaaS、私有入口的顺序执行。下文涉及 001/002 的路径、拓扑和维护步骤属于现有试点环境。沿用现有环境时走发布或恢复流程。

新 PC 的方案 C 固定入口为 `http://maas-service:18831`。检查 `maas-consumer-private.service` 与 `maas-consumer-private-policy.timer` 是否处于 active 状态，再查看 `nft list table inet maas_consumer_private`。从实际下游容器解析名称并访问模型能力路径，无凭据预期 `401`，授权凭据预期 `200`。若 DNS 正常但连接超时，核对当前 Docker 网桥是否已被策略刷新纳入。宿主机上直查 API 健康仍使用 `http://127.0.0.1:18830/healthz`。入口范围和停用步骤见[新 PC 上线计划](../../deploy/maas/NEW-PC-ROLLOUT.md#方案-c-固定-http-消费者入口)。

## 1. 拓扑、路径与操作权限

| 项目 | 当前值 |
|---|---|
| 001 | XZ-31-001，10.11.31.1；控制端+worker+API/单执行器/observer/DB/artifact |
| 002 | XZ-31-002，10.11.31.2；worker |
| SSH入口 | 192.0.2.10，001端口SSH_PORT_REDACTED，002端口SSH_PORT_REDACTED；本地私钥认证 |
| GPUStack | 固定v2.2.3；001私有18080；已有server/worker容器，不新建daemon |
| 远程源码 | `/root/wtaobj/maas-framework` |
| 正式应用配置 | `/root/wtaobj/maas-live/app.env`、catalog.json、secrets、probes、maintenance |
| collector | `/root/wtaobj/maas-runtime/health.json`及consumer只读证明 |
| TLS | `/root/wtaobj/maas-private-tls/`；私钥不导出 |
| 正式任务库 | 独立PostgreSQL16的maas库；与GPUStack库/可销毁测试库分离 |
| 结果存储 | 001 Compose命名artifact卷，容器内 `/var/lib/maas/artifacts`；不放到002 |

下文宿主机命令在001以授权root执行，工作目录 `/root/wtaobj/maas-framework`；本机命令会另外标明。管理凭据和SSH私钥仅在受信任宿主机，应用容器没有Docker socket或管理key。

应用镜像基线为 `cybercafe-maas-tasks:p4-r1`，API/执行器/observer使用同一版本与配置，UID10001，restart=unless-stopped。不能以本地Git最新提交推断远端镜像已更新。

## 2. 私有访问与端口

| 端口（001） | 用途 | 客户访问 |
|---|---|---|
| 18832 / 18831 | 私有Nginx TLS / HTTP，回环 | 授权隧道后访问；推荐TLS |
| 18830 | API，回环 | 运维直查health/docs；不作为公网入口 |
| 18835 | 独立任务PostgreSQL，回环 | 不向客户转发 |
| 18080 | GPUStack管理/原生推理 | 仅管理/诊断，不作为MaaS客户入口 |

本机授权运维可建立隧道：

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18832:127.0.0.1:18832 -p "$MAAS_SSH_PORT" -i "$MAAS_SSH_KEY" "${MAAS_SSH_USER}@${MAAS_SSH_HOST}"
```

管理UI另外按需转发18080；不要给普通客户root SSH权限来使用API。管理员应提供受控转发方式或已批准的私有访问环境。普通用户拿到地址、公共证书和推理key即可调用，不应接触app.env。

当前TLS为90天自签证书，校验服务器证书后使用；本机公开证书副本为仓库 `.maas/private-tls.crt`（不入Git）。客户端使用 `--cacert`/SSLContext，不能使用 `-k`。证书SAN包含127.0.0.1/::1/maas.local；换域名需重新规划证书。

TLS/Nginx根路径返回404是预期，不代表不可用。`/healthz`需直查18830；Nginx只允许业务路径和GET/POST、拒绝query参数，不转发Swagger及管理接口。

## 3. 日常巡检（只读）

在001执行：

```sh
cd /root/wtaobj/maas-framework
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml ps
curl -fsS http://127.0.0.1:18830/healthz

docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli drain-status
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli alerts

systemctl is-active maas-collector.service maas-live-monitor.timer maas-private-tls.service
cat /root/wtaobj/maas-runtime/health.json
cat /root/wtaobj/maas-live/monitor/status.json
journalctl -u maas-collector.service -u maas-live-monitor.service -n 50 --no-pager
```

预期：API/DB健康、执行器与observer运行，collector/monitor状态新鲜，无UNKNOWN，无非预期暂停。`healthz.ok`只说明API可访问DB，不能证明推理可用。`cli status`输出聚合指标，维护准入详情应使用 `drain-status`。

需要时分别在001/002只读检查：

```sh
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/persist_boundary.py status
systemctl is-active maas-boundary-boot.service maas-boundary-refresh.timer
nvidia-smi
```

不要打印app.env、bindings、key、完整docker inspect环境或完整Compose展开结果。允许 `docker compose … config --quiet` 校验语法。日志、状态文件或截图转交前也须核对没有敏感配置。

## 4. 监控、告警与用量

observer每60秒读取任务DB/共享卷，私有 `metrics.prom/status.json` 位于artifact卷的 `.observability/`。宿主机 `maas-live-monitor.timer`每30秒独立检查容器、API、collector/observer新鲜度和TLS；指标历史有界轮换约10MiB，看板在 `/root/wtaobj/maas-live/monitor/dashboard.html`，需受控导出查看。

```sh
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli metrics
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli alerts
```

| 告警/现象 | 排查与处理 |
|---|---|
| BACKEND_QUARANTINED | 查异步/同步UNKNOWN；取得实际终止证据后人工解除，不能仅重启执行器 |
| MAINTENANCE_PAUSED | 核对是否预期维护；完成真实探针后resume |
| QUEUE_NEAR_FULL / QUEUE_WAIT | 查暂停、UNKNOWN、执行器和模型可用性；不要只放大队列 |
| STORAGE_QUOTA_NEAR / DISK_LOW | 核对TTL与活动引用、清理到期数据或扩容；不手工删活动文件 |
| OBSERVABILITY_UNAVAILABLE / 监控SERVICE_DOWN_* | 查DB、卷权限、容器或observer；恢复后核对状态新鲜度和解除事件 |
| collector不健康或陈旧 | 查两个既定worker、模型/镜像/pin、只读导出权限与SSH清单 |

不开放客户 `/metrics`，没有外部告警通知。宿主机监控与控制端同机，001整机失联时不能自行发送告警。

用量为每成功异步任务/成功同步请求一次事件，维护探针不计；不是GPU秒、token、电费或商业扣款。CLI alerts可提供按project成功用量，仅授权管理员读取。

## 5. 配额与文件生命周期

| 环境变量 | 默认值 |
|---|---:|
| MAAS_MAX_RUNNING / MAAS_MAX_PROJECT_RUNNING | 2 / 1（同步与异步合计） |
| MAAS_MAX_QUEUED / MAAS_MAX_PROJECT_QUEUED | 1000 / 100 |
| MAAS_MAX_INPUT_BYTES / MAAS_MAX_OUTPUT_BYTES | 10MiB / 20MiB |
| MAAS_MAX_STORAGE_BYTES / MAAS_MAX_PROJECT_STORAGE_BYTES | 10GiB / 2GiB |
| MAAS_MIN_FREE_BYTES | 2GiB |
| MAAS_INPUT_TTL_SECONDS / MAAS_RESULT_TTL_SECONDS | 86400 / 86400 |
| MAAS_ORPHAN_GRACE_SECONDS / MAAS_CLEANUP_BATCH_SIZE | 3600 / 200 |
| MAAS_LEASE_SECONDS / MAAS_HEARTBEAT_SECONDS | 30 / 5 |

表为代码默认，实际值以审核过的私有app.env为准。项目额度当前全局统一配置，不支持每个project单独设不同数字。修改需评估容量并让API/执行器/observer同时使用一致配置，在维护窗口重新创建相应应用容器；不是业务UI热调参。

输入从上传计时、结果从生成计时。活动任务引用期间保护输入；未引用的上传到期可回收。结果过期立即不能下载，不能依赖物理文件尚在。执行器约每60秒清理一次，执行器停机则该周期任务不运行；可人工执行：

```sh
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli cleanup
```

清理包括有界TTL及孤儿文件回收，不删除任务/计量历史。不要删除 `.lifecycle.lock`、改artifact命名或给各进程换成独立根目录。当前只批准同机POSIX共享卷，不支持任意NFS/对象存储替换。

## 6. 全局暂停与UNKNOWN处置

只需暂停准入时：

```sh
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli pause --reason 'planned maintenance'
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli drain-status
```

pause停止新的受控同步请求与异步派发；异步提交仍可能进入有界队列、等待并消耗总截止时间。已接受请求继续执行。查询与未过期结果下载仍可用。`managed_requests_drained=true`只说明受控活动排空，`safe_to_poweroff`始终false，不是GPU停止或系统切换证明。

UNKNOWN不在任务终态列表，而在backend_activity/sync_activity。当前可能由网关尝试到不同worker，故全局隔离后续推理。先保存request/task/attempt/token、发生时间、维护journal和实例/进程证据，核对所有潜在执行范围。仅HTTP连接关闭、超时或停止worker都不够。

取得真实结束/终止证据后，按活动类型人工解除（替换占位符，理由至少10字符且不含密钥）：

```sh
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli resolve-backend --token '<async-attempt-token>' --reason '<verified termination evidence>'
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli resolve-sync --token '<sync-token>' --reason '<verified termination evidence>'
```

解除只记录操作员证据，不会替你验证GPU已停止，也不会重放任务或自动resume。不要直接改数据库状态。确认已暂停、完全排空且collector健康后，普通准入恢复命令：

```sh
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml exec -T api python -m maas_platform.cli resume --credential-id pilot-ocr --probe-image /run/maas-probe/upright.png
```

如果存在完整节点维护窗口，必须按该窗口的recover/resume操作，不能用此命令绕过其journal和恢复定时器。

## 7. 002节点完整维护

当前操作会**暂停整个目标集群推理，并将模型1、9的集群副本归零**，影响001/002上的这些模型；001 API/DB/SSH及历史结果保留。不提供逐worker持续服务，不自动关VM或切Windows。

先检查没有耐久测试/其他运维窗口，collector与恢复key正常，模型范围仍是已批准集合，旁路处于受控边界。执行：

```sh
MAAS_MAINT_CONFIG=$(python3 scripts/maas/runtime/bind_maintenance.py new-session)
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" begin
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" stop
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" status

# 完成批准的节点操作后，使用同一个窗口配置：
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" recover
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_MAINT_CONFIG" resume
```

- new-session输出本窗口绝对路径，立即保留；已有未完成窗口时拒绝另建。正式绑定在 `/root/wtaobj/maas-live/maintenance/binding.json`，日常不要重复bind。
- begin记录原配置/分布、暂停/排空、设置maintenance；stop等待实例消失，再停002原worker/旧Agent并核对真实GPU。
- recover恢复原worker、maintenance、副本与分布，仍保持接单暂停；resume等待collector并做真实探针，generation核对后开放。
- 001有480秒best-effort恢复保护，002有600秒worker恢复保护；都不自动开准入、不清UNKNOWN、不保证整机故障恢复。

失败时保存同一窗口status/journal，查配置漂移与GPU工作负载；有证据后使用同一窗口recover/resume。不要删除active.json/journal强行另开窗口、手工全删容器或反复缩放模型。完整阶段说明见 [正式维护手册](../framework/P4-MAINTENANCE.md)。

## 8. 备份、异机保管与恢复

备份仅覆盖正式任务DB、artifact及应用/维护配置，**不覆盖整机、GPUStack平台和模型完整灾备**。选择空闲、无UNKNOWN、无其他发布/维护/耐久负载窗口。在001执行：

```sh
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/backup_live.py create
```

脚本暂停/排空，停止API/执行器/observer写入，保留DB，再做配套快照，启动应用并探针恢复。不是在线不停机备份。记录输出的snapshot-ID。失败先检查状态；保护只启动应用，不自动开放准入。

随后在本机仓库根目录执行：

```sh
.venv-maas/bin/python scripts/maas/backup_transfer.py fetch snapshot-<ID>
```

密文保存在本机 `.maas/backups/`，恢复密钥 `.maas/backup.key`；密钥不传001、不提交Git，应另行安全保管，丢失密钥无法恢复。归档含敏感配置。AES256-CBC/PBKDF2配合独立HMAC，先认证后恢复，不能自行改成未认证解密流程。

独立恢复演练：本机执行 `backup_transfer.py restore-copy snapshot-<ID>`，确认001已准备带批准标签的可销毁测试PostgreSQL，再在001执行：

```sh
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/restore_backup_test.py snapshot-<ID>
```

演练只恢复独立 `_test`库及私有目录，核对文件哈希、任务/成功事件、凭据读取权限，完成后删除临时副本。正式库不覆盖。明文staging在认证下载验证后清除、保留审计；按既有备份工具及 [P4发布手册](../framework/P4-RELEASE.md)核对，不手工批量删除备份根目录。

**生产原位恢复没有一键工具。** 数据丢失时先隔离写入、保留故障卷和journal、验证密文及独立恢复结果，再制定经审核的原位恢复窗口；同时恢复匹配DB与artifact，不只恢复一个。当前手动快照，无承诺生产备份周期/RPO/整机RTO。

## 9. 发布与回退

先核对基线镜像ID、已应用迁移、完整配置、可恢复备份、collector与空闲状态。构建候选显式标签，不能在运行中覆盖默认标签后无记录重建。Compose配置检查可用 `config --quiet`，不要输出含密码的完整配置。

批准窗口流程：pause → drain-status确认受控排空且无UNKNOWN → 停止三个应用写入进程 → 更新经过审核的app.env中的MAAS_TASK_IMAGE/配置 → 用同一Compose启动API/执行器/observer → 核对健康/版本/原卷 → 真实探针resume。DB和artifact卷保留。命令结构：

```sh
# 已完成暂停、排空、备份与镜像审核后：
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml stop api executor observer
# 使用受控编辑器切换app.env到已批准镜像；不要打印秘密。
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml config --quiet
docker compose --env-file /root/wtaobj/maas-live/app.env -f deploy/maas/compose.yaml up -d --no-build api executor observer
```

当前仅验证兼容候选p4-r2→p4-r1回退，未做schema降级。若候选已迁移出不兼容schema，不能只换旧镜像；需要专门恢复方案。禁止 `down -v`、删除正式卷、重新初始化正式库。正式Docker/VM重启是维护变更，不是日常“修复所有问题”按钮。

## 10. 网络持久性与环境变化

两节点raw/pre-DNAT自有链MAAS-P4-EARLY在Docker前安装，runtime每10秒核对容器/可信地址；仅批准001/002及既定单引擎/桥网段。两个Docker引擎重启已测试，真实VM重启/断电尚未测试。

新增节点、Docker桥网段、server镜像/地址或IPv6拓扑变更必须重审规则，不能关防火墙“临时解决”。不全局flush、不改SSH边界、不新增worker/daemon做故障替身。Nginx门禁不能替代原生GPUStack/Playground/worker旁路限制。H3容器与隧道保持停用，保留镜像/模型。

## 11. 耐久测试与验收

已保存计划：北京时间2026-10-21 19:00—22日约03:00，001 `maas-endurance-night-20261021.timer/.service`，run ID `night-8h-20261021`。40张样本、每5秒、单客户端/单执行器，不是最大吞吐测试。

```sh
systemctl list-timers maas-endurance-night-20261021.timer --no-pager
systemctl status maas-endurance-night-20261021.service --no-pager
cat /root/wtaobj/maas-live/endurance/night-8h-20261021/status.json
journalctl -u maas-endurance-night-20261021.service -n 50 --no-pager
```

未到启动时间/未创建status属于预期。timer不补跑错过的窗口，服务不自动重启。取消未开始计划用 `systemctl disable --now maas-endurance-night-20261021.timer`；停止已运行负载用 `systemctl stop maas-endurance-night-20261021.service`，两者不停止正常OCR。

结束后在001执行 `python3 scripts/maas/runtime/verify_endurance.py night-8h-20261021`，核对≥28800秒、任务全成功、无错误、唯一计量对账与请求时间连续性（最大间隔≤60秒）。取回status/verification/requests，更新本机报告并核对原OCR实例、collector、监控、边界和H3停用。失败/中断不重启同run ID凑时长，保留证据后重新安排新一轮。

8小时等待不等于持续负载通过；P4仍保留私有TLS、单控制端、未整机/双系统切换、手动备份无生产RPO这些边界。

## 12. 常见故障速查

| 现象 | 优先检查 |
|---|---|
| 证书校验失败 | SSH转发端口、可信公共证书、SAN/到期时间；不要跳过校验 |
| 401/403 | 本地登记/禁用、secret权限/归属、GPUStack key到期/撤销/模型ACL；管理员处理 |
| 404 | 是否使用MaaS逻辑名/正确路径；根路径和未开放docs返回404属预期 |
| 429 | 并发/队列/存储分别排查；同步拒绝与异步排队行为不同 |
| 排队不动 | pause、UNKNOWN、执行器退出、project占槽、总deadline、collector |
| PREFLIGHT_* / MODEL_* | 新鲜证明、pin/目录/镜像是否漂移、两worker是否都健康 |
| UNKNOWN_BACKEND_ACTIVITY | 保存证据、全局保守隔离；不能让用户盲目重提 |
| 410 | artifact保留期到期；没有承诺在线找回，按备份策略评估 |
| DB不可用 | 保留文件与attempt；应用恢复扫描并不等于所有上游执行已终止 |

证书/key到期轮换仍需人工安排；具体业务授权见 [管理员手册](ADMIN.md)，接口行为见 [普通用户手册](USER.md)。

## 2026-10-23 私有试点验收状态

E6退役collector后的完整8小时复测通过：5760/5760成功，零错误，独立SQL任务/终态/计量一致；P4限定两节点私有环境通过。参见 [最终报告](../../results/maas-p4-release/REPORT.html)。无需重新启动负载；不恢复自建collector，不自动解除UNKNOWN。两台VM永久禁止重启；本轮没有重启VM或Docker/worker/容器。私有证书、同机监控、手动备份及未做E6生产回退演练的边界仍有效。下一项为E5设计调研，未部署动态身份方案。

## 13. 新增模型上线与回退

**现有 worker 已具备执行守卫所需的启动、挂载和补报能力时，新增模型无需重做 VPC 母版。** 母版决定新克隆的 worker 自带哪些程序。GPUStack 后端版本决定副本运行哪个推理镜像，按 `image_id` 发布的签名配置决定 worker 创建该副本时给它哪些资源、凭据和回执目录。

| 本次变更 | 运维动作 |
|---|---|
| 给同一部署增加 MaaS 逻辑名 | 复用已验收的推理服务、守卫和镜像规则。登记目录与授权，把别名加入原容量池，并验证结果及计量。 |
| 新增 GPUStack 部署，或更新权重、推理镜像、启动入口 | 固定权重和后端版本，核对推理协议与引擎终态。已有签名规则仍精确匹配时可复用。摘要或入口变化时发布新规则。逐个验收新副本。 |
| 新引擎、新协议或多卡推理 | 交由模型服务开发者验证适配器、执行获权和引擎终态。当前受保护接入支持下列三类 OCR driver，守卫要求一个推理实例只看见一个 GPU UUID。其他协议或多卡身份需先实现相应支持，再进入下述上线流程。 |
| 新增 worker VPC | 核对克隆来源的 `image_id`、bootstrap 客户端和已应用的签名 revision。旧快照克隆需要补装新版客户端并切到兼容配置。希望今后克隆开箱带有本机补报时，统一更新一次共享母版并验收新克隆。 |

2026-11-25，测试 worker 31、32、34 和共享 Tower 母版均已更新到 `tower-e5-receipts-v4`。母版已验签 `e5-local-receipts-cfde6d4`，补报 timer 已设为开机启用。扩容时记录平台保存的快照 ID，并在首台独立克隆上核对实际 revision、timer 为 active 及模型副本的回执挂载。既有 v3 快照创建的 worker 仍需升级客户端和配置。此次母版安装尚待新快照的克隆首启验收。[母版安装与快照交接](../../results/maas-pg-finish-delivery-20261125/REPORT.md#tower-母版更新与快照交接)

### 13.1 确定模型契约并保存基线

1. 在变更单记下客户端使用的**逻辑模型名**、MaaS `driver`、输入样本、预期结果、同步或异步入口、计量单位和最大运行时间。图片结构化 OCR 可参考 `gpustack.ocr`，OvisOCR3 单图文本使用 `gpustack.ovisocr3`，Unlimited 文档使用 `gpustack.document_ocr`。后两者通过异步任务接入。新协议还要提供维护恢复用的真实推理探针。[适配器及样本](../../deploy/maas/MODEL-ONBOARDING.md)
2. 记录权重来源和不可变 revision、推理镜像完整 `registry/repository@sha256:…`、Docker 主容器实际 `Entrypoint` 数组、后端版本、回执写入进程映射到宿主机的 UID，以及候选 GPU 的型号和显存。外置权重按模型发布到已有只读模型目录，在每类目标 worker 的容器内核对可读性和版本。模型权重、回执和客户结果各自使用对应的存储。[模型存储约定](../../deploy/bootstrap/clone/NFS.md)
3. 保存当前 GPUStack 部署、后端版本、路由及目标副本数，记录各类 worker 的签名 revision、MaaS 目录、绑定、容量策略和已生效的预算。通过私有 CLI 的 `status` 核对 `QUEUED`、`RETRY_WAIT` 数量，用 `drain-status` 留下在途及既有 `UNKNOWN` 的基线。为验证准备独立项目或带 `test_agent_` 标记的数据，预先登记任务、文件及临时资源的清理范围。

### 13.2 准备推理服务并验证执行守卫

同一受保护镜像用于新权重时，先验证新模型仍走原来的引擎调用、完成信号和结果解析。需要接入新推理代码时，由模型服务开发者在实际执行路径接入守卫，由运维验收以下行为。

| 检查点 | 可接受的行为 |
|---|---|
| 进程身份与启动 | 每个新进程使用自己的实例身份、可见 GPU UUID 和实际派发路由，响应 `GET /maas/guard-proof` 的签名挑战。缺少必要凭据时拒绝受保护推理。 |
| GPU 获权 | 接收 MaaS 的 `X-MaaS-Execution-Attempt`，在向引擎提交工作前取得该尝试的启动授权。缺失、无效或被拒的授权不能进入 GPU。同一路由的首次请求、网关重试和兜底目标都须经过守卫。 |
| GPU 终态 | 只有引擎确认该请求已结束才报终态。例如 PP 等待 ONNX 返回主机结果，Ovis 观察引擎调度器的完成事件。HTTP 超时、客户端断连、worker 重新 Ready 都不足以单独结清该次执行。 |
| TLS 断连与补报 | 守卫写入原尝试、原进程的签名结束回执，回调确认丢失时重送同一事件。回调持续不可达时由宿主机定时器补报，收到 HTTP 204 后删除回执。重送只结清后端活动，已失败的客户任务保留失败和原计量。 |

可对照已接入的三种执行路径定位回执应放在哪一层。[PP 同步引擎](../../gpu-ocr/execution_guard.py) · [Ovis 调度器](../../deploy/ovisocr3/vllm_guard.py) · [Unlimited 执行路径](../../deploy/unlimited-ocr-2/uocr_execution.py)

### 13.3 发布 worker 镜像匹配规则

1. 在拟接单的各类 worker 上核对 bootstrap 是否支持 `receipt_uid` 和 `maas-worker-receipts.timer`。在各自当前签名 manifest 上增补 `worker_runtime.images[]`，保留其他模型条目。匹配键为**完整镜像摘要和实际 Entrypoint 数组**。已存在同一组合时复用该条规则。
2. 每条受保护规则填写镜像实际读取的 `execution_env_prefix`，例如 `MAAS_OVIS_EXECUTION_`。启用本机回执时填写整数 `receipt_uid`，以映射后的实际 UID 为准。有 CPU、内存限额时引用 `profiles` 中已定义的 `profile`。保留 `worker_runtime.execution.origin` 的私有 HTTPS 地址和可信 `ca_pem`。在 GPUStack 模型环境中将 `<prefix>ROUTE` 设为**实际派发的上游路由名**。worker 在创建容器时注入 `ORIGIN`、`KEY_FILE`、`CA_FILE` 和 `RECEIPT_DIR`（均带该前缀），密钥和 CA 只读，回执目录单独可写。[字段与配置示例](../../deploy/bootstrap/clone/README.md#推理资源档位与执行回执)
3. 在配置服务器的受控运维环境中为本次改动分配新 `config_revision`。下列变量分别指向已核实的发布根目录、目标客户端设置、签名密钥文件、目标 `image_id`、候选公开 manifest、私有 registration 文件。目标 manifest 的 `compatible_image_ids` 只列该 `IMAGE_ID`。按实际使用的客户端范围分别发布兼容版本，再从配置服务按同一 `image_id` 回读验签并核对 revision。[按镜像发布与回退](../../deploy/bootstrap/clone/NFS.md)

```sh
python3 -m maas_bootstrap.publisher \
  --root "$RELEASE_ROOT" --settings "$IMAGE_SETTINGS" --signing-key "$SIGNING_KEY_FILE" \
  publish --image-id "$IMAGE_ID" --manifest "$CANDIDATE_MANIFEST" \
  --registration-file "$REGISTRATION_FILE"
```

4. 新克隆在首次启动时获取兼容 revision。存量 worker 需要切换规则时，先暂停并排空受影响的工作，在维护窗口按 GPU UUID 完成 worker 接续，使用新会话的启动许可。检查实际应用的 revision 及 Ready 状态，再创建或替换推理副本。已消费的许可不可复用。XZ-31-001、XZ-31-002 全程禁止整机重启。[存量 worker 接续步骤](../../deploy/bootstrap/clone/README.md#无盘-gpu-接续试点)

无盘 worker 的待报回执在宿主机 `/run/maas-bootstrap/execution/receipts/` 下，绑定到推理容器的 `/run/maas-execution-receipts`。同一 VPC 内替换推理或 worker 容器后仍可补报。定时器经 MaaS 私有 HTTPS 提交，由 MaaS 写 PostgreSQL，模型无需数据库账号或额外的共享 NFS 回执卷。整台无盘 VPC 回收会丢失本机未送达的文件。控制端缺乏原终态证据时维持隔离。[回执存储与恢复边界](../../deploy/maas/INFERENCE-QUARANTINE.md#worker-本机补报与-postgresql-结清)

### 13.4 创建 GPUStack 部署并核对真实副本

1. 在目标集群的自定义后端中新增不可变版本，把镜像引用固定到完整摘要，保留旧版本。创建模型时指定该后端版本、固定权重来源、启动参数、资源声明和对应路由，先保持客户流量关闭。从单个候选副本开始。记录 GPUStack 返回的 cluster ID、model/deployment ID、owner principal ID、route ID 和路由名。使用目录内已有部署增加别名时，直接核对这些值。[GPUStack 常态发布](../../deploy/maas/README.md#现有测试控制端的常态发布) · [部署模板示例](../../deploy/ovisocr3/gpustack-template.json)
2. 对每个可达副本核对实际 worker、GPU UUID、后端版本、容器镜像摘要、入口、守卫代码版本及三处挂载的读写属性。GPUStack 更新期望版本后，旧实例可能继续运行。按维护窗口替换旧实例，再将每台宿主机的核验计数与 GPUStack 的 Ready 清单逐个对照。若新引擎采用不同的入口布局，应提供能检查其实际加载代码的等价核验。
3. 现有 PP、Ovis、Unlimited 的 Python 入口可直接使用以下只读检查。在目标 worker 运行，`VERIFY_SCRIPT` 为[核验脚本](../../scripts/maas/runtime/verify_worker_receipts.py)在该机的路径，`GUARD_SHA256` 为从**批准发布版本**的 `gpu-ocr/execution_guard.py` 算出的 SHA-256。输出的 `revision` 和按模型前缀统计的 `serving` 应与预期一致，timer 应为 enabled、active。持续出现 `rejected` 时检查凭据、路由和进程身份，`unavailable` 时检查回调连通性，保留尚未确认的文件。

```sh
PYTHONPATH=/opt/maas/lib python3 -B "$VERIFY_SCRIPT" --guard-sha256 "$GUARD_SHA256"
systemctl list-timers maas-worker-receipts.timer
journalctl -u maas-worker-receipts.service -n 20 --no-pager
```

### 13.5 登记 MaaS 路由、授权和容量

以下用 `ocr.invoice` 举例，假设新模型已验证兼容 PP 图片协议，GPUStack 路由名为 `invoice-ocr`。这两个名字分别提供给客户端和推理网关。revision、超时和容量均须由本次发布确定。

| 配置位置 | 应填写或核对的内容 |
|---|---|
| `MAAS_CATALOG_FILE` | 在 JSON 对象新增键 `ocr.invoice`，填写 `driver=gpustack.ocr`、`upstream_model=invoice-ocr`、`revision`、`max_runtime_s`。在确认全部可达推理目标都强制守卫后，对同一路由的所有 MaaS 别名设置 `execution_guard: true`。Unlimited 还需固定模型 revision、运行镜像摘要、后处理器、渲染 profile，并在 API 和执行器安装相应 PDFium 依赖。[图片目录](../../deploy/maas/catalog.ocr.example.json) · [文档目录](../../deploy/maas/catalog.document.example.json) · [Ovis 目录](../../deploy/maas/catalog.ovisocr3.example.json) |
| `MAAS_BINDINGS_FILE` 与 GPUStack 路由 ACL | 在获准项目的 credential 条目中将逻辑名 `ocr.invoice` 加入 `models`，保留原 project 和 secret 引用。在 GPUStack 授予该凭据调用 `invoice-ocr` 的权限。先授权验收身份。维护探针凭据还须具备目录内全部网络模型的本地和上游权限。[绑定示例](../../deploy/maas/bindings.example.json) |
| `MAAS_CAPACITY_POLICY_FILE` | 新部署登记池，已有部署的别名放入同一池。`routes` 写逻辑名到 GPUStack **数字 route ID** 的映射。核对 `cluster_id`、`model_id`、`owner_principal_id`、`gpu_names`、实测 `slots_per_replica`、`queue_limit` 及适用的 `hard_limit`。与其他模型同卡时，按已实测的组合或显存预算登记，覆盖目录内所有上游模型。[容量策略及完整示例](../../deploy/maas/README.md#动态容量只观测) |
| 网关与隔离范围 | 受保护聚合路由核对 Higress 所有正权重目的实例、重试和兜底路径后，填 `gateway_verified: true`，并将 `guard_backend_version` 设为 GPUStack 的受保护后端版本名。采用固定单卡路由时改配 `replica_routes`，逐条核对路由、部署 ID 和唯一 GPU UUID，并授予上游调用权。两种配置在同一池中互斥。[聚合路由验收](../../deploy/maas/INFERENCE-QUARANTINE.md#受保护聚合路由的额度回收) · [固定路由验收](../../deploy/maas/INFERENCE-QUARANTINE.md#逐-gpu-隔离候选) |
| 自动调节副本数（启用时） | 将新部署的 ID、最小/最大副本数、优先级、权重加入调节策略，核对写授权覆盖该 ID 和上限。先用 observe 对照实际可放置的副本数，再切换 apply。调节器决定期望副本数，MaaS 根据合格的实际副本发放并发额度。[调节策略示例与授权](../../deploy/maas/README.md#三模型副本权重准备2026-11-20) |

在已挂载候选配置、凭据文件并使用发布版本依赖的 MaaS 容器内，可执行以下只读校验。它检查目录、已启用的绑定和动态策略能否一同加载，不输出凭据。API 与所有执行器使用相同版本的应用、目录和绑定，observer 与准入进程使用匹配的容量策略。

```sh
python -B - <<'PY'
from maas_platform.auth import AuthConnector
from maas_platform.capacity_admission import configuration
from maas_platform.config import Settings

settings = Settings.from_env()
catalog = settings.catalog()
bindings = AuthConnector(settings).bindings()
policy, _, policy_hash = configuration(settings)
print(f"models={len(catalog)} bindings={len(bindings)} pools={len(policy.pools)} policy_hash={policy_hash}")
PY
```

已有逻辑名要换 revision 时，先在下游冻结该逻辑名的新提交，让使用旧定义的排队及在途任务完成，再暂停派发并切换。MaaS 的 `pause` 仍允许异步任务进入队列。需要两版并行时使用新逻辑名，任务会保留提交时的模型指纹。

常规切换时先暂停并排空，在 observer 的 `capacity.json` 核对 `expires_at`、新策略、`ready_ids` 及各池额度。受保护聚合路由还须有 `route_gpus` 和 `guard_topology` 的进程证明。在维护窗口执行私有 CLI 的 `capacity-enforce`，完成 `resume --credential-id … --probe-image …` 的真实探针。已有历史 `UNKNOWN` 时，普通切换会被阻止。仅在满足专门流程的物理隔离证据、在途检查和新鲜预算条件时使用隔离切换，保留原账本。[常规启用流程](../../deploy/maas/README.md#隔离环境启用动态准入) · [含历史隔离的切换条件](../../deploy/maas/INFERENCE-QUARANTINE.md#受保护聚合路由的额度回收)

### 13.6 验收、开放流量与回退

| 顺序 | 操作与通过条件 |
|---|---|
| 1. 授权与业务结果 | 用验收凭据查询 `GET /maas/v1/models/{name}/capabilities`，核对逻辑名、revision、输入与参数。已登记但无该模型权限的凭据应被拒绝。对异步模型按能力声明准备输入，必要时先上传到 `POST /maas/v1/artifacts`，再用唯一幂等键调用 `POST /maas/v1/tasks`。新建返回 202，轮询到 `SUCCEEDED`，下载 `result_artifact_id` 并与预期内容比对。支持同步协议的模型再通过 `POST /gpustack/v1/chat/completions` 做一次真实推理。[API 与请求格式](../api/README.md) |
| 2. 执行与计量 | 按任务和尝试关联原 GPU、进程、启动与终态。同一异步任务以相同键和相同请求重提应返回同一任务。核对 `GET /maas/v1/tasks/{id}/usage`，成功图片或页数符合样本，只有一份成功计量。同步成功时按响应头 `X-Usage-ID` 查询 `GET /maas/v1/usage/{id}`。同卡有其他模型时也要验证其探针与剩余额度。 |
| 3. 异常与补报 | 新引擎或改变终态、回执链路时，在隔离测试部署执行故障验证。先让结束回调已提交 PG 后丢失 TLS 确认，验证只重送原结束事件、GPU 只获权一次。再让回调持续不可达，确认本机留下签名回执，再在同一 VPC 替换推理容器并恢复回调，核对 timer 收到 204、文件删除、原活动从 `UNKNOWN` 结清。重复回执后结束时间和成功计量均不变，已失败的客户任务仍为 `FAILED`。沿用已验收实现时，记录所复用的版本证据并核验本模型的实际路由、挂载、timer 和终态。[断连与补报复验](../../results/maas-pg-finish-delivery-20261125/REPORT.md#修复后复验) |
| 4. 放量 | 在实测单副本并发及目标同卡布局下核对显存、结果、队列与有效额度。新副本缺少可信进程或路由证据时，相应受保护聚合池应无可派发额度。所有检查通过后再开放业务凭据、逐步放量，并记录生效版本、样本、脱敏任务 ID、结果及回退点。 |

**验收失败或上线后异常时**，先停止新流量和候选模型扩容，保留原路由、执行凭据和回执目录，处理队列及在途任务。尚有未决回执时，保留其验证和补报所需的规则、密钥及目录，直到原活动结清。对于全新逻辑名，待排空后撤回新增授权、目录、容量及调节条目，再移除候选部署。对于已有模型升级，在窗口内还原已保存的后端版本、路由、目录、绑定及策略，实际替换候选副本。若需撤回签名规则，先确认旧版本的注册凭据仍有效，在同一发布环境用以下命令将上一个可用版本的内容作为**新 revision** 发布，按 13.3 让存量 worker 接续生效。

```sh
python3 -m maas_bootstrap.publisher \
  --root "$RELEASE_ROOT" --settings "$IMAGE_SETTINGS" --signing-key "$SIGNING_KEY_FILE" \
  rollback --image-id "$IMAGE_ID" --source "$PREVIOUS_REVISION" --new-revision "$ROLLBACK_REVISION"
```

最后核对实际副本、原模型探针、有效额度、未决回执和原任务的结果下载。保留历史任务、客户 artifact、计量和仍待查证的 `UNKNOWN`。按事前清单回收本次测试数据、临时实例和验证文件，把验收与回退结果登记到变更单。
