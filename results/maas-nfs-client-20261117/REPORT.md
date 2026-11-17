# NFS按需挂载客户端实现与基线预检

2026-11-17 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6)

## 当前结论

已实现客户端和离线验证，已连接用户指定的001进行只读基线预检；未宣称集中NFS已经部署完成。服务端目录按用户指定为`/root/maas/data/modelsWeights`，worker宿主入口为`/srv/maas/models`，启动只准备本地automount，首次访问才尝试连接。真实NFS服务端/首次读取、母版安装、快照保存/应用及新VPC首启均待验。

## 实现

- root-only本机`model_nfs`与签名manifest须完全一致；未配置者保持原本地模式，不接受任意容器挂载或shell选项。
- 使用systemd瞬态automount/mount单元，不改fstab，不在启动阶段resolve/stat远端模型目录或读测试文件，不向NFS写marker。返回已准备/已挂载及probe_verified，避免混淆。
- 复用现有延后启动与GPU UUID链，在短期许可前准备本地入口，不修改推理容器、模型路径/来源、标签/选择器或API服务。
- 重复应用不叠加挂载；非空本地目录、其他来源、可写/soft挂载及不匹配的automount单元明确失败。首次挂载失败保留systemd失败与启动限制；显式检查子进程超时保留私有操作记录，不自动重放或重启VM。
- 新增不启动worker的`storage-prepare`及显式访问的`storage-check`，交付包与离线母版安装器包含新模块及配置示例。实际依赖安装、母版和平台操作仍需环境验收。

## 验证

变更前bootstrap基线164项通过。变更后`tests/bootstrap`与`tests/test_private_connections.py`共223项通过，1条已有Starlette依赖弃用提示。覆盖本机/签名配置、按需准备不读远端、autofs与真实NFS分态、重复准备、错误/叠加挂载、只读约束、特殊/超大测试文件、子进程卡住保留现场、无自动重试、GPU许可前置顺序、独立CLI及离线母版交付。

这些测试模拟mountinfo/systemd/NFS，不证明Linux真实按需访问、服务端拒写或内核I/O故障已经通过。

## 001只读基线与环境交接

用户提供的新入口已连接并核验主机角色。001为Ubuntu/systemd 255环境，具备systemd-mount；当前缺少nfs-common。宿主`/srv/maas/models`已有image-contained目录，原生GPUStack worker进程仍在运行。本次不覆盖该目录，真实宿主试点使用独立目录及独立私有状态，不修改现有worker布局。

从001读取的既有控制服务地址尚不能建立NFS TCP连接；这不证明SSH、export或NFS已配置。新PC的安全SSH入口/实际export及允许网络仍待交接；母版由用户联系管理员启动，未继续连接或修改母版。历史旧入口超时不再当作001当前不可达。

原始连接信息、配置和预检输出保留在本机Git忽略的私有操作目录，未写入公开报告。未执行VM/Docker重启、容器重建或快照操作，也未部署旧退役插件。

## 下一门槛

核对服务端只读export和真实数据通道；补齐001客户端依赖，在独立目录分别取证“入口准备未连远端”和“首次访问成功/只读/故障”。试点通过后才实际安装母版。管理员保存并应用新快照后，在获准独立新VPC采集零手动补配置首启；001/002不通过整机重启补测。
