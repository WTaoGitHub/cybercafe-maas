# E5 系统就绪后启动：母版部署记录

2026-11-11，按用户授权在已核验的 `tower-*` 母版安装 [b6ab211](https://github.com/WTaoGitHub/cybercafe-maas/commit/b6ab211dc9c08b59a856558d5f019fa552cef877)。安装完成，真实新快照首启尚未验收。关联 [Issue #20](https://github.com/WTaoGitHub/cybercafe-maas/issues/20)，GPU 身份关联见 [Issue #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)。

## 已安装行为

- 一次性 timer 开机满 30 秒触发准备任务；任务排在 `multi-user.target` 与供应商命名单元之后，网络边界与 bootstrap 均不再成为系统基本启动、网络或 Docker 启动的前置条件。
- 检查母版保护、供应商命名完成、Docker、单张 GPU UUID 与 Bootstrap HTTPS 健康入口。准备任务在 600 秒总预算内按 10 秒间隔重试，超时记录原因并停止自动尝试。
- 就绪后先应用网络边界，再执行已有 E5 验签、自动许可及原生注册。边界失败不得继续启动 worker；子进程分别限制 45、150 秒且受剩余总预算约束。systemd 总超时 620 秒。
- 手工许可 `.path` 触发同一个一次性 timer，避免许可文件持续存在造成无限重启。人工恢复入口为 `systemctl restart maas-worker-bootstrap.service`。
- 本次同时部署先前移除 001 名称及旧地址注册阻断的代码。母版保护保留，001、002 禁止整机重启的约束继续有效。

## 部署与核验

原程序、私有配置与启用关系已备份到操作机的受保护目录，未将备份或凭据写入公开仓库或母版持久目录。原子替换 17 个文件，逐一核对源码摘要、root:root 归属与文件权限，并更新母版安装清单。私有连接信息与原始日志未公开。

| 单元 | 开机启用状态 | 安装后运行状态 |
| --- | --- | --- |
| `maas-worker-bootstrap.timer` | enabled | inactive，未使用 `--now` |
| `maas-worker-bootstrap.service` | static | inactive |
| `maas-clone-boundary.service` | static | inactive |
| `maas-worker-bootstrap.path` | disabled，当前使用自动许可 | inactive |

移除旧 bootstrap/boundary 的 `multi-user.target.wants` 链接，更新 drop-in 后已无旧 `Requires` 和 `ExecStartPre` 阻断链。实际 `systemctl show` 确认服务排在 `multi-user.target` 后，目标本身不依赖这两个服务。staging 与安装后的 `systemd-analyze verify` 均返回 0；供应商既有 `start_appinstaller.service` 文件可执行权限告警保持原状。

验证结果：

- 本地 Bootstrap 测试 141 项通过，覆盖延迟命名后继续执行、依赖未就绪、边界失败禁止注册、总预算超时、互斥、重试及镜像启用关系。
- 公开源码交付包按提交构建，26 个成员摘要验证通过；部署只安装所需客户端与单元，不覆盖私有配置。
- 母版 `boot-check` 返回 `PROTECTED_HOST_WORKER_FORBIDDEN`，边界 `plan` 同样拒绝执行。在独立 `/run` 测试目录内，以缩短预算调用实际准备任务，验证持续母版保护会有限等待并返回超时，全程没有身份、许可或容器生成。此检查不是克隆首启测试。
- 从母版只读取回新 PC 的签名发布并验签，目标集群为 `1`，命名策略为 `gpu-external-id`，边界策略允许的控制端与发布中的原生 server 一致。
- 安装前后私有配置文件摘要、IPv4/IPv6 防火墙规则（忽略计数器）、Docker 主进程及启动时间、本次系统 boot ID 均保持一致。母版仍无容器和原生身份。

结构化证据：[核验结果](verification.json)、[安装文件摘要](installed-files.json)。

## 待完成的真实启动验收

由管理员保存包含本轮文件和启用关系的新快照，再启动另行获准的独立克隆。核对供应商命名、系统基本启动与准备任务的先后关系，确认无人工重试即可完成 GPU UUID 命名注册，并检查 `name`、`external_id`、实测 GPU UUID 和集群 ID。接续场景继续要求已有唯一记录为 `not_ready`，保留原 worker ID。

本轮没有创建或重启 VM，没有启动母版 worker，未升级现有在线克隆，也未执行模型服务验收。母版安装与静态依赖校验不能代替新快照首启证据。
