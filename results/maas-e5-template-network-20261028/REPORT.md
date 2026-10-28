# 母版网络隔离交付准备

2026-10-28用户授权将网络隔离规则写入tower母版。**当前状态：PREPARED_NOT_DEPLOYED。** MCP及本地OpenSSH访问111.4.255.126:6025均超时；用户回复“等待连接恢复”。未取得当前母版身份、配置或依赖检查结果，因此未上传、安装或修改母版，不把本地打包当作部署成功。

公开交付包已在Mac准备：`dist/e5/clone-network-v1-20261028.tar.gz`；SHA256及逐文件清单见[package.json](package.json)。归档含通用独立防火墙脚本、root600策略（controllers=[10.11.31.1]、当前示例接口enp3s0/桥172.17.0.0/16）、boundary unit和bootstrap dropin；不含节点hostname、维护身份、SSH私钥或注册码。配置服务器/原生服务地址保留现有母版静态值，本包不覆盖bootstrap.json。

9项规则测试通过；归档白名单、逐文件内容SHA及权限校验通过。尚未核对当前tower真实接口/桥、已有文件或systemd依赖；示例配置必须在连接恢复后据实确认，不能直接作为已验证母版配置。

## 连接恢复后的执行顺序

1. 只读核对hostname/tower角色、boot ID、现有E5静态地址、启动unit、网络接口/桥、iptables工具与内核能力；不得将已注册业务克隆误作母版。
2. 备份原文件、systemd启用状态及规则至Mac私有目录，保存权限和摘要；不能覆盖已有未知改动。
3. 校验交付包后安装通用文件，按实测接口调整独立策略，不复制worker身份/维护凭据。只做daemon-reload与enable，**不使用enable --now，不start/restart，不在母版运行apply/guarded-apply**。
4. 导入和规则渲染检查、systemd验证、source/权限对照；确认boot ID、Docker容器、现有规则及worker状态未因安装改变，不启动worker。不修改001/现有克隆，不重启任何VM或Docker。
5. 更新实际部署报告后交由管理员保存快照、创建独立普通克隆，验证首启注册、001可访问模型、同伴访问受阻、SSH和出站正常。未创建独立克隆前，不能声称跨启动及镜像继承验收通过。

实际母版部署及管理员快照仍待完成；E5.F1迁移优化继续暂缓，地址先静态预置。
