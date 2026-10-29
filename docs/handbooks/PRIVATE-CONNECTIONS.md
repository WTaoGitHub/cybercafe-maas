# 私有连接信息管理

真实VPC公网地址、SSH映射端口、登录账号、个人密钥路径和连接日志不写入仓库、报告或Issue。发布文件里的`192.0.2.10`为文档保留地址，`SSH_PORT_REDACTED`为历史入口脱敏标记，均不能用于实际连接。局域网架构/服务端口属于既有部署策略，本次不改变服务器网络或运行配置。

## 本机文件

| 文件 | 用途 |
|---|---|
| `.codex/config.toml`、`.codex/ssh-servers.json` | 当前项目MCP真实连接配置；本机保留、Git忽略 |
| `.maas/ssh/config` | 早期运维脚本使用的私有OpenSSH配置；默认别名`maas-controller`、`maas-legacy-worker`、`maas-template-legacy` |
| `.maas/ssh/controller-command.json` | 原有复用SSH通道的完整参数数组，仅供本机`connect_retry.py`使用 |
| `service-validation/*.log` | 本机诊断日志，可能含连接信息，不发布 |
| `.maas/backups/` | 脱敏前原始资料与配置私有备份 |

目录权限700，配置、原始日志及备份文件600。仓库里只保留`.codex/*.example.*`模板。真实值通过运维私有渠道交付，不在聊天、Issue或命令示例中填写。

## 手动连接与转发

优先在自己的私有SSH配置中填写Host/HostName/Port/User/IdentityFile，开启`StrictHostKeyChecking yes`。不要关闭主机身份校验，不要在未核实身份时更新known_hosts。

```sh
ssh -F .maas/ssh/config maas-controller
ssh -F .maas/ssh/config -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18832:127.0.0.1:18832 maas-controller
```

部分文档示例保留环境变量形式，运行前由运维在本机设置`MAAS_SSH_HOST`、`MAAS_SSH_PORT`、`MAAS_SSH_USER`和`MAAS_SSH_KEY`。这些不是API凭据，不应写入可提交脚本；未设置时不要执行示例。

早期脚本使用`MAAS_SSH_CONFIG`覆盖配置文件，`MAAS_LEGACY_WORKER_SSH_ALIAS`覆盖历史worker别名。它们仅用于经过核对和授权的旧实验，不代表旧worker仍在线，也不因脱敏而授权再次运行。当前MCP真实配置保留原样；私有SSH别名须在更换主机时单独核对。

复用通道脚本从`.maas/ssh/controller-command.json`读取原有命令，不在源码内保存入口。可用`MAAS_CONTROLLER_SSH_COMMAND`或`--command-file`指定其他私有JSON文件；文件内容为以`ssh`开头的参数数组，应沿用经过核对的socket及身份校验选项。只解析配置不会建立连接。

## 发布和证据边界

- 本轮将真实配置和连接清单停止Git跟踪，原文件留在本机；报告连接入口脱敏，测试结论不变。原始文件及SHA256清单备份在本机私有目录。
- 不能把修改后的报告字节当作原始文件；历史校验清单/固定提交仍用于原版本，脱敏副本不重新冒充原始取证。引用被移出仓库的私有清单时，改由运维本机查阅。
- `.gitignore`仅阻止今后普通添加，不能清除旧提交或阻止`git add -f`。
- **本轮不改写提交历史、不强制推送。旧提交仍可能保留连接信息，Issue里的旧提交引用也不能因当前文件脱敏而视为已清理。** 后续历史清理需独立备份、审查引用及协作影响，再安排受控改写。
- SSH认证私钥正文没有写入仓库；密钥文件路径与私钥正文不是一回事。本次不轮换凭据、不修改服务器或访问策略。
