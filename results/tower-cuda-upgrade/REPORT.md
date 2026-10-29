# tower驱动与CUDA升级（不重启）

用户授权仅在tower-mNST8iPdv（192.0.2.10:SSH_PORT_REDACTED）安装NVIDIA 580.95.05和CUDA Toolkit 13.0，后续激活由管理员统一安排。XZ-31-001/002未参与。

## 驱动阶段

已安装NVIDIA官方Ubuntu 24.04包`nvidia-driver-580=580.95.05-0ubuntu1`，内核6.14.0-27-generic对应DKMS模块编译安装成功，磁盘模块版本580.95.05。当前加载的内核模块仍为535.274.02，未重启、未主动卸载/重载模块。

原Ubuntu缓存中580.95.05-0ubuntu0.24.04.2部分文件404，切换到官方签名源相同上游版本的0ubuntu1包。安装时遇到旧nvidia-utils-535文件所有权冲突，显式移除已无须保留的旧工具包并补齐libnvidia-compute-580依赖解决，未使用force-overwrite或autoremove。

为固定用户指定的版本，目标机`/etc/apt/preferences.d/tower-r580-5809505`保存版本约束；管理员以后升级驱动时应审查该约束。包管理阶段使用临时policy-rc.d和NEEDRESTART_MODE=l延后服务启动/重启，安装流程不包含VM重启或主动驱动模块重载。

[驱动证据](driver-installed.json)。原包清单和完整日志保存在目标机/root/wtaobj/tower-cuda-upgrade。驱动用户态库已更新、内核模块尚未切换，此过渡阶段可能无法运行nvidia-smi或CUDA程序，不能据此认定新驱动已生效；管理员激活后须验证。

## Toolkit阶段

已安装`cuda-toolkit-13-0=13.0.3-1`，nvcc版本13.0.88；`/usr/local/cuda`默认指向`/usr/local/cuda-13.0`，原`/usr/local/cuda-12.2`及其编译器12.2.91仍保留。未添加全局LD_LIBRARY_PATH。

为RTX 4070的sm_89架构编译cuda13_probe.cu成功；没有执行GPU探针。`dpkg --audit`为空、`apt-get check`返回0，DKMS对应当前6.14.0-27-generic为installed。临时policy-rc.d已清理，安装前后boot ID一致；当前加载驱动仍为535.274.02，未重启/主动重载。

当前nvidia-smi返回Driver/library version mismatch，NVML库580.95与已加载535模块不一致；这是此次分阶段安装的待激活状态，不标记GPU/CUDA运行验收通过。管理员安排新驱动生效后，再执行nvidia-smi和`/root/wtaobj/tower-cuda-upgrade/cuda13_probe`核对实际GPU运行；本轮不提供或执行自动重启步骤。

完整无凭据核对见 [最终验证](verification.json)。源码与脚本保存在scripts/tower；远端日志、原包清单及探针位于/root/wtaobj/tower-cuda-upgrade。
