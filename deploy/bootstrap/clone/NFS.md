# 宿主NFS按需挂载交接

2026-11-23 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6) · [实施计划](../../../docs/framework/MODEL-WEIGHTS-NFS-IMPLEMENTATION.md)

签名下发模式已通过 311 项本地 bootstrap 回归，包含新旧发布并存验证。2026-11-23，新 PC 已部署配置服务源码 `8aa8edc`，发布 `e5-nfs-signed-20261123-r1`，仅关联镜像 `tower-e5-nfs-signed-v3`。三个旧发布的指针和内容逐字节保持一致，Tower 实际 HTTPS 拉取并验签新版及两个旧 NFS 发布均通过。

Tower 已更新通用引导和启用开关，母版保护、固定 worker 镜像和定时启动配置通过核对。Tower 无 worker 容器、注册状态和 NFS 挂载。新 PC 已直接核验现有只读导出及探测文件摘要与新发布一致。待管理员保存快照并启动一台独立克隆，先从启动日志和挂载元数据确认 NFS 来源及首次访问顺序，再执行 `storage-check` 验证实际挂载和文件摘要。Docker 的路径检查也可能触发首次挂载。以下原版现场记录适用于旧快照。

客户端实现、233项回归、新PC只读NFS、001隔离试点和母版安装已完成，见[部署记录](../../../results/maas-nfs-live-20261117/REPORT.md)。新VPC 005继承文件全部匹配，首次启动因白名单缺失失败；精确放行和显式恢复后NFS/worker运行时通过，见[005记录](../../../results/maas-nfs-clone-20261117-005/REPORT.md)。平台快照ID按用户决定记录未知，零干预完整首启及推广仍待验。默认服务端目录按用户指定为`/root/maas/data/modelsWeights`，宿主入口为`/srv/maas/models`。本功能不配置模型服务容器。

## 配置交接

管理员提供可达的NFS地址、指定目录的只读export、允许的客户端网络和读取权限。只共享模型目录，不导出`/root`、暂存目录或管理凭据。模型导入/发布/回收仍是独立管理工作。若NFSv4导出使用了不同的客户端路径，必须先核对真实export，不能凭服务器磁盘路径猜测。

