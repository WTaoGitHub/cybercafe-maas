# 第二次冷启动补验：worker 7

2026-11-13，用户报告管理员停止原 002 并启动新 VPC，提供入口后仅执行只读取证。当前入口对应的主机名实际为 XZ-31-001，不是原 002；不能把复用的 SSH 入口当作 VM 身份。

17/17 文件匹配 b6ab211，timer 自动到期，E5 在开机 100.128105 秒启动，116.785577 秒首次尝试 APPLIED / Ready，注册为集群 1 worker 7。首次取证时机器运行时间已经为 508.65 秒，未执行手工触发、许可交付或配置修改。HTTPS me=true 返回 200，name、external_id、实际 GPU UUID 和上报清单一致，unreachable=false。IPv4/IPv6 边界链及钩子均存在。

当前 GPU 与原 002 此前上报的 GPU 不同，因此继续验证的是新快照冷启动、首次新卡自动上线，不是同卡接续。主机名 XZ-31-001 已允许普通 worker 注册，本次成功也补充验证了旧控制平面名称不再误拦。该名称不代表原受保护 VM 的平台实例身份；本轮未查询平台实例 ID，也没有执行 VM 或操作系统操作。

结构化证据见 [worker7.json](worker7.json)。原始 GPU UUID、boot ID、连接信息与凭据不公开。关联 [Issue #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)、[Issue #20](https://github.com/WTaoGitHub/cybercafe-maas/issues/20)。
