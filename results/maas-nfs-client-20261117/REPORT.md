# NFS按需挂载客户端实现与基线预检

2026-11-17 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6)

## 当前结论

已实现客户端和离线验证，并在用户指定的001完成独立目录的真实Linux automount准备/幂等检查；未宣称集中NFS已经部署完成。服务端目录按用户指定为`/root/maas/data/modelsWeights`，worker宿主入口为`/srv/maas/models`，启动只准备本地automount，首次访问才尝试连接。真实NFS服务端/首次读取、母版安装、快照保存/应用及新VPC首启均待验。

## 实现

- root-only本机`model_nfs`与签名manifest须完全一致；未配置者保持原本地模式，不接受任意容器挂载或shell选项。
- 使用systemd瞬态automount/mount单元，不改fstab，不在启动阶段resolve/stat远端模型目录或读测试文件，不向NFS写marker。返回已准备/已挂载及probe_verified，避免混淆。
- 复用现有延后启动与GPU UUID链，在短期许可前准备本地入口，不修改推理容器、模型路径/来源、标签/选择器或API服务。
- 重复应用不叠加挂载；非空本地目录、其他来源、可写/soft挂载及不匹配的automount单元明确失败。首次挂载失败保留systemd失败与启动限制；显式检查子进程超时保留私有操作记录，不自动重放或重启VM。
- 新增不启动worker的`storage-prepare`及显式访问的`storage-check`，交付包与离线母版安装器包含新模块及配置示例。实际依赖安装、母版和平台操作仍需环境验收。

## 验证

变更前bootstrap基线164项通过。变更后`tests/bootstrap`与`tests/test_private_connections.py`共223项通过，1条已有Starlette依赖弃用提示。覆盖本机/签名配置、按需准备不读远端、autofs与真实NFS分态、重复准备、错误/叠加挂载、只读约束、特殊/超大测试文件、子进程卡住保留现场、无自动重试、GPU许可前置顺序、独立CLI及离线母版交付。

离线测试模拟mountinfo/systemd/NFS，不证明真实远端访问、服务端拒写或内核I/O故障已经通过。下述001实测另行证明本地automount准备，仍不代替远端读取。

## 001只读基线与环境交接

用户提供的新入口已连接并核验主机角色。001为Ubuntu/systemd 255环境，具备systemd-mount；基线缺少nfs-common。宿主`/srv/maas/models`已有image-contained目录，原生GPUStack worker进程仍在运行。本次不覆盖该目录，宿主试点使用独立目录及独立私有状态，不修改现有worker布局。

从001读取的既有控制服务地址尚不能建立NFS TCP连接；这不证明SSH、export或NFS已配置。新PC的安全SSH入口/实际export及允许网络仍待交接；母版由用户联系管理员启动，未继续连接或修改母版。历史旧入口超时不再当作001当前不可达。

原始连接信息、配置和预检输出保留在本机Git忽略的私有操作目录，未写入公开报告。未执行VM/Docker重启、容器重建或快照操作，也未部署旧退役插件。

## 001客户端准备与独立实测

模拟安装计划确认只新增4个包、无升级/删除后，补齐nfs-common及其依赖，禁止安装工具自动重启既有服务。安装前后VM启动标识、Docker主进程及启动时间、原生worker容器ID/启动时间/运行状态一致；没有执行apt autoremove或驱动变更。

公开交付包基于已提交并推送的`3d25ef0ddd649800f99738747d0274c6072cd0c7`构建，30个成员，SHA-256为`94d307f87abda2430303de2e930deec8820a00b2423ccef8a9a07b46ba8226b1`。本机及001隔离源码目录均逐文件核对bundle manifest，未替换正式`/opt/maas/lib`或本机Bootstrap配置。

使用隔离的宿主目标、缓存和私有状态目录，直接调用该提交的宿主准备代码两次，不经过worker启动、发证或模型部署。结果为`nfs_state=automount`、`probe_verified=false`，两次一致；实际NFS mount单元始终`inactive`、`ControlPID=0`、`Result=success`，前后NFS TCP连接数均为0，未执行测试文件读取。systemd实际读回`TimeoutUSec=20s`、`StartLimitIntervalUSec=infinity`、`StartLimitBurst=1`，确认本机版本接受这些参数。

该试验验证“本地准备不启动远端挂载”，不证明首次访问、服务器权限或网络故障。试验autofs入口已正常停止，未强制卸载；源码和本地依赖保留供后续独立试点。前后VM、Docker、原worker及正式Bootstrap配置摘要保持一致。由于尚未取得新PC的SSH/export交接，本轮未触发真实NFS首次访问，也未将001改为正式NFS节点。

## 下一门槛

取得新PC安全SSH入口，核对服务端只读export和真实数据通道，再在001独立目录验收首次访问、只读拒写、错误/不可达及恢复。完整试点通过后才实际安装母版；母版当前由用户联系管理员启动。管理员保存并应用新快照后，在获准独立新VPC采集零手动补配置首启；001/002不通过整机重启补测。