用户最新决定保留静态名单，默认允许`10.11.x.x`即`10.11.0.0/16`。服务端export与2049/TCP规则已同步该网段，网段内真实出口无需逐IP扩白；其他网段须另行明确授权，IPv6/UDP和只读限制保持。worker准入仍使用原批次/GPU UUID/短期许可，自动同步NFS的建议暂缓，不增加本轮验收门槛。详见[当前授权约定](../../../docs/framework/MODEL-WEIGHTS-NFS.md#worker准入与nfs授权来源2026-11-17用户补充)及[实际应用结果](../../../results/maas-nfs-static-cidr-20261117/REPORT.md)。

本次[服务端配置](../nfs-server/README.md)将该模型目录设为NFSv4根，客户端source使用`<新PC地址>:/`；示例已按此更新。物理目录仍是`/root/maas/data/modelsWeights`，不会为了遍历它而放开`/root`权限。

### NFS 配置的两种交付方式

新母版在本机 `bootstrap.json` 中设置 `"model_nfs_from_release": true`，保留 `model_root`、`model_cache_dir` 和已有信任材料。本机省略 `model_nfs`。管理员仅在 E5 签名 manifest 中以 `model_nfs` 字段发布[配置示例](model-nfs.example.json)中的数据。`source` 包含远端主机及 NFS 导出路径，`probe_file` 与 `probe_sha256` 用于显式读取验证。准备阶段使用本机固定的只读选项及按需挂载逻辑。缺少该字段、验签失败或发现现有挂载来源不符时，引导停止并保留现场。

旧快照继续读取各自的已有发布。采用旧 NFS 配置的快照仍要求本机 `model_nfs` 与签名发布完全一致。原本使用本地模型目录的快照继续使用本地目录。新版母版使用新的 `image_id`，发布时只切换该 ID 的指针。迁移 NFS 时，通过新 revision 更新该母版所用的来源和校验信息，新启动的克隆可继续使用同一份母版。运行中的 worker 和现有挂载需要按原维护流程处理。

NFS母版使用独立逻辑`image_id`。配置发布器的`publish --image-id <该ID>`将新版本激活到`images/<该ID>.json`，manifest的`compatible_image_ids`须仅包含该ID；NFS发布不允许省略此参数来覆盖默认`active.json`。配置读取、精确revision读取和自动/人工worker许可均按同一image_id选择版本。旧节点继续默认发布，GPU UUID预登记/发证不变；回退同样使用`rollback --image-id <该ID>`。发布文件不可覆盖，回退创建新revision。

示例测试文件的内容为`host-nfs-pilot-v1`加一个LF换行，SHA-256已固定在示例中。管理员放置此小文件供显式试读；也可选择另一个不超过1MiB的普通文件并登记实际摘要。启动阶段只检查配置，不读取该文件。

母版/试点客户端需要Ubuntu的`nfs-common`和系统systemd工具。本次源码交付包和离线母版安装器已纳入`maas_bootstrap/nfs.py`及配置示例；安装器不代替系统包安装。真实母版已另行按核验过的在线流程备份、安装并读回，版本为`48ff46a`。

## 启动与实际访问

沿用系统就绪和供应商命名完成后的延后启动链，在原有worker许可/启动前准备宿主automount。使用`systemd-mount --automount=yes`创建临时`.automount`/`.mount`，不修改`/etc/fstab`，不重启Docker或VM；每次启动从受控配置重新准备。

准备阶段只检查本地mountinfo及systemd属性，不对远端目录做resolve、stat、列目录或摘要读取。没有实际NFS挂载时，只有经过校验的systemd autofs入口才算已准备，空本地目录不能冒充成功。已有非空本地目录、错误来源或其他挂载一律报错，不自动遮蔽或卸载。

首次实际访问才由systemd尝试NFS连接，固定选项为`ro,hard,proto=tcp,fg,retry=0,nosuid,nodev`。不设置闲置自动卸载。挂载单元使用20秒启动超时、无限计数窗口内最多一次启动；失败状态保留，不自动reset-failed或循环重连。管理员确认现场后再恢复。systemd的挂载超时不等于已挂载文件的hard NFS读取具有20秒超时，内核I/O卡住仍需独立故障验收。

任何进程访问目录都可能触发，包括`ls`、巡检和Docker自身的路径检查；这里只实现文件系统按需访问，不能识别“是否部署了模型”的业务意图。容器映射与挂载传播由模型部署业务另行处理，宿主入口就绪不能作为容器可见性的证据。

## 独立宿主验收

在无业务试点的独立目录和独立私有配置中进行；不得直接覆盖既有`/srv/maas/models`。两个命令均不启动worker或申请GPU许可：

```sh
/opt/maas/bin/maas-worker-bootstrap storage-prepare
/opt/maas/bin/maas-worker-bootstrap storage-check
```

`storage-prepare`先验签再准备入口，返回`MODEL_STORAGE_PREPARED`、`nfs_state=automount`、`probe_verified=false`。此时应采集systemd/mountinfo与NFS连接计数，证明未访问远端；不能把已准备写成可读取。

`storage-check`是显式的首次访问，会触发挂载，核对真实来源、类型、ro及测试文件摘要，成功后返回`MODEL_STORAGE_VERIFIED`、`nfs_state=mounted`、`probe_verified=true`。在新模式下，本命令先拉取和验证当前签名发布，要求配置服务可达，并按该发布核对已有挂载；旧模式沿用本机配置。禁止把该命令加进启动准备或定时巡检来破坏按需行为。独立export还须分别验证服务端拒写、不可达和错误来源，不对已发布模型执行写入测试。

本地回归从仓库根目录运行 `python -m pytest tests/bootstrap`。NFS 用例模拟 systemd 与挂载元数据，覆盖配置验签、启动阶段仅准备入口、显式摘要校验和已有挂载拒绝换源。发布用例核对新旧镜像同时读取各自的签名发布。离线构建用例核对新母版只保留启用开关和本地路径。

客户端子进程操作20秒超时后保留本机私有`state_dir/nfs-operation.json`，下一次准备明确失败。该文件也保护父进程被中断时的现场；不得定时清理。先检查记录中的PID、systemd单元和实际挂载，确认无未完成操作，再由管理员解除记录及失败状态。禁止依赖强制卸载或VM重启恢复。

XZ-31-001只做获准的宿主试点；001/002不得整机重启、重建或还原快照。母版安装、管理员保存/应用快照和独立新VPC真实首启分别取证；容器和模型请求不在本功能验收内。
