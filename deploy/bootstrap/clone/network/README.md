# 克隆网络隔离 overlay

2026-11-11 启动顺序调整。网络边界服务在网络和 Docker 就绪后执行，E5 仍通过 `Requires`、`After` 及 `ExecStartPre` 在注册前核对边界。边界单元启动超时为 45 秒，停止超时为 10 秒。失败会阻止本轮 E5 注册。此顺序需要部署到母版并由管理员保存新快照后生效。

克隆需保持镜像内无自动启动的旧 worker 或模型容器，才能确保边界安装完成前没有这些服务监听。此调整覆盖 E5 管理的启动流程。其他业务的开机启动和 Docker 重启后的边界状态仍需分别核对。

2026-10-28已在worker7/8部署并验证；随后通过SSH_PORT_REDACTED在母版tower-1lpDmdmo6完成文件安装和开机enable。**尚未由管理员保存新快照，也未验证新克隆首启继承规则。** 详见[实际部署](../../../../results/maas-e5-template-network-20261028/REPORT.md)。

源程序为仓库`maas_bootstrap/clone_boundary.py`，目标`/opt/maas/ops/clone_boundary.py`。依赖Python3、iptables/ip6tables及raw/multiport/addrtype模块；IPv6规则安装失败时不得认定部署成功。示例JSON在母版不固定hostname；按实际LAN接口、控制机IPv4和Docker桥网段审阅后安装为`/etc/maas/clone-boundary.json`，root:root/600。

unit和dropin分别安装到`/etc/systemd/system/maas-clone-boundary.service`、`/etc/systemd/system/maas-worker-bootstrap.service.d/20-clone-boundary.conf`，root:root/644，daemon-reload并enable boundary。**在母版只准备文件和enable，不运行apply，不启动worker；不重启现有VM或Docker。**独立克隆启动后执行规则，并在bootstrap执行前再次核对/应用。实际跨启动顺序和不同内核、接口/桥网段仍需管理员另建独立克隆验收。

本包提供文件交付材料，不含自动安装器；母版本轮按核对及备份后的受控安装流程更新。集成时须核对原有systemd依赖和现有规则，先在获准独立克隆使用guarded-apply及180秒回退，再验证001→worker通畅、其他克隆→worker受阻、SSH与配置/仓库出站正常，最后confirm。不要在保护窗口外盲目重复guarded-apply。

仅允许配置的控制端从指定物理接口访问受保护TCP/UDP端口；保留loopback，IPv6没有额外外部放行。SSH22、其他业务端口和OUTPUT不修改。规则覆盖pre-DNAT、INPUT与存在时的DOCKER-USER；只管理三个MAAS-CLONE链，不清空全局规则。当前端口范围匹配v2.2.3部署，未来模型端口、overlay/IPv6容器网络或新bridge须重新核验。

**不要打包**当前节点的`/etc/maas/maintenance-node.json`、maintenance SSH私钥、会话/回退journal或带hostname的当前节点策略。维护绑定是当前VM身份的运维材料，不能从母版复制给新克隆。新克隆上线后另做身份核验和维护重绑定。

## 静态地址策略与后续迁移

2026-10-28用户决定：当前母版先预置配置服务器`https://10.11.31.1:18840`及控制端允许IP`10.11.31.1`（示例JSON的controllers列表），原生GPUStack仍为`http://10.11.31.1:18080`。地址来自独立配置文件，不写死在Python程序中；不在本轮实现配置服务器动态下发防火墙策略或自动发现。

001为临时测试VPC。后续下线/替换前按[E5.F1迁移增强](../../../../docs/framework/ENHANCEMENTS.md)清点母版/在线克隆的地址、证书和允许列表，完成新端验证及回退安排后再下线旧端；不能先关闭001再期望克隆自动发现替代机。该增强低优先级暂缓，在实际迁移前处理。本轮已按此静态策略安装母版文件，后续迁移能力仍未实现。

## 2026-10-28部署进度

用户已授权母版安装；当前SSH_PORT_REDACTED连接超时，用户要求等待恢复。公开包已准备并完成9项规则测试及归档校验，**尚未安装母版**。待恢复后先核对真实母版和网络环境、备份到Mac，再安装文件及enable（不带--now），不在母版应用规则或启动worker。[准备记录](../../../../results/maas-e5-template-network-20261028/REPORT.md)。

## SSH_PORT_REDACTED母版安装完成（最新）

SSH_PORT_REDACTED超时为此前状态；用户提供SSH_PORT_REDACTED后，匹配已知SSH主机公钥，核对主机tower-1lpDmdmo6、无容器/实例身份，备份Mac后安装本包四文件，daemon-reload及enable完成，未start/apply。实际归档为`dist/e5/clone-network-v1-20261028-r2.tar.gz`，摘要及逐文件权限见[installed-package.json](../../../../results/maas-e5-template-network-20261028/installed-package.json)。旧enp3s0准备包不是最终安装版本。

母版网卡实测enp5s0、现有克隆enp3s0，因此最终策略使用iptables受限接口前缀`en+`，兼容两者；控制端仍精确限定10.11.31.1。程序只接受安全的末尾+，拒绝裸+、嵌入通配符和lo+。若未来硬件出现eth0等非en命名，新克隆可能被阻断，需先审查接口策略，不自动放宽来源。

16项规则测试/完整bootstrap91项通过；systemd-analyze verify通过，隔离network namespace实际IPv4/IPv6加载、重复apply及hook检查通过。宿主防火墙对比忽略生成时间和包/字节计数，仅比较实际规则和链策略；确认未变。当前boundary enabled/inactive，原bootstrap的failed历史状态保留，不当作新增故障，也不通过重启清除。新快照和独立新克隆首启、实际双向网络/模型访问仍待管理员安排。

## 验证安排调整（2026-10-28用户确认）

当前不单独要求管理员为本轮网络补丁保存快照或新建克隆；后续母版相关更新时合并验证。网络文件已安装，但新快照继承、首启顺序、自动注册、控制端/同伴网络边界和模型推理仍待验收，不标记为完成。若此前保存快照，备注“待验收”；新版镜像批量交付前须完成独立克隆验证。现有两worker继续服务，E3不受此等待阻塞。[部署记录](../../../../results/maas-e5-template-network-20261028/REPORT.md)。
