# P3 私有统一入口与网络旁路边界

2026-10-20 · [本轮报告](../../results/maas-p3-boundary/REPORT.html) · [维护手册](P3-MAINTENANCE.md)。

最新更新：按用户要求，两节点H3容器与对应Cloudflare隧道均已停止并关闭Docker自动重启，镜像、容器和模型卷保留；网络库存已刷新，OCR复核通过。见 [停用记录](../../results/h3-parked/README.md)。下文关于旧H3公网通道的描述是停用前背景，恢复该服务时仍需重新纳入维护边界。

后续W6进程级维护已通过：GPUStack maintenance、全局副本停止、002 worker/GPU进程停止及真实探针恢复已验收。见 [节点维护手册](P3-NODE-MAINTENANCE.md)。实际VM关机/系统切换、规则持久化与公开发布仍未验收。

## 当前部署

- 001已启用 `maas-edge.service`，使用本机缓存的Nginx镜像内容ID固定版本，非root、只读文件系统、无额外capability；配置源为 `deploy/maas/edge.nginx.conf`。
- 仅监听 `127.0.0.1:18831` 与 `[::1]:18831`。允许任务/结果/能力路径和受控非流式chat入口；管理、Playground、文档和其他路径404。禁止查询串、非GET/POST方法；清除客户端身份旁路头和Cookie；关闭Nginx上游重试。
- 任务API/执行器仍未常驻发布。本轮使用独立临时API与数据库验收，收尾后入口保留；后端未运行时允许路径返回502，不代表可对外售卖。公网TLS和常驻任务层属于P4。
- 两节点已应用并确认运行期防火墙策略，保护宿主机端口、Docker DNAT和直接容器地址；不修改全局默认策略，不清空Docker规则，不创建额外worker/daemon。

## 网络范围与信任

保护TCP端口：8820、10150–10151、10161、11435、18080、18830、40000–40063、41000–41999。模型范围依据固定v2.2.3和本轮运行配置；改变服务端口范围时必须重新评估策略。

仅对这些端口生效，不接管整台主机的所有网络服务。SSH22保持原状；IPv4允许回环、指定物理网卡上的001/002地址，以及001的GPUStack服务容器从docker0访问后端；IPv6只允许回环，其他来源访问保护端口被拒绝。Docker转发匹配原始宿主机端口及容器目标IP，避免只写INPUT却漏掉DNAT。

这是两台可信VM的网络边界，不是对恶意局域网IP伪造、拥有宿主机/root权限的用户或可信节点内任意进程的安全隔离。GPUStack管理与Playground可通过SSH隧道作为运维访问；所有这类运维直连仍可绕过任务门禁，因此维护时仍须核对运维请求和已有连接。

**现有Cloudflare隧道仍指向两节点的127.0.0.1:11435旧H3服务，未修改或停止。** 这种本机代理流量属于回环，网络规则不能识别它背后的公网客户。H3不经过当前MaaS门禁，整机维护前必须另行停止或纳入维护流程。因此本轮仅完成MaaS/GPUStack指定端口的网络客户端隔离，不能宣称整机所有推理入口统一，更不能据此判定可关机。

## 检查与回退

在对应节点运行：

```sh
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/boundary.py status
```

应同时看到active、confirmed、rules_ok、inventory_matches为true。库存变化（例如服务容器IP变化）会报告不匹配，不会自动改写信任名单。

应用前检查原策略已回退、没有同名链，随后：

```sh
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/boundary.py apply
# 180秒内验证SSH、两worker/collector、真实OCR及隔离客户端探针。
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/boundary.py confirm
```

apply先设置180秒恢复定时器；失败时回退。confirm只在规则与容器库存一致时撤销定时器，它不代替端到端实测。回退只删除本工具登记的hook和三个自有链：

```sh
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/boundary.py restore
```

规则目前为**运行期策略**，尚未实现启动前恢复、Docker重启后的自动重建或持续漂移封锁。重启宿主机、重置防火墙或重建相关容器后，不得沿用旧的验收结论；先暂停任务，再核对/重新应用策略。持久化和启动窗口验证必须在P4对外发布前补齐。

私有Nginx检查与回退：

```sh
systemctl is-active maas-edge.service
systemctl is-enabled maas-edge.service
# 停止私有入口：
systemctl disable --now maas-edge.service
```

源码安装器 `scripts/maas/runtime/install_edge.py` 仅用于001首次安装，遇到已有容器/配置会拒绝覆盖。它先用固定内容ID的镜像执行nginx -t，再启动systemd服务并验证入口；安装失败恢复新增文件。不使用镜像自动更新。

## 验收与下一步

独立临时network namespace/veth实测IPv4模型端口、Docker映射和IPv6监听的前后变化；没有修改docker0。直接容器地址在应用前已被Docker隔离，记录为前后均不可达，不把它冒充新增策略效果。测试namespace、veth和临时IPv6监听均已移除。

通过Nginx完整验证同步OCR、异步任务、暂停503、排队、历史结果下载和真实探针恢复。下一步W6重点是GPUStack maintenance/副本控制、阻止推理重新拉起、处理仍可能执行的独立服务，并演练完整节点停止与恢复。W6仍未整体验收，不新增自动关机能力。
