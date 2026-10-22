# 母版NVIDIA Container Toolkit安装

2026-10-22，用户授权在tower-mNST8iPdv上安装。XZ-31-001/002未修改。

已从既有NVIDIA官方签名APT源安装同版本四包，均为1.20.1-1：nvidia-container-toolkit、nvidia-container-toolkit-base、libnvidia-container-tools、libnvidia-container1。nvidia-ctk和nvidia-container-runtime均报告1.20.1，dpkg --audit无输出。

已执行仅写配置的`nvidia-ctk runtime configure --runtime=docker`，/etc/docker/daemon.json包含nvidia runtime，默认仍为runc，原其他配置核对保持。旧文件如存在则私有备份到目标机/root/wtaobj/tower-container-toolkit/daemon.before.json，不导出内容。

未重启VM、Docker或其他服务，未主动重载驱动模块。Docker MainPID及启动时间、VM boot ID、加载的535.274.02驱动安装前后均保持。临时policy-rc.d已清理。原580.95.05磁盘驱动及CUDA13.0.3不变，未添加已注册GPUStack身份。

母版或独立克隆下一次由管理员安排驱动生效及Docker启动/重启后才验证GPU容器；当前GPU容器运行尚未测试，不能标记GPU容器可用。此报告不授权自动重启或绕开现有001/002永久禁止重启要求。

[验证证据](verification.json)。安装脚本：scripts/tower/install_container_toolkit_no_restart.py；远端安装日志及元数据：/root/wtaobj/tower-container-toolkit。
