# NFS服务端、宿主试点与母版软件安装

本报告保留母版安装时点的结果。后续已交接新VPC 005，文件继承和人工恢复后运行时通过，首次零干预启动失败；平台快照ID按用户决定记录未知。详见[005最新报告](../maas-nfs-clone-20261117-005/REPORT.md)，以下“尚未交付”对应安装时点。

2026-11-17 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6) · [实施计划](../../docs/framework/MODEL-WEIGHTS-NFS-IMPLEMENTATION.md)

## 当前结论

新PC只读NFSv4服务已安装，001隔离宿主目录的真实读取、服务端拒写及连接中断/恢复已取证；母版已实际安装NFS客户端、引导文件和独立签名配置，并完成文件摘要、保护及无实例身份检查。**管理员尚未交付新快照和独立新VPC；快照保存、选择/应用、零干预首启和推广未验收。** 不将母版文件安装写成快照已经生效。

本次只交付宿主目录按需访问，不配置模型副本容器、模型路径、调度或推理，也不实现模型导入/发布应用。001/002未执行整机重启及等效操作；001原worker、正式配置和已有模型目录保持。旧OCR的镜像权重及Unlimited-OCR-2的本地目录未迁移。

## 版本与证据层次

| 对象 | 实际版本或状态 |
| --- | --- |
| 宿主客户端初版 | `3d25ef0ddd649800f99738747d0274c6072cd0c7`；001隔离源码以此版试点 |
| 镜像范围配置发布及母版软件 | `48ff46a6f5b6a853f28d343ef1adba0488ee4eaf` |
| 服务端模板 | 初版`db71fc1`，子树边界修正`96d8051`已实际应用并读回 |
| 本次母版source-and-trust包 | 36成员；SHA-256 `0d5a8316cbe617a67e0b7e330e79991424fd64d730be221692cbf6be31bc65e5` |
| 实际逻辑image_id | `tower-e5-nfs-automount-v1`；与平台snapshot ID分别登记 |
| 签名配置revision | `nfs-automount-20261117-r1`，按该image_id发布 |
| 母版软件安装 | 已安装，逐文件清单见[INSTALLATION.json](INSTALLATION.json) |
| 平台快照保存、选择/应用 | 待管理员交接实际ID及实例对应关系 |
| 独立新VPC首启、小批推广、存量维护 | 待验收 |

交付包是代码及公开信任材料，包内通用示例image_id不代表实际部署。真实母版以安装清单及本机受控配置为准。私钥、共享码、原始配置/连接信息、主机/设备启动标识和原始日志均留在Git忽略的私有操作目录，不随本报告发布。

## 服务端实际安装

用户指定的`/root/maas/data/modelsWeights`设为专用NFSv4根，客户端source为已核验的服务器地址加`:/`；此根只包含模型库，不能解释为导出服务器系统根。只绑定核验的私网IPv4，仅启用NFSv4/TCP，exports与2049网络规则均限定到试点实际源地址。未放开`/root`权限，未开放rpcbind/UDP或任意客户端网段。

配置实际安装到`/etc/nfs.conf.d/maas-models.conf`、`/etc/exports.d/maas-models.exports`及nfs-server网络drop-in。导出为`ro,sync,fsid=0,subtree_check,root_squash,all_squash,anonuid=65534,anongid=65534`。源配置和`/var/lib/nfs/etab`均确认子树检查开启；`exportfs -v/-s`省略该正向默认标志，不能仅凭该简略输出判断未生效。

安装模拟确认0升级、5新增后补齐NFS服务依赖；禁止needrestart自动重启现有服务，未运行autoremove或更新GPU驱动。rpcbind原无业务，服务/socket保持masked，不新增111监听。安装前后主机启动标识、Docker及既有容器启动时间保持。模型库只增加固定小测试文件及独立权限试验目录，未导入真实模型；配置/备份/运维日志位于模型库之外。

