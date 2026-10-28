# 克隆网络隔离 overlay

2026-10-28已在当前worker7/8部署并验证。这里提供后续母版集成材料；**未更新tower、未保存新快照，不能认为将来克隆已自带本规则**。

源程序为仓库`maas_bootstrap/clone_boundary.py`，目标`/opt/maas/ops/clone_boundary.py`。依赖Python3、iptables/ip6tables及raw/multiport/addrtype模块；IPv6规则安装失败时不得认定部署成功。示例JSON在母版不固定hostname；按实际LAN接口、控制机IPv4和Docker桥网段审阅后安装为`/etc/maas/clone-boundary.json`，root:root/600。

unit和dropin分别安装到`/etc/systemd/system/maas-clone-boundary.service`、`/etc/systemd/system/maas-worker-bootstrap.service.d/20-clone-boundary.conf`，root:root/644，daemon-reload并enable boundary。**在母版只准备文件和enable，不运行apply，不启动worker；不重启现有VM或Docker。**独立克隆启动后执行规则，并在bootstrap执行前再次核对/应用。实际跨启动顺序和不同内核、接口/桥网段仍需管理员另建独立克隆验收。

本包不含安装器，不自动改现有镜像。集成时须核对原有systemd依赖和现有规则，先在获准独立克隆使用guarded-apply及180秒回退，再验证001→worker通畅、其他克隆→worker受阻、SSH与配置/仓库出站正常，最后confirm。不要在保护窗口外盲目重复guarded-apply。

仅允许配置的控制端从指定物理接口访问受保护TCP/UDP端口；保留loopback，IPv6没有额外外部放行。SSH22、其他业务端口和OUTPUT不修改。规则覆盖pre-DNAT、INPUT与存在时的DOCKER-USER；只管理三个MAAS-CLONE链，不清空全局规则。当前端口范围匹配v2.2.3部署，未来模型端口、overlay/IPv6容器网络或新bridge须重新核验。

**不要打包**当前节点的`/etc/maas/maintenance-node.json`、maintenance SSH私钥、会话/回退journal或带hostname的当前节点策略。维护绑定是当前VM身份的运维材料，不能从母版复制给新克隆。新克隆上线后另做身份核验和维护重绑定。
