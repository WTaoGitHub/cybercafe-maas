# 引擎镜像自动拉取第一步验收 · 2026-10-27

结论：在当前克隆完成模块安装和显式worker容器维护后，GPUStack已用自动取得的endpoint专用只读凭据，通过TLS从常驻LAN仓库拉取OCR新版本，按稳定原生标签调度，恢复MaaS业务。母版未更新，新快照首启留待下一步。

## 验收

| 项目 | 实际结果 |
|---|---|
| 配置 | e5-registry-r1、registry-access-v1，证书/账号载荷摘要由Ed25519 manifest绑定，本地endpoint白名单 |
| 原生身份 | worker ID6及name/UUID/token/JWT保持；重复apply=UNCHANGED，boot及Docker PID未变；旧容器停止保留 |
| SDK认证 | 独立RO Docker config，解析到本仓库reader，quay.io无该认证，无全局registry密码环境；publisher不下发 |
| 首次拉取条件 | 新manifest引用、新config ID都不存在；旧OCR基础镜像缓存存在，不能称全盘空缓存 |
| 原生下载 | 10.11.31.2访问manifest/config/layers返回200；GPUStack自行拉取，不在worker手工pull/load |
| 镜像一致 | manifest 9ec6c93f…、image/config b46b3486…，分别核对，不混用 |
| 调度 | test1/cluster3、selector os=linux/arch=amd64，model18/route17/instance102，replicas=1，不绑定随机worker名 |
| 隔离 | 真实模型挂载仅models RO/cache RW；写模型EROFS、缓存写入可见；身份、socket及registry认证文件不共享 |
| 请求/计量 | 私有TLS同步200/46行，异步SUCCEEDED/attempts1，下载200/46行/ocr-registry-r1；SQL成功1条/usage事件1条 |
| 收尾 | MaaS已恢复，UNKNOWN=0，worker/model ready，仓库active，母版及VM未操作 |

DOCKER_CONFIG公开路径字符串被native mirrored继承，但该路径不存在、没有认证文件或密码，GPUSTACK_TOKEN环境也不存在；这与“秘密文件继承”不同。目录隔离不意味着privileged强沙箱。

## 证据

[配置发布](published.json) · [安装/身份保持](installed.json) · [SDK认证](sdk-auth.json) · [首次拉取条件](cold-baseline.json) · [仓库请求日志](registry-access.log) · [镜像一致](image.json) · [挂载](mount-proof.json) · [OCR/计量](acceptance.json) · [目标](target.json) · [版本](pin.json) · [收尾](final.json)。

本地bootstrap共67项通过（含10项registry验证）；运维脚本实际运行验收，源码及公开证据无真实口令。私有回退材料优先备份Mac。

## 未完成与下一步

新母版/快照首启尚未验证。活跃配置仅支持tower-e5-registry-v1，旧母版image_id仍tower-e5-pilot-v1，会得到409；需要下一阶段明确更新母版后再创建克隆。当前可运行不等于旧快照自动获得新能力。

没有凭据热轮换、仓库HA/生产RPO、权重拆分/NFS/HF、大规模下载风暴或E8升级；单副本不会随worker数自动扩容。下一步母版升级、管理员保存快照、独立无OCR缓存克隆验收，详见[部署/回退手册](../../deploy/maas/REGISTRY-ACCESS-V1.md)。