固定测试文件`probe.txt`内容为`host-nfs-pilot-v1`加LF，SHA-256为`489df92a840799f19a74346eb75992ad62213022fab927b8f422c1b3b8837867`。服务端匿名UID在独立试验目录具备本地写入权限；另由001以独立`rw,nosharecache`挂载验证实际VFS和superblock均为rw，此时写入仍返回EROFS，证明拒写来自服务端export。试验挂载已正常卸载。非试点来源的2049连接被拒，不能据此宣称所有worker网络均已放行。

## 001真实宿主试点

保留正式`/srv/maas/models`及worker；所有挂载使用独立`/srv/maas/nfs-*`目标、缓存和私有状态。不替换正式引导配置、不申请新GPU许可、不创建推理服务。

- 两次准备均为本地automount，`probe_verified=false`，首次访问前NFS TCP连接数为0；systemd实际读回20秒启动超时及一次启动限制。
- 显式首次读取触发正确NFSv4/TCP只读挂载，固定文件摘要一致；重复读取成功。小文件首次访问为0.283秒，只代表该次宿主试验，不是权重加载或吞吐性能结论。
- 客户端拒写、错误摘要和错误来源均被拒绝，恢复原配置后读取成功；服务端拒写另按上述独立rw视图取证。
- 首次挂载前仅在001阻断到试点NFS端口的连接，首次访问以`NFS_PROBE_FAILED`失败（10.135秒），mount单元failed且ControlPID=0；再次准备明确返回`NFS_AUTOMOUNT_FAILED`，InvocationID未变，没有reset或重新挂载。故障规则及试验入口已清理，规范化规则和原worker基线保持。
- 连接已建立后，只对该隔离NFS连接的TCP五元组注入丢包；读取另一个未读测试文件在20.024秒返回`NFS_OPERATION_TIMEOUT`并保留私有操作记录，下一次操作被`NFS_OPERATION_PENDING`阻止。没有停止共享NFS或影响其他连接。
- 移除精确故障规则后，确认原helper已回收、恢复后未缓存文件可读、正常卸载成功，才归档超时记录并进行人工恢复验证；没有自动清除记录或重试。此例helper已退出，不宣称实测了不可回收D态。
- 原始防火墙全文比较包含注释/计数器而报不等，原结果完整保留。后续规范化读回确认规则一致、故障规则不存在、clone boundary仍有效，试验NFS挂载全部清理。
- 001新增客户端依赖使用4个新增包、0升级；其原先无业务的rpcbind service/socket保持禁用，随后再次通过真实NFSv4读取。

准备、读取、故障和清理各自核对VM启动标识、Docker主进程/启动时间、原worker容器ID/启动时间及正式配置摘要，均未改变。001仍为原有本地模式节点；隔离目录通过不代表其正式目录已改为NFS。

## 配置服务及旧节点兼容

为NFS母版启用独立`images/<image_id>.json`指针，manifest兼容范围仅为该逻辑image_id。更新后的配置读取及人工/自动许可均使用同一范围；NFS版本禁止覆盖默认active.json。签名私钥继续留在服务端私有目录，不进入NFS、母版或公开包。

新PC的Bootstrap基础设施容器已使用不可变源码目录切换到`48ff46a`；镜像、私有挂载、只读rootfs、权限收敛及重启策略均核对。旧容器停止保留供进程级回退，其他容器未重建；没有切换GPUStack worker或模型容器。

默认active.json字节及001实际HTTPS配置响应在切换前后完全一致；001按原信任材料成功验签，revision仍为`baremetal-diskless-r1`，manifest摘要为`6f3c3a59eba1e0fc8d604527f4b35839912ec6d069ca79421f8033a8ee2c8207`。新母版按新image_id获取并验签NFS配置成功，不通过更换全局发布让存量节点意外启用NFS。

## 母版实际安装与封版检查

用户交接当前母版后，以既有可信主机密钥核验新SSH入口，确认母版保护、无容器及无实例状态。修改前将原模块、配置、单元/启用关系、模型marker及软件清单备份到母版私有目录，并下载到本机私有目录核对整包SHA；未把离线安装器指向运行系统根目录。

