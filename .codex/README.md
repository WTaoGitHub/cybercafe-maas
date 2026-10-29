# 项目级 SSH MCP（私有配置）

本目录只提交模板和说明。真实地址、SSH映射端口、登录账号、密钥路径和诊断日志保存在本机，不进入Git。

- `config.example.toml`：项目级MCP启动模板。复制为被Git忽略的`config.toml`，按本机路径修改。
- `ssh-servers.example.json`：连接结构模板。复制为被Git忽略的`ssh-servers.json`，由运维私下填写真实信息，权限设为600。
- 已配置本机保留原来的两个真实文件，移除Git跟踪不会删除文件或中断已有会话。
- 根目录旧参考连接文件也已停止跟踪；不要重新强制添加。

SSH MCP使用固定版本`@fangjunjie/ssh-mcp-server@1.10.0`，stdio、按需连接。传输目录、命令限制和代理策略由真实本机配置决定，模板不是已授权的生产连接。

详细操作见[私有连接配置](../docs/handbooks/PRIVATE-CONNECTIONS.md)。001/002禁止VM重启及等效操作，仍以AGENTS.md为准。
