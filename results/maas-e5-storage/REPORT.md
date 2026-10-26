# 独立模型存储与身份卷排除 · 2026-10-26

结论：代码实现和新克隆容器级迁移验证通过，OCR/MaaS已恢复。母版6025当前不可达，用户明确稍后恢复时通知，因此母版部署、保存快照及新版克隆首启尚未完成，不宣称全流程交付结束。

## 已实现

| 目录 | worker | 推理容器 |
|---|---|---|
| /var/lib/maas-bootstrap/gpustack-worker → /var/lib/gpustack | 原生身份和运行数据，可读写 | 排除，不继承 |
| /srv/maas/models | 独立发布目录，只读 | 原生mirrored继承，只读 |
| /srv/maas/model-cache | 独立下载缓存，可读写 | 原生mirrored继承，可读写 |
| Docker socket | worker调度需要 | 原生过滤，不继承 |

保留固定GPUStack镜像的既有vendor ignore列表，追加/var/lib/gpustack；显式指定mirrored自身名称。GPUSTACK_CACHE_DIR切到独立缓存；正确设置worker metrics port 10151。公开marker由bootstrap准备，无需docker cp；实际OCR程序及权重仍在镜像中。

启动布局具有model-storage-v1版本和独立SHA256；真实Mounts、读写属性、关键环境、版本标签不一致时返回NEEDS_MAINTENANCE，不能因旧manifest fingerprint相同而误报UNCHANGED。模型根目录/缓存/身份目录及秘密路径不得重叠，不接受宽泛系统根、根路径/marker符号链接或可被其他用户写入的存储根。57项bootstrap测试通过，其中17项新增用例覆盖隔离、布局漂移及维护备份许可。

## 实际迁移与验收

1. 先私有备份至Mac；新克隆安装新client，旧布局apply实际返回NEEDS_MAINTENANCE，原worker未被自动修改。
2. 不使用旧维护binding。显式暂停/排空MaaS（generation23），维护worker5，旧model12副本降0并确认实例删除。
3. 停止并重命名旧worker容器作为回退，保留原生数据；写root-only维护备份许可，再由新bootstrap创建worker。worker ID仍5，UUID/name/token/JWT文件字节均未变化，重复apply=UNCHANGED；VM boot及Docker PID未变。
4. 新建model16/route15/instance98，revision=ocr-e5-r2，使用新公开路径。旧model12、route14及ocr-e5-r1记录保留，副本0，没有用同一revision覆盖配置变更。
5. 实际推理容器仅有模型根与缓存两个挂载：marker可读，发布目录写入失败EROFS，缓存写入成功且宿主机可见，身份文件及Docker socket不可见；worker内身份仍存在。
6. 新MaaS路由以真实探针恢复；私有TLS同步200/46行、异步SUCCEEDED/attempts1、结果下载200/新revision、SQL成功1条及usage事件1条。收尾未暂停、UNKNOWN=0、新worker/model ready。

GPUStack在维护时将worker state显示为maintenance，解除后才恢复ready。本轮已按这个实际状态处理，控制调用超时后先核对已完成的状态，未盲目重复变更。

## 当前保留与限制

- 旧worker容器maas-gpustack-worker-before-storage-v1-20261026保持停止、restart=no，仅供回退，不能与新worker同时启动；root-only receipt只认可停止且同一原生身份卷的受管理容器。此receipt、原生身份及维护日志不得进入母版。
- 本轮未向母版安装本版本，实际状态待恢复后复核。公开源码包dist/e5/e5-worker-template-model-storage-v1-e202112.tar.gz已构建并核验16个公开载荷；它不是Ubuntu镜像，不含注册码/token/私钥或GPUStack容器镜像，需母版恢复后安装，再由管理员保存快照。
- 目录隔离仅减少不必要共享，不是privileged容器的强安全边界；可写缓存没有伪装成只读。HF/ModelScope真实下载、NFS、双克隆并行及千节点分发未验收。
- 旧通用维护binding仍引用worker2/model1、9，未重绑；本轮仅使用显式worker5的专用迁移流程，不是通用E1逐worker排空功能。
- 真实回退未演练，回退材料已保留；不在001/002做VM重启或间接重启验证。旧凭据清理仍按用户决定暂缓。

证据：[克隆安装](clone-installed.json) · [容器替换与身份保持](replacement.json) · [挂载/读写证明](mount-proof.json) · [MaaS请求/计量](acceptance.json) · [新目标](target.json) · [版本登记](pin.json) · [最终状态](final-status.json) · [交付包](bundle.json)。

部署与回退流程见[操作手册](../../deploy/maas/MODEL-STORAGE-V1.md)。
