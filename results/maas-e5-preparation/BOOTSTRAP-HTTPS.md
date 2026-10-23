# E5配置服务端口、监听与访问边界实施

2026-10-23。用户授权按18840及现有网段实施，随后确认现阶段按10.11.31.0/24测试、预留多个CIDR；tower到GPUStack18080不通不作阻塞，未来以新克隆为准。

## 结果

| 检查 | 结果 |
|---|---|
| 专用入口 | https://10.11.31.1:18840，IPv4 LAN单地址监听 |
| 服务 | maas-bootstrap-config与boundary均enabled/active，无DB/VM会话 |
| 来源限制 | lo允许；LAN enp3s0来源10.11.31.0/24允许；其余DROP；代码/策略支持CIDR列表 |
| 自签名HTTPS | SAN=10.11.31.1，notAfter=9999-12-31T23:59:59Z，宿主/002/tower验证成功 |
| 授权配置 | 持共享码读取200，匿名/错误码401，错误image_id409；no-store；Ed25519 manifest签名核对成功 |
| 正常LAN客户端 | 002和tower均health200、授权200、匿名401，读取同e5-pilot-r1/test1(ID3) |
| 错误TLS | 不信任证书及错误访问名均被标准校验拒绝，未跳验证 |
| 非允许来源 | 临时198.18.224.2网ns连接18840失败，专用DROP计数+2；测试namespace/veth清理，原raw规则字节不变 |
| 实际运行镜像回归 | 8 passed，隔离无网络/无GPU/无Docker socket测试容器；仅第三方TestClient弃用警告 |
| 原有控制服务 | Server、MaaS API/executor/observer/DB的ID/启动时间与准备记录相同，health200、监控无告警/UNKNOWN=0 |
| 001旧worker/OCR | 仍停止、restart=no，未重新拉起 |
| 云备用 | http://configuration.cybercafe.phanlink.cn预留且disabled，无实现/部署/请求 |

初次新服务启动因上传umask导致源码父目录700、非root容器不能导入，修正仅源码目录权限后新服务成功。没有修改原Server/MaaS运行实例；未重启001002、Docker引擎或GPUStack worker。公开代码755/644；秘密与数据受限、容器只读挂载，签名私钥不进服务容器。

共享码在001的operator受限文件中生成，当前试点expires_at=null（未设自动到期），可以停码；并未在Git/聊天/服务日志显示。test1原生注册凭据从原生管理接口在001内存读取并受控保存，不使用cluster1注册凭据。日志检查未发现共享码或原生token。公开TLS证书及发布验证公钥随证据保存，均不含私钥。

## 验收边界和下一步

本轮完成配置读取API、18840监听及边界持久化配置和实机验证，**不等于E5全部完成**。boot/manual引导、通用发布CLI、image-prep、母版快照、新克隆自动注册/容器GPU及旧维护工具适配尚未完成。

test1仍用于无业务模型实验，本轮没有新建worker或部署模型。新克隆对18080及Server到worker原生路径要另行验证；tower的18080失败忽略为阻塞项，现有原生规则未扩大。配置API的可用性也不能证明原生注册路径已通。

systemd单元及专用规则的持久化入口已安装，未进行VM重启验证，不把配置存在宣称跨重启实验通过。多CIDR策略/生成器测试通过不代表1000节点容量或真实网络地址已规划；线上仅/24。

证据：[宿主核验](bootstrap-verification.json) · [LAN客户端](bootstrap-lan-clients.json) · [非LAN探测](bootstrap-boundary-probe.json) · [最终状态](bootstrap-deployment-final.json) · [实际镜像测试](bootstrap-runtime-tests.log)。操作与文件说明见 [部署手册](../../deploy/bootstrap/README.md)。
