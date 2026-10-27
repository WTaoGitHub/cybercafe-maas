# model-storage-v1新快照克隆验收 · 2026-10-27

## 已确认的启动结果

管理员已保存更新后的母版快照并创建独立克隆，公网SSH端口仍16482、hostname仍XZ-31-002、内网仍10.11.31.2。名称/地址复用不作为旧实例身份依据。当前新worker ID6、名称e5-ba71e774d9ca94c0138c01fa，与旧ID5和旧名称不同；GPU也不同，因此不能据此证明同一GPU UUID跨VM重绑定的稳定性。

在任何手动apply之前读取开机日志及applied.json：trigger=boot、APPLIED、cluster3/test1、e5-pilot-r1、布局model-storage-v1。server实际ready，worker容器restart count=0，三模块SHA与母版安装版本一致。确认本次自动接入成功，不是手动补救注册。原生v2.2.3保持，没有启动E8升级。证据见[startup.json](startup.json)。

## 模型镜像分发缺口

新克隆只缓存GPUStack worker镜像，没有OCR业务镜像。创建model17/route16（maas-ocr-e5-r3）后，原生拉取quay.io/cybercafe-maas-ocr:p2-r1返回404，说明该历史测试引用不是可用的远程镜像源。旧节点本地缓存曾掩盖此问题；不能将以前私仓探针成功等同于此业务镜像已建立持久分发机制。

暂停接单并排空（generation25），UNKNOWN=0；将新model17副本降0停止失败重试。使用001已缓存的精确镜像通过SSH流式docker save/docker load交付新克隆，没有启用旧临时registry、修改Docker配置、把业务镜像写回母版或重启VM/引擎。最初Mac中转过慢，显式停止该导出进程后改为001至新克隆LAN SSH传输；临时本地ssh-agent转发及已知主机密钥校验，没有写入新的authorized_keys或长期私钥。此为显式离线部署，不是原生自动分发验收。

## 验收进度

离线镜像交付后已通过验收：

- 镜像ID为sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5，与001缓存一致。
- 手动重复apply=UNCHANGED，worker ID仍6，容器ID与初次开机创建相同，boot ID保持；初次自动注册证据在手动apply前保存。
- model17/route16/instance101在新worker运行，MaaS目录切至ocr-e5-r3；旧model16副本降0，旧记录/历史保留。
- 实际模型容器只有两个共享挂载：models RO、model-cache RW。只读写入返回EROFS，缓存写入宿主机可见，worker身份文件和Docker socket不在推理容器中，worker自身身份仍存在。
- 私有TLS同步200/46行，异步任务SUCCEEDED/attempts1，结果下载200/46行/revision ocr-e5-r3，SQL成功记录1条、usage事件1条。MaaS恢复接单，无在途请求，UNKNOWN=0。

[挂载实测](mount-proof.json) · [重复应用](repeat-apply.json) · [OCR验收](acceptance.json) · [目标](target.json) · [pin](pin.json) · [最终状态](final-status.json) · [边界](known-boundaries.json)。

结论：新版快照的自动接入及挂载布局首启验证已完成，离线补齐业务镜像后的模型服务闭环通过；自动业务镜像分发尚未通过，不能将两者合并为“完全无人值守模型服务”。下一步优先落实稳定镜像仓库/worker只读拉取凭据与证书，再用无OCR镜像的新克隆实测冷拉取；不要求将12GB业务镜像固化母版。

## 边界

本轮未自行创建/回收/重启任何VM；管理员快照/创建操作来自用户确认。没有修改母版、驱动、供应商系统身份或旧凭据。旧worker记录/模型历史保留，未做全局清理。双克隆并行、同GPU复用、NFS/HF下载、千节点容量、正式registry生命周期仍待验证/实现。目录共享隔离不等于privileged容器强安全边界。
