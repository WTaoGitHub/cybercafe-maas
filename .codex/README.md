# 项目级 SSH MCP

本目录仅为cybercafe-maas的受信任项目会话配置SSH MCP，不写入用户全局config.toml。项目级MCP方式见[官方说明](https://developers.openai.com/codex/mcp/)。

- `config.toml`：启动固定版本`@fangjunjie/ssh-mcp-server@1.10.0`，stdio、按需连接。
- `ssh-servers.json`：XZ-31-001、XZ-31-002、tower-mNST8iPdv三台连接；只保存本机私钥路径，不包含私钥内容或密码。
- `ssh-config.proposed.json`与`codex-mcp.proposed.toml`为仓库根目录的同步参考副本；实际读取本目录的两个配置文件。

新增连接：root@111.4.255.126:6025，名称tower-mNST8iPdv。沿用原两台策略：exec、无代理、无白名单/命令模板、同一黑名单，上传下载允许本地`/Users/4paradigm/dev/cybercafe/wtaobj`与远端`/root/wtaobj`。这些是用户指定沿用的传输路径，不自动跟随仓库改名。

配置含当前Mac绝对路径；其他电脑需调整配置和私钥路径。全局配置未更改。重新加载MCP或重新打开本项目会话后使用`list-servers`；现有会话可能需要重新加载工具。项目级作用域不是对其他本地程序的权限隔离。

XZ-31-001、XZ-31-002永久禁止VM重启及等效操作，详见根目录AGENTS.md；此处没有连接/重启远端VM的启动操作。

验证记录（2026-10-22）：Codex CLI可读取`cybercafe_ssh`项目配置；通过独立stdio MCP握手调用`list-servers`，三台记录均存在；通过`execute-command`在tower-mNST8iPdv运行`whoami`返回root。未在001/002执行修改或重启动作。

本机默认npm源为不可达内网地址，导致首次npx MCP握手超时。因此仅在该MCP的环境变量中设置`NPM_CONFIG_REGISTRY=https://registry.npmjs.org`，不改用户全局npm配置。包版本固定1.10.0。全局Codex配置哈希核对未变化。
