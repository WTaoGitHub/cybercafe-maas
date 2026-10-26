# 原生私有仓库拉取验证 · 2026-10-26

结论：第一项通过。GPUStack2.2.3/runtime0.2.1在现有新worker上完成了真实认证冷拉、digest校验和仓库离线时的缓存复用。未在worker手动docker pull/load，未改其启动配置，未重启VM、Docker或原worker/OCR容器。

| 验证 | 结果 |
|---|---|
| HTTPS仓库 | 001的10.11.31.1:18443，局域网ACL；backend仅127.0.0.1:18500；自签证书验证开启 |
| 账号权限 | reader读取200，匿名/错误密码401，reader写入401；publisher通过TLS成功发布 |
| 初始缓存 | worker既没有canonical digest引用，也没有对应source image/config ID |
| 原生错误凭据 | model13/instance95，GPUStack报授权拒绝，镜像仍未进入缓存 |
| 原生正确凭据 | model14/instance96，自动拉取后running，/health=ok；仓库日志记录worker的manifest及layer请求200 |
| digest与内容 | registry manifest digest=c4f10cd146e8342232fa37140b223f4817516ca139c40cbf7ae1e04f57fbe190；导入配置ID匹配source e9f98bfa496f27ae5ca6ee59bfb73efc63f427c14c5f937a3146c1377cd6846c |
| 缓存离线对照 | 停止测试仓库TLS入口，worker连接被拒绝；新model15/instance97仍running、/health=ok，证明IfNotPresent缓存复用 |
| 无重启信任接入 | 在001及新worker仅新增/etc/docker/certs.d/10.11.31.1:18443/ca.crt，两端Docker PID及boot均未变；当前Docker28.2.2有效 |
| 原服务保持 | 原worker/OCR/pause的容器ID、启动时间和restart count均未变；MaaS同步OCR仍200、46行；worker/model12 ready |
| 清理 | model13/14/15、backend21及其探针容器已删除；测试reader/publisher撤销后均401；测试仓库两容器已停止且restart=no，18443/18500无监听 |

## 证据

- [仓库发布与权限](setup.json)、[初始worker缓存与容器](worker-baseline.json)
- [原生三项探针](probe.json)、[冷拉内容校验](cold-image.json)、[仓库访问日志](registry-access.log)
- [原服务保持](worker-final.json)、[账号撤销及OCR请求](post-check.json)、[最终状态](final.json)

测试镜像是已维护的CPU探针，约125MB；服务由GPUStack调度启动，/health校验对应fixture版本。它不是OCR质量、吞吐或大量节点同时拉取测试。registry manifest digest与本地image/config ID分别核验，不混用。

## 范围与边界

1. 本次仅验证基础机制。测试只读凭据通过三个临时probe模型env传给原生runtime，未写入母版或worker启动参数；模型已删除、账号已撤销。正式凭据从E5模块化、安全地交给worker尚未实现，不能把临时模型env方案直接作为正式设计。
2. IfNotPresent在缓存命中时不再访问仓库；仓库账号撤销不等于删除已缓存镜像或停止运行服务。错误凭据用例专门在缓存未命中时执行。
3. 本次没有解决原生身份卷被mirrored继承、独立模型目录持久化/NFS、自动模型文件分发、新快照或新克隆首启；这些留给下一项。
4. Registry只作隔离试点：基础设施、数据和镜像保留，服务已停止；新加的精确仓库信任文件保留。正式仓库的生命周期、访问账号及证书配置需另行确定，不直接把此测试实例认定为生产服务。
5. 私有账号、服务端私钥及publisher Docker配置仅在001私有目录；账号/密钥等回退材料已优先备份Mac .maas/backups/registry-pilot-20261026，未入Git。

下一项：在沿用GPUStack mirrored机制的前提下，设计独立模型目录挂载、排除worker身份/认证目录，并验证新布局。先完成设计与最小验证，再更新母版。
