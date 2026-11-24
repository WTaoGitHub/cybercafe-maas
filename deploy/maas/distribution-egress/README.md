# NFS 权重与镜像分发的共享出口限速

规则部署在同时提供 NFS 和镜像仓库的 Linux 服务器上。每个出口一棵 HTB 队列树；所有 worker 从该出口取得的 NFS 和镜像数据共用一个桶。2026-11-24 现场配置为物理出口 2500 Mbit/s、分发上限 1500 Mbit/s。该配置独立于调节器的启动并行度。 [Issue #79](https://github.com/WTaoGitHub/cybercafe-maas/issues/79)

| 服务端发出的流量 | 分类规则 | 带宽 |
| --- | --- | --- |
| NFS 和镜像分发 | IPv4／IPv6 TCP 源端口 2049 或仓库专用前端端口，一起进入 `d150:10` | 两者合计最高 1500 Mbit/s |
| 普通业务，包括 MaaS 和 GPUStack | 其余流量进入默认队列 `d150:20` | 两类均繁忙时配置份额为 1000 Mbit/s，可借用空闲带宽至父类上限 |

这里控制的是该网卡的发送速率，令牌桶允许短时突发。配置份额是队列调度值，端到端吞吐还取决于链路开销、其他网络瓶颈及对端。实际部署前核对 worker 的回程接口、协商速率、仓库面向 worker 的专用监听端口及现有 `tc` 规则。如果镜像仓库与业务共用同一个 IP 和 TLS 端口，需要先提供可区分的分发入口。独立仓库主机和其他出口应在各自的发送路径上配置。

## 部署与查看

以下命令由管理员在分发服务器上以 root 执行，从本目录取安装文件。将 `DEV` 设置为实际物理出口，`REGISTRY_PORT` 设置为该出口上 worker 访问仓库的实际 TCP 端口。先保存既有规则；脚本仅在无显式句柄的内核默认 `fq_codel` 或 `noqueue` 根队列上安装。示例值适用于协商速率 2.5 Gb/s 的出口。

```sh
: "${DEV:?设置物理出口名}" "${REGISTRY_PORT:?设置仓库专用前端端口}"
cat "/sys/class/net/$DEV/speed"
tc -s qdisc show dev "$DEV"
tc -s class show dev "$DEV"
tc filter show dev "$DEV" root
install -D -m 0755 maas-distribution-egress /usr/local/libexec/maas-distribution-egress
install -D -m 0644 maas-distribution-egress@.service /etc/systemd/system/maas-distribution-egress@.service
mkdir -p /etc/maas
printf 'LINK_MBIT=2500\nDISTRIBUTION_MBIT=1500\nREGISTRY_PORT=%s\n' "$REGISTRY_PORT" > "/etc/maas/distribution-egress-$DEV.conf"
unit=$(systemd-escape --template=maas-distribution-egress@.service "$DEV")
systemctl daemon-reload
systemctl enable --now "$unit"
systemctl is-active "$unit"
tc class show dev "$DEV"
tc -s qdisc show dev "$DEV"
tc filter show dev "$DEV" parent d150:
```

从一台可访问这两个服务的局域网节点分别进行只读请求。分发队列的发送字节数应随两种请求增长，普通业务应进入默认队列。同机通过回环地址发出的请求无法验证物理出口分类。撤销规则执行 `systemctl disable --now "$unit"`，随后以 `tc qdisc show dev "$DEV"` 和预先保存的规则对照。停止命令仅删除本服务的 HTB 根句柄，内核恢复默认队列。

## 验证记录

- 在有 `iproute2`、Python 3、curl、Bash 和 GNU coreutils 的 Linux 主机以 root 运行 `./verify-isolated.sh`。使用两个临时网络命名空间验证端口分类、不覆盖其他显式根队列，以及两个 TCP 流共享上限。每个流传送 256 MiB 的稀疏测试文件。实测合计 TCP 有效载荷为 **1434.4 Mbit/s**。脚本退出时删除测试进程、命名空间和文件。
- 现场在仓库局域网前端确认其流量专用于仓库，后端监听回环。启用规则后，从局域网对端执行 5 次 NFSv4 NULL RPC 均成功，分发队列累计发送 1500 字节；随后 3 次仓库 HTTPS 请求均核对证书指纹并收到预期的 401 认证挑战，队列累计增至 19140 字节。同期普通队列有独立计数，MaaS 管理接口返回 200，GPUStack 版本查询成功，调节器继续 Apply。
- systemd 单元已启用并运行。上述双流测速来自隔离环境，现场验证了真实流量分类和服务可达。完整冷启动下的 API P95、错误率、worker 心跳与模型 Ready 耗时应在下一轮使用相同负载比较。 [启动压力验收](../../../docs/framework/REPLICA-STARTUP-OBSERVATION.md)
