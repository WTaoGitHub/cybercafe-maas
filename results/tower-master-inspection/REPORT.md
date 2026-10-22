# tower母版快照就绪性只读检查

2026-10-22，目标tower-mNST8iPdv。用户要求VM内不作任何改动。本轮仅查询文件元数据/公开主机密钥指纹、systemd状态、包/容器/镜像清单；未安装/删除/写入远端文件，未启动/停止/重载服务，未重启或注册GPUStack。本文仅保存在本地仓库。

## 当前证据

| 检查 | 结果 | 对母版的意义 |
|---|---|---|
| 设备machine-id | /etc/machine-id为33字节、0444；内容摘要与001/002相同，dbus文件链接同值 | 已有镜像级固定标识，不能用其区分克隆设备；供应商是否启动时重建尚未知 |
| 主机名 | tower-mNST8iPdv，/etc/hostname存在；swcfghostname.service启用、当前inactive | 有供应商命名组件，但未验证克隆首次启动时是否覆盖/如何生成，不能直接用母版hostname作为所有worker名 |
| SSH主机身份 | ED25519/RSA/ECDSA主机密钥均已存在，公开注释root@swdesk | 快照可能复制同一SSH主机身份；需确认供应商是否重建，不能现在删除，否则可能影响管理接入 |
| root SSH访问 | /root/.ssh只有authorized_keys文件，600、102字节；未读取内容；未发现该目录下客户端私钥 | root授权会随快照复制，是否允许所有克隆保留需管理员决定；不是自动删除项 |
| cloud-init | 已安装但status disabled，/etc/cloud/cloud-init.disabled存在，旧instance状态存在 | 当前不能假设cloud-init user-data会执行；需明确启用机制或使用供应商启动钩子/systemd方案 |
| 容器基础 | Docker/containerd均运行且开机启用，无容器记录 | 容器引擎已具备，尚未配置Worker启动流程 |
| NVIDIA容器支持 | 未找到nvidia-container-runtime/nvidia-ctk命令，dpkg未找到nvidia-container-toolkit、nvidia-container-runtime、libnvidia-container1 | 仍缺GPU容器运行支持；本轮不安装 |
| GPUStack | 未找到gpustack命令、镜像或相关开机单元；/var/lib/gpustack和/root/wtaobj/gpustack-worker、gpustack-server均无可见注册目录 | 常见路径未发现已注册身份污染；不是全盘秘密扫描，不据此保证系统任意位置不存在凭据 |
| 业务目录 | /root/wtaobj只有tower-cuda-upgrade | 当前没有我们安装的Worker业务层；升级日志/探针可作为后续封版审阅对象 |
| 驱动/CUDA | 驱动包580.95.05-0ubuntu1、CUDA Toolkit13.0.3-1已安装；当前6.14.0-27-generic对应DKMS580.95.05 installed；加载的仍为535.274.02 | 包安装完成但新驱动尚未激活，不应标为GPU运行验收通过，管理员统一安排生效 |
| 默认CUDA | /usr/local/cuda → /usr/local/cuda-13.0 | 新Toolkit路径已生效，旧12.2此前保留 |
| 包状态 | dpkg --audit无输出；临时policy-rc.d不存在 | 没有上次安装遗留的半配置包或临时服务阻断文件 |
| 根盘 | /dev/swblock_sysroot ext4 | 仍为供应商镜像/写回层环境，跨快照/启动持久性未验收 |
| APT缓存 | /var/cache/apt/archives约3.6G | 封版时可评估清理以减小镜像，当前不执行清理 |

## 结论与后续封版清单（本轮不执行）

当前可作为依赖构建中的母版，尚不具备克隆后自动加入GPUStack的完整条件。优先与供应商明确镜像格式/启动钩子、每机配置注入和持久目录，以及machine-id/SSH主机密钥/hostname是否由其重新生成，再确定我们需要补充的最小逻辑。

后续需安装并验证NVIDIA Container Toolkit、准备固定版本GPUStack Worker、实现每机Server URL/集群注册凭据/身份或会话配置的首次启动注入。公共快照不包含已注册worker_uuid/worker_token或每台固定worker名；无需让母版加入当前生产集群。root authorized_keys是否继承由管理员决定，不能清理掉管理通道。

母版驱动激活和GPU运行验证由管理员安排，本轮及自动化均不重启母版。001/002永久禁止VM重启，启动/克隆验证只能在另行获准的独立克隆环境进行。该检查不改变今晚P4测试安排。

## 后续授权安装更新

用户在只读检查后另行授权安装NVIDIA Container Toolkit，现已安装1.20.1并写入Docker运行时配置，未重启VM/Docker或重载驱动。原检查表是安装前快照，最新状态见 [安装报告](../tower-container-toolkit/REPORT.md)。GPU容器运行仍待管理员安排驱动与Docker生效后验证。
