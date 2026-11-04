# 运维人员手册

> **2026-11-02当前已发布ocr-metering-r2**：规范字段image_base64/image_url、旧别名兼容、异步远程URL及E16两个计量查询均已完成真实验收。4异步+3同步成功，7条唯一收据；当前tokens实际null/unavailable，不能按tokens实际收费。generation488、监控健康、UNKNOWN=0，8副本profile保持，无VM/worker/Docker引擎重启。OpenAPI已内嵌真实脱敏响应；以下待部署状态为历史。[发布报告](../../results/maas-ocr-release-20261102/REPORT.md)。


> **2026-11-02内嵌图片异步提交已部署**：应用inline-task-r1上线，generation486，接单恢复。data_url四并发同键只1任务，旧artifact方式兼容，两任务唯一尝试/成功计量和46行下载通过；同步OCR、能力schema、监控及UNKNOWN=0正常。8副本/8执行器/并发8/8、Q25/25和3.5GiB保持，无VM/worker/Docker引擎重启。下方待部署描述为历史。[发布报告](../../results/maas-inline-deployment-20261102/REPORT.md)。


> **2026-11-02 8副本候选已受控启用并收尾**：常驻R8/E8/G8/P8、全局/项目等待队列25/25、每副本3.5GiB，两卡4+4。generation484、监控正常、UNKNOWN=0；真实同步1次、异步2次（唯一尝试/计量及46行结果）通过，原配置已备份Mac。沿用10月31日完整分段T6证据，本次未跑新8小时，不宣称SLA；5%余量仍待观察。下一步E8独立适配，不自动升级现网。[部署与回退记录](../../results/maas-e3-promotion-20261102/REPORT.md)。


> E6迁移说明（2026-10-22）：正式版本e6-r1已上线，已移除同步/异步推理的动态preflight门禁，改为静态模型/revision登记并复用GPUStack服务状态。下文collector、30秒证明与MAAS_TRUSTED_DIR相关说明属于旧P2–P4基线；新部署不安装collector、不挂载consumer导出，维护/备份恢复使用真实业务探针。当前正式发布状态及操作以 [E6执行记录](../framework/E6-RETIREMENT.md) 为准。鉴权、配额、UNKNOWN和成功计量规则保留。


基线：2026-10-21当前P0–P4私有试点，不含E1。文档命令供授权运维按窗口执行，本次编写未在服务器运行它们。默认不在耐久测试期间重启、发布、维护或更改配额；确需变更时先停止负载并重新安排完整验收。

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

001 根分区实测已用约 113 GB（2026-11-04），已超过管理员提示的 100 GB 运行阈值。Docker 和应用目录共用该分区。001 的备份采用**直接流式传到异机**，压缩、加密、临时文件和最终归档都由接收端负责。现有 `backup_live.py create` 会在 001 写入多份归档，暂停用于该节点。旧的 `backup_transfer.py restore-copy` 也不得将归档传回 001 做演练。

| 备份组 | 一致性要求 |
| --- | --- |
| MAAS 业务 | 选择无 UNKNOWN、无发布或维护的窗口。暂停接单、排空任务和后端活动，停止 API、执行器及 observer 写入，保持 PostgreSQL 运行。将 `pg_dump -Fc`、artifact 文件流和配套应用配置写入同一个异机快照。 |
| GPUStack Server | 保留镜像摘要、启动配置与数据挂载。逻辑备份使用对应版本的数据库客户端；完整目录归档在 Server 容器正常停止后流式读取，保留文件 UID/GID 和权限。迁移最终备份需与 MAAS 的停写窗口协调。 |
| Registry 与部署资产 | 暂停 Registry 推送和垃圾回收后传输数据。单独加密保存 Compose、入口、Bootstrap、网络规则及凭据，记录镜像 digest 和模型 revision。可重新取得的镜像和模型按清单恢复，独有资产异机保存。 |

接收端先检查容量、可写性和加密配置，再进入停写窗口。源端使用顺序、限速读取，逐项检查发送端、传输和接收端退出状态。接收端校验大小、SHA-256 和归档可读性后才发布备份清单。断线或校验失败的副本标记为失败，仅在接收端处理临时文件。无论备份是否成功，都应检查服务状态并走探针验收后恢复接单。

解密密钥单独保管。恢复演练在独立环境进行，先按原版本恢复，再检查 GPUStack 登录及模型、Registry 镜像拉取、MAAS 历史任务和 artifact 下载，以及同步和异步推理。迁移切换前保留源端数据，目标验收后再切换入口。切换后若目标已接收新写入，回退前先处理这部分数据。

当前为运维策略，流式备份和失败恢复流程须先在隔离环境演练，生产备份周期、RPO 和 RTO 以演练结果为准。实施跟踪见 [Issue #30](https://github.com/WTaoGitHub/cybercafe-maas/issues/30)。

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