确认0升级、4新增包后安装nfs-common；不升级驱动、不重启Docker或VM。原子更新11个worker模块、固定入口、service/timer/path及root-only配置。配置仅增加独立image_id与model_nfs，保留原证书、公钥、共享码、GPU UUID注册方式和已有网络策略。原模型根只有公开image-contained marker，完整移到私有备份，挂载点保持空目录，不遮蔽本地权重。

实际读回确认：

- 所有安装文件的摘要、root属主和权限匹配；旧image-manifest与新增nfs-install-manifest均记录新逻辑image_id及当前摘要。
- 本机配置和实际HTTPS签名release完全匹配，固定worker镜像已缓存；未调用apply、自动预登记、发证或worker启动。
- `boot-check`明确返回`PROTECTED_HOST_WORKER_FORBIDDEN`；母版无容器、无模型NFS挂载，缓存仍为本地可写目录。
- timer保持开机enabled，path保持disabled；母版维护期间timer/service停止，不使用`--now`启动准备链。新VPC首启是否继承及执行仍待实测。
- 无原生worker token、设备私钥/证书、实例身份、许可、Registry认证及applied/maintenance状态；未安装旧退役插件。保留供应商OS/SSH身份处理流程，没有为了封版擅自删除machine-id或主机密钥。
- 母版启动标识、Docker、信任材料及网络策略保持；软件安装不代表平台已经保存或应用快照。

## 取证脚本偏差与处理

| 编号 | 原始结果与处理 | 状态 |
| --- | --- | --- |
| NFS-INC-001 | 故障清理后原始iptables-save全文不等；进一步剔除注释和链计数器后规则一致，故障规则不存在。保留原false结果及两轮校正读回，不改写首次试验 | 已解决，校验口径偏差 |
| NFS-INC-002 | 候选Bootstrap容器挂载列表顺序不同导致校验失败；按挂载集合核对内容、读写及安全属性后通过，再切换服务 | 已解决，校验顺序偏差 |
| NFS-INC-003 | 母版验证脚本误把is-enabled的disabled退出码及无关binfmt autofs当成失败；按systemd契约和模型目标范围修正后读回通过。安装结果分开保存，未重新执行已完成安装 | 已解决，校验脚本偏差 |
| NFS-INC-004 | 子树更新后exportfs简略输出未显示正向标志；源文件和etab均为subtree_check，随后NFS读取通过 | 已解决，读回接口偏差 |
| NFS-INC-005 | 首挂失败试验脚本第一次未传子进程PYTHONPATH，在插入故障规则前停止；第二次错误码断言过窄，finally已清理规则/入口。第三次记录实际失败码及单元不重试后通过；三次结果保留，尝试次数不重置 | 已解决，试验脚本偏差 |

上述编号是执行/取证故障，不是E编号增强项；原始失败和校正结果均在私有证据中保留，不将脚本失败抹成第一次即通过。

## 管理员下一步与验收边界

先保存当前母版的新快照，登记真实snapshot ID/版本。先用它创建一个全新可销毁VPC，通过后再提供第二个；交付快照与实例对应、GPU分配和安全SSH入口。首次访问NFS前核对真实出口源地址并同步更新受限export与2049规则，不直接扩大为全部网段。当前仅允许已核验试点地址。

新VPC不手工mount、apply或复制配置来补救首启。采集供应商命名、延后准备、automount及自动注册的真实时序，再分别验证来源/ro/文件摘要与重复应用。准备代码不主动读取远端；Docker路径检查等其他实际文件访问也可能触发NFS，应记录真实触发时刻，不能声称整个首启必然零连接。旧VPC只核对未受影响，不重建001/002来“对比”。

回退先保留现场并核对占用：Bootstrap服务可回到保留的旧容器/不可变源码；按image_id回退配置须与本机策略一致。母版软件按私有备份恢复，NFS挂载正常解除后再恢复原目录；不强制卸载、不删除权重、不对001/002还原快照。快照或新VPC失败时保留失败证据和尝试次数，由管理员另交付获准实例。

`tests/bootstrap`与`tests/test_private_connections.py`回归233项通过，只有已有Starlette弃用提示。未验证真实模型权重性能、内核不可回收D态、HA或大量并发；平台快照、新VPC首启、小批推广及存量迁移仍待交付，不能标记整个计划完成。
