# 母版网络隔离交付准备

> **最新状态：TEMPLATE_INSTALLED，待管理员新快照/独立克隆验证。** 用户随后提供SSH_PORT_REDACTED，实际母版tower-1lpDmdmo6已完成安装；下文“等待SSH_PORT_REDACTED”的准备记录保留为历史。最终包为-r2，不能使用最初绑定enp3s0的准备包冒充实际安装版本。

2026-10-28用户授权将网络隔离规则写入tower母版。**当前状态：PREPARED_NOT_DEPLOYED。** MCP及本地OpenSSH访问192.0.2.10:SSH_PORT_REDACTED均超时；用户回复“等待连接恢复”。未取得当前母版身份、配置或依赖检查结果，因此未上传、安装或修改母版，不把本地打包当作部署成功。

公开交付包已在Mac准备：`dist/e5/clone-network-v1-20261028.tar.gz`；SHA256及逐文件清单见[package.json](package.json)。归档含通用独立防火墙脚本、root600策略（controllers=[10.11.31.1]、当前示例接口enp3s0/桥172.17.0.0/16）、boundary unit和bootstrap dropin；不含节点hostname、维护身份、SSH私钥或注册码。配置服务器/原生服务地址保留现有母版静态值，本包不覆盖bootstrap.json。

9项规则测试通过；归档白名单、逐文件内容SHA及权限校验通过。尚未核对当前tower真实接口/桥、已有文件或systemd依赖；示例配置必须在连接恢复后据实确认，不能直接作为已验证母版配置。

## 连接恢复后的执行顺序

1. 只读核对hostname/tower角色、boot ID、现有E5静态地址、启动unit、网络接口/桥、iptables工具与内核能力；不得将已注册业务克隆误作母版。
2. 备份原文件、systemd启用状态及规则至Mac私有目录，保存权限和摘要；不能覆盖已有未知改动。
3. 校验交付包后安装通用文件，按实测接口调整独立策略，不复制worker身份/维护凭据。只做daemon-reload与enable，**不使用enable --now，不start/restart，不在母版运行apply/guarded-apply**。
4. 导入和规则渲染检查、systemd验证、source/权限对照；确认boot ID、Docker容器、现有规则及worker状态未因安装改变，不启动worker。不修改001/现有克隆，不重启任何VM或Docker。
5. 更新实际部署报告后交由管理员保存快照、创建独立普通克隆，验证首启注册、001可访问模型、同伴访问受阻、SSH和出站正常。未创建独立克隆前，不能声称跨启动及镜像继承验收通过。

实际母版部署及管理员快照仍待完成；E5.F1迁移优化继续暂缓，地址先静态预置。

## SSH_PORT_REDACTED实际部署完成

新端口ED25519公钥与此前可信SSH_PORT_REDACTED记录一致后加入本地known_hosts；主机身份/无容器/无实例身份及现有静态地址核对通过，变更前备份已保存Mac私有目录。四文件清单/权限及包摘要见installed-package.json，安装结果见installation.json。源码允许受限接口前缀，策略采用en+，兼容母版enp5s0与已验克隆enp3s0；controller仍精确为10.11.31.1，不放行整个网段。

systemd-analyze verify返回0；daemon-reload、enable后服务enabled但inactive，未在母版应用宿主防火墙，也未启动worker。隔离network namespace内实际加载IPv4/IPv6规则并重复执行，两INPUT协议hook无重复、DOCKER-USER hook存在；namespace退出后宿主规则结构保持。比较忽略iptables-save时间注释和自然变化的包/字节计数。16项规则测试及完整bootstrap91项通过（1项已有Starlette弃用警告）。

原bootstrap.json/worker模块/启动unit摘要不变，boot ID与Docker启动时间不变，无容器及实例/维护身份，启动保护仍拒绝母版注册。原bootstrap的failed历史状态保留，没有为清除状态重启任何服务或VM。没有改001/现有克隆，未改副本/并发、未实现E5.F1动态寻址、未清理旧凭据。

下一步由管理员保存**新快照**并启动独立普通克隆，提供SSH入口后验证继承、自动纳管、控制端/同伴端口边界及OCR。当前尚未保存快照或验证真实跨启动；新接口名若非en前缀或桥网段改变需重新核验。控制器/配置服务器迁移仍按静态地址策略后续处理。

证据：[tower-before.json](tower-before.json)、[installation.json](installation.json)、[tower-verification.json](tower-verification.json)、[installed-package.json](installed-package.json)。操作及撤销步骤见[手册第33节](../../deploy/bootstrap/clone/MANUAL-TOWER-DEPLOYMENT.md)。

## 后续验证安排（用户确认）

用户接受暂缓本轮独立克隆首启验证，待下一次母版相关更新后一并验收；不单独安排快照/新VM，也不阻塞E3。现有安装及测试证据保留，snapshot_created/new_clone_boot_verified仍为false。新版镜像批量交付前补验；若管理员此前保存快照，应标为待验收版本。本次无服务器变更。
