# NFS静态网段10.11.0.0/16实际应用

2026-11-17 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6) · [005原始首启与恢复](../maas-nfs-clone-20261117-005/REPORT.md)

## 当前决定和实际结果

用户明确继续使用静态名单，默认允许`10.11.x.x`，按CIDR解释为`10.11.0.0/16`。NFS服务端的export与2049/TCP规则已实际切换为相同网段，原001/005的/32条目已移除。005现有挂载读取、来源/ro/hard/TCP、测试文件摘要及原生worker Ready通过；001原worker仍Running，原模型根仍本地，NFS TCP仍可达。

此前提出的“以worker准入为唯一来源并自动同步NFS”暂缓，不作为本轮实施或验收前置条件。GPU UUID身份、自动预登记、发证和GPUStack接入策略均保持。NFS当前按静态源网段执行权限，网段内节点无需再逐IP申请；网段外来源不因worker已注册而自动获准，需另行明确授权。

## 已应用配置

| 层次 | 实际状态 |
| --- | --- |
| 文件导出 | `/root/maas/data/modelsWeights`只允许`10.11.0.0/16`，NFSv4根仍对应模型库 |
| 导出权限 | `ro,sync,fsid=0,subtree_check,root_squash,all_squash,anonuid=65534,anongid=65534`保持 |
| IPv4网络 | TCP2049进入`MAAS-MODEL-NFS`，该网段RETURN后继续遵守既有INPUT策略，其他来源REJECT |
| 其他协议 | IPv6的NFS TCP仍拒绝，2049/UDP仍丢弃，rpcbind设置不变 |
| 持久化 | exports文件和nfs-server网络drop-in均已写回、读回；启动前规则实际执行两次无叠加 |
| 客户端 | 005仍使用母版继承代码和签名配置，无补拷或配置修改，不重建/重启worker容器 |

模板默认网段已同步，服务端监听地址示例仍使用文档保留地址，不提交真实连接/凭据。此次只调整模型NFS，不增加其他目录、写权限或网络端口，也不以ACCEPT绕过已有INPUT策略。

## 应用顺序与验证

先将原export、drop-in、iptables和主机/服务基线备份到模型库外的私有操作目录，核对当前只有已知的两个/32。写入新export并reload；写入drop-in并daemon-reload，先插入网段RETURN并核验，再精确删除旧/32 RETURN，不清空链。整个过程保留旧客户端在新范围内的访问权限。

实际断言及读回通过：

- 源配置、活动etab、网络链和持久化drop-in均为相同的`10.11.0.0/16`；链只有一个网段RETURN及末尾显式TCP拒绝，无冗余逐IP授权。
- 同一drop-in实际执行两次，规范化iptables-save一致；其他防火墙规则、SSH和应用端口保持。
- 从服务端自身的网段外地址向同一NFS监听端口建立TCP连接，收到ECONNREFUSED，末尾REJECT计数增加；与005的实际成功读取分别证明拒绝和可用性，不将服务未启动当成访问控制证据。
- 005使用继承的storage-check显式读取，固定文件摘要匹配，保持一个autofs及一个真实NFS挂载；按原生`me=true`核验同GPU UUID/worker为Ready，容器ID与启动时间不变，17个客户端安装文件摘要仍匹配。
- 001原worker仍运行、正式模型根仍本地，NFS TCP连接成功；没有在001新挂载模型目录或改其引导配置。
- 服务端VM启动、Docker、NFS服务InvocationID/启动时间及既有服务容器均保持；未重启VM、NFS或Docker，无容器重建。

新增静态配置契约检查，约束export与防火墙网段一致、只读/squash和子树边界、显式TCP拒绝、无ACCEPT旁路及所有drop-in shell语法；与交付包及私有连接保护一同验证。实际检查摘要见[VALIDATION.json](VALIDATION.json)。

## 边界与继续验收

本次证明配置已实际生效及001/005现有运行时可用；未逐一探测整个/16的每个地址，网段内其他节点仍需具备真实路由及文件读取条件。未通过整机重启验证规则恢复，不重启001/002或还原快照；持久化文件安装与两次执行不冒充新VPC冷启动。

005此前首次启动失败的事实保留，不因本次扩网段改成首启成功。零干预完整首启、第二独立VPC、小批推广和存量维护仍按计划继续；真实权重性能、模型容器映射和推理不在本轮范围。此次仅服务端配置改变，不需要为了本次网段策略修改母版客户端。
