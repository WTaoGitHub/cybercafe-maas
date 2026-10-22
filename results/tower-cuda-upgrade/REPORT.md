# tower驱动与CUDA升级（不重启）

用户授权仅在tower-mNST8iPdv（111.4.255.126:6025）安装NVIDIA 580.95.05和CUDA Toolkit 13.0，后续激活由管理员统一安排。XZ-31-001/002未参与。

## 驱动阶段

已安装NVIDIA官方Ubuntu 24.04包`nvidia-driver-580=580.95.05-0ubuntu1`，内核6.14.0-27-generic对应DKMS模块编译安装成功，磁盘模块版本580.95.05。当前加载的内核模块仍为535.274.02，未重启、未主动卸载/重载模块。

原Ubuntu缓存中580.95.05-0ubuntu0.24.04.2部分文件404，切换到官方签名源相同上游版本的0ubuntu1包。安装时遇到旧nvidia-utils-535文件所有权冲突，显式移除已无须保留的旧工具包并补齐libnvidia-compute-580依赖解决，未使用force-overwrite或autoremove。

为固定用户指定的版本，目标机`/etc/apt/preferences.d/tower-r580-5809505`保存版本约束；管理员以后升级驱动时应审查该约束。包管理阶段使用临时policy-rc.d和NEEDRESTART_MODE=l延后服务启动/重启，安装流程不包含VM重启或主动驱动模块重载。

[驱动证据](driver-installed.json)。原包清单和完整日志保存在目标机/root/wtaobj/tower-cuda-upgrade。驱动用户态库已更新、内核模块尚未切换，此过渡阶段可能无法运行nvidia-smi或CUDA程序，不能据此认定新驱动已生效；管理员激活后须验证。

## Toolkit阶段

正在安装cuda-toolkit-13-0；保留/usr/local/cuda-12.2。尚不标记整体完成，编译及最终包审计将在安装结束后核对。
