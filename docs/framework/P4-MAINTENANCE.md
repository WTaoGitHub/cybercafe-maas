# 常驻环境全局维护：当前schema-v2绑定

2026-10-28更新。001仅控制端；当前对象为test1/cluster3、worker7和8、model18、route17、14副本。**全局暂停推理**，不提供E1逐worker排空和其他节点继续服务。当前演练104.875秒通过，见[报告](../../results/maas-topology-20261028/REPORT.md)。2026-10-21旧worker2/models1/9单节点绑定已过期，不得执行旧binder的`bind`，旧journal只保留审计。

> **同日最新修正**：用户确认副本数是随时可调的runtime配置。维护绑定不再批准固定副本数；每次begin从GPUStack读取期望replicas，写入该窗口journal，按该值停止/恢复。当前4只是本次只读观察，不是新的固定值。副本数变化无需重新bind；节点/模型/路由或镜像等绑定范围变化仍须审核。详见[动态副本验证](../../results/maas-runtime-replicas-20261028/REPORT.md)。

## 权限与材料

命令由001宿主机root执行。MaaS管理命令与GPUStack管理凭据留在001；专用SSH位于`/root/wtaobj/maas-live/maintenance/access-v2`，700目录/600私钥与known_hosts，仅允许001来源执行克隆forced-command actuator的status/arm/stop/restore/disarm。没有任意shell或转发权限，不向业务容器分发。

每克隆`/etc/maas/maintenance-node.json`为600，绑定当前hostname/IP、boot ID、worker容器完整ID、镜像ID、原生UUID哈希及worker ID/name。**不能进入母版快照**。克隆回收、身份变更或容器替换后先重新核验，不复用旧绑定；备份优先Mac私有目录，禁止打印key。

## 每次维护

先确认无外部直接GPUStack/Playground推理客户端，当前克隆网络边界有效；MaaS暂停只能控制MaaS请求。审核当前对象集合：有新worker/model/route或镜像、命令等部署配置变更时停止并重新审计；仅调整replicas无需重新绑定。若仍在扩缩容、有pending/error实例，则先等待健康收敛再begin，不把未就绪状态作为正常维护基线。

```bash
cd /root/wtaobj/maas-framework
python3 scripts/maas/runtime/bind_cluster_maintenance.py audit --workers 7 8 --models 18 --routes 17 --cluster 3
# 仅首次或节点/模型/路由等绑定配置变更后执行bind；仅改replicas跳过此行。
python3 scripts/maas/runtime/bind_cluster_maintenance.py bind --workers 7 8 --models 18 --routes 17 --cluster 3
# 可选：只读预览当前副本目标；begin会重新读取，预览不是冻结值。
python3 scripts/maas/runtime/maintenance_control.py --config /root/wtaobj/maas-live/maintenance/binding.json plan
python3 scripts/maas/runtime/bind_maintenance.py new-session
```

保存new-session输出的绝对operator.json路径到本次运维变量；每个窗口只分配一次，未begin的已分配窗口继续使用原文件，不重新分配。

```bash
MAAS_WINDOW_CONFIG=/root/wtaobj/maas-live/maintenance/sessions/本次窗口/operator.json
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_WINDOW_CONFIG" begin --reason 'planned maintenance'
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_WINDOW_CONFIG" status
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_WINDOW_CONFIG" stop
# 这里只允许已授权的容器/进程层维护；禁止VM/系统或Docker daemon重启。
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_WINDOW_CONFIG" recover
python3 scripts/maas/runtime/maintenance_control.py --config "$MAAS_WINDOW_CONFIG" resume
```

begin校验完整cluster范围和实际身份，读取每个模型当时的期望副本数（不是ready_replicas、实例计数或绑定时旧值），将恢复目标写入600 journal，然后pause/排空后为两节点arm恢复保护，再开启GPUStack maintenance。stop把目标全部模型副本降0，等待原生实例、真实推理容器和GPU计算进程消失，才停止原worker容器。**worker stopped不代表GPU空闲**，本流程保留实际停止核对，但只在维护窗口按需执行，不引入周期collector或每次推理SSH。

recover启动同一容器、恢复原maintenance和本窗口记录的副本数，等待这些实例全部running且位于批准的worker；分布交给GPUStack调度并记入journal，不强制复刻旧7/7或3/1分布，接单仍暂停；resume真实业务探针通过后开放接单，并取消两节点保护及001 timer。记录RESUMED后才允许下次分配窗口。任务队列/下载保留；同步暂停返回503。

## 失败和保护

001 watchdog在begin后480秒触发recover；节点watchdog在arm后600秒仅恢复原worker容器。**它们不会自动开放MaaS接单，不会解除UNKNOWN，不会重启VM。** 维护需超过保护时限时不要擅自停保护继续长时间操作，应另行设计受控窗口。

命令失败先看同一config的status与600 journal，核对timer、真实节点及MaaS generation；不盲目重放begin、创建新session或重试推理。无UNKNOWN/配置漂移且有安全证据时，在同一会话显式recover，再resume。探针失败保持暂停。ADMISSION_RESUMED表示探针已成功但可能未完成disarm，确认generation未变后重跑同会话resume仅完成清理，不重复探针。不得为“恢复”强行覆盖用户UI更改。

全局文件锁阻止并发维护动作；active/session约束阻止跨窗口覆盖。root仍可绕开限制，需运维纪律。本轮四个MaaS容器未重建，原正式库/卷保留；模型推理容器在副本0→14过程中由GPUStack重新创建。

## 网络规则运维

当前两克隆已安装`/opt/maas/ops/clone_boundary.py`及root600策略。`status`只核对规则；apply重复执行仅管理自己的链。guarded-apply先arm180秒回退，确认控制端和SSH可用且同伴被阻断后confirm；失败保留回退，不停用所有防火墙。rollback只删除MAAS-CLONE链/hooks及新dropin，不恢复全局iptables备份。策略改动必须先私有备份并审查端口/网段，不能直接从旧镜像复制节点hostname策略。

```bash
python3 /opt/maas/ops/clone_boundary.py status
```

开机unit及bootstrap dropin已enable，未做VM重启验收；母版网络文件随后已通过SSH_PORT_REDACTED安装，但新快照/独立克隆首启仍待管理员安排。通用镜像材料见[network overlay](../../deploy/bootstrap/clone/network/README.md)。E6自建collector保持退役；E1逐节点能力未上线。

## 动态副本与并发操作

例如绑定时14，管理员后来改4，再new-session/begin时读到4，该窗口执行4→0→4；下一窗口若改6，执行6→0→6。new-session只分配会话，不冻结副本；plan仅预览，begin重新读取。历史已完成journal及其恢复目标不修改，旧配置中的approved_replicas兼容性忽略，新binding不再生成此字段。

窗口创建后恢复目标固定到journal，不能在recover时读取当前0并把0当原始目标。维护前后每阶段核对配置/generation；在还没由脚本缩容时改0，或中途改为其他目标，检测到漂移后拒绝覆盖、保持暂停。GPUStack当前PUT没有接入原子比较交换/版本锁，读取和写入间仍有竞态，也不能辨认第三方写入同一个值；维护窗口内应避免另一个管理员/自动扩缩容同时改模型，不能宣称已实现跨系统分布式锁。

本轮只发布宿主维护脚本、迁移未来binding的副本来源元数据并执行只读plan，未启动实际维护或改变当前4副本；1/4/6/14等值的停止/恢复由模拟API测试覆盖。此前14副本真实演练保留为历史证据，不能视为本轮重新做过。
