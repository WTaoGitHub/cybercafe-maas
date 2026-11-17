# 宿主NFS按需挂载交接

2026-11-17 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6) · [实施计划](../../../docs/framework/MODEL-WEIGHTS-NFS-IMPLEMENTATION.md)

客户端实现和离线回归已完成，真实服务端、首次访问和新快照首启仍须分别验收。默认服务端目录按用户指定为`/root/maas/data/modelsWeights`，宿主入口为`/srv/maas/models`。本功能不配置模型服务容器。

## 配置交接

管理员提供可达的NFS地址、指定目录的只读export、允许的客户端网络和读取权限。只共享模型目录，不导出`/root`、暂存目录或管理凭据。模型导入/发布/回收仍是独立管理工作。若NFSv4导出使用了不同的客户端路径，必须先核对真实export，不能凭服务器磁盘路径猜测。

将[配置示例](model-nfs.example.json)作为`model_nfs`字段，分别加入本机root-only的`bootstrap.json`与对应的签名release manifest；两处必须完全一致。示例地址为文档保留地址，须替换为实际NFS地址。不能仅修改本机而绕过签名，也不能给未配置NFS的节点直接发布启用NFS的release；兼容image_id与发布范围须对应。未配置该字段的节点保留原本地存储流程。

示例测试文件的内容为`host-nfs-pilot-v1`加一个LF换行，SHA-256已固定在示例中。管理员放置此小文件供显式试读；也可选择另一个不超过1MiB的普通文件并登记实际摘要。启动阶段只检查配置，不读取该文件。

母版/试点客户端需要Ubuntu的`nfs-common`和系统systemd工具。本次源码交付包和离线母版安装器已纳入`maas_bootstrap/nfs.py`及配置示例；安装器不代替系统包安装，也不代表已经更新真实母版。

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

`storage-check`是显式的首次访问，会触发挂载，核对真实来源、类型、ro及测试文件摘要，成功后返回`MODEL_STORAGE_VERIFIED`、`nfs_state=mounted`、`probe_verified=true`。禁止把该命令加进启动准备或定时巡检来破坏按需行为。独立export还须分别验证服务端拒写、不可达和错误来源，不对已发布模型执行写入测试。

客户端子进程操作20秒超时后保留本机私有`state_dir/nfs-operation.json`，下一次准备明确失败。该文件也保护父进程被中断时的现场；不得定时清理。先检查记录中的PID、systemd单元和实际挂载，确认无未完成操作，再由管理员解除记录及失败状态。禁止依赖强制卸载或VM重启恢复。

XZ-31-001只做获准的宿主试点；001/002不得整机重启、重建或还原快照。母版安装、管理员保存/应用快照和独立新VPC真实首启分别取证；容器和模型请求不在本功能验收内。
