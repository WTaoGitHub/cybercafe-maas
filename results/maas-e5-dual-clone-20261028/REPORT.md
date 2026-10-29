# 第二独立克隆与14副本自动扩容验收

报告日期：2026-10-28（Asia/Shanghai，以用户客户端日期为准）。用户提供新SSH端口SSH_PORT_REDACTED并要求验证。本轮通过的是E5双克隆同时在线的正常自动接入/扩容及真实业务，不是完整异常矩阵、E1或E3验收。

## 结论

**新克隆自动成为test1的worker8，原7个pending自行调度并启动。14副本全部running，worker7/worker8各7个，两个独立GPU都实际通过MaaS网关返回OCR结果。** 首次控制面只读取证即已14 running，未由验证脚本安装、注册或触发调度。

| 项目 | 原克隆 | 新克隆 |
|---|---|---|
| SSH端口 | SSH_PORT_REDACTED | SSH_PORT_REDACTED |
| hostname / LAN | XZ-31-002 / 10.11.31.2 | XZ-31-003 / 10.11.31.3 |
| GPUStack worker | 7 / e5-4672ade0506987f34555caed | 8 / e5-e98c7888ed68fd705b57d535 |
| 原生状态 | ready，unreachable=false | ready，unreachable=false |
| GPU | RTX4080SUPER，580.95.05 | RTX4080SUPER，580.95.05 |
| OCR实例 | 103–109，共7个 | 110–116，共7个 |
| 自动配置 | boot / APPLIED / e5-registry-r1 | boot / APPLIED / e5-registry-r1 |
| 布局 | registry-access-v1 | registry-access-v1 |
| worker容器restart count | 0 | 0 |

## 启动、身份、镜像及隔离证据

- 两节点boot ID、原生worker UUID摘要、worker name均不同；批准的4模块源码摘要及registry公共证书摘要相同。原生身份各自在本VM保存，不是GPU UUID作为身份凭据。不是供应商machine-id/SSH主机密钥泛化的证明。
- 新节点的systemd bootstrap为success/active(exited)，本次boot journal存在APPLIED事件；APPLIED里的native_state=not_ready是刚注册时记录，最终独立用GPUStack ready/running及真实请求核实。
- 新节点7个原生serve日志均有固定registry digest的Pulling/Pulled记录，当前镜像ID/RepoDigest与既有不可变发布一致。本次第一次连接时拉取已完成，**没有直接捕获拉取前镜像不存在的快照**；不把当前缓存存在误写成完整的冷缓存前后对照。此前worker7的无OCR缓存实测仍独立保留。
- registry只读认证由boot生成，endpoint仅10.11.31.1:18443，认证目录700/文件600。未输出认证内容，未交付publisher权限。
- 两节点14个推理容器都只具有models RO及model-cache RW挂载，无原生身份/socket/registry认证目录挂载或GPUStack token环境项。本轮只读检查挂载元数据，没有重复执行模型目录写入/缓存写入实验。
- 新端口ED25519主机公钥与已有受信任克隆/控制端记录之一匹配后加入known_hosts，SSH保持StrictHostKeyChecking。共享主机密钥是既有供应商快照事实，不等于证明强设备身份。

## 真实请求及计量

固定model18/route17、ocr.default、ocr-registry-r1不变。通过001私有TLS、pilot-ocr受限身份，串行发送独立有意探针；最多14次用于覆盖两GPU，不是失败重试。本例3次即覆盖：

| 请求 | 实际执行worker | 结果 |
|---|---:|---|
| 同步Chat探针1 | 8（新节点） | HTTP200，46行 |
| 同步Chat探针2 | 8（新节点） | HTTP200，46行 |
| 同步Chat探针3 | 7（原节点） | HTTP200，46行 |
| 异步上传→提交→查询→下载 | 7 | 201→202→SUCCEEDED，attempts=1，结果HTTP200/46行 |
| READ ONLY按该异步任务ID核对 | — | task_usage_events恰好1条 |

多节点的同步/异步可能返回不同GPU，验证器不再使用旧单节点脚本“同步与异步node必须相同”的假设。未运行旧脚本，也未更改业务调度策略。

本轮新建1个异步测试输入/任务/结果及对应成功记录，3个同步成功请求；文件按既有TTL保留。没有进行独占项目全局同步计数差值断言，也没有测量最大吞吐或14并发。

收尾：两节点boot ID和worker容器ID相对本轮首次取证不变、restart count=0；14 running/0 pending，monitor ok=true、alerts=[]、UNKNOWN=0。没有手动apply/pull/load、改模型/副本/revision/额度/凭据、停止/恢复业务、重启VM/Docker/worker或修改下游配置。

## 待继续的事项

1. **按已确认顺序进入现拓扑维护与网络边界。** 两节点IPv4 filter表未发现项目MAAS链，INPUT=ACCEPT，worker代理/指标及推理端口为0.0.0.0监听。不是完整网络安全审计，未测试公网/第三方LAN可达性；不能据此认定已经隔离。需要独立落实和验收，不在本次自动修改规则。
2. 新拓扑下维护绑定/模型集合须审查与重绑，不能直接运行旧节点/旧模型的维护脚本；逐worker排空E1仍未部署。
3. 本轮未重复新节点manual apply、不做配置服务离线/错误凭据/轮换/启动响应丢失/资源不足等异常实验。正常双克隆通过不关闭所有E5增强和异常项。
4. MaaS全局并发2、每project1、单异步执行器不变；E3显存声明/副本/并发联合调优按后续顺序安排，2GiB claim未下调。
5. 独立权重/NFS、GPUStack2.3、千节点、8小时新耐久、生产HA/RPO等未由本轮验证。

[原节点启动/镜像](worker7-startup.json) · [新节点启动/镜像](worker8-startup.json) · [请求及计量](acceptance.json) · [控制面最终状态](final.json) · [原节点收尾](worker7-final.json) · [新节点收尾](worker8-final.json)。

复现工具：scripts/maas/inspect_clone_scaleout.py仅一次性只读取证；scripts/maas/runtime/verify_dual_worker_ocr.py有界真实请求且保存发送前检查点，无失败重放。不是后台collector，不能用作每次推理的门禁。
