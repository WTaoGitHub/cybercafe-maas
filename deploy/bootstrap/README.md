# E5局域网配置读取服务（第一批实现）

2026-10-23，已部署001：`https://10.11.31.1:18840`。无VM登记、会话或E5数据库。只读API已实现，克隆boot/manual客户端与image-prep尚未实现，不能据此认定自动注册已通过。

## 运行与文件

- systemd：maas-bootstrap-boundary.service（独立INPUT链）、maas-bootstrap-config.service（配置API）。均enabled/active；持久化单元已安装，未重启VM验证，001002永久禁止VM重启。
- API复用当前MaaS运行镜像的不可变image ID，挂载独立maas_bootstrap代码；非root UID10001、只读rootfs、只读配置挂载，无Docker socket或GPU，资源/日志有界。没有替换或重启原MaaS容器。
- 服务目录：`/root/wtaobj/maas-bootstrap`。code为不含秘密的源码，private为只读挂载的policy/active/release/TLS文件，operator为root-only管理材料。
- `operator/shared-code`仅在001受限保存；当前试点码未设自动到期（expires_at=null），可通过policy停用。它与无限期TLS证书是两个独立策略，后续可显式设置到期。未在Git或聊天中保存秘密。
- `operator/server.crt`、`release-signing.pub`是公开分发材料；`release-signing.key`只留operator，TLS私钥只留private/tls。证书SAN=10.11.31.1，notAfter=9999-12-31T23:59:59Z，宿主和002/tower的标准Python HTTPS均验证通过。
- 已发布e5-pilot-r1，scope cybercafe-lab/e5-pilot，目标test1（ID3）；image_id tower-e5-pilot-v1只是预留交付标识，尚无该快照/客户端已完成的声明。配置含受保护的test1原生注册token，授权读者拥有其实际原生权限。
- `GET /healthz`返回最小状态；`GET /bootstrap/v1/config?image_id=tower-e5-pilot-v1`须共享码，返回no-store；历史revision端点当前只允许active revision。文档/匿名配置写接口禁用。云备份字段保留且disabled，不发请求。

## 网络范围

IPv4仅监听10.11.31.1:18840，本机经lo放行；LAN enp3s0仅放行10.11.31.0/24，其他来源DROP。IPv6仅本机规则允许且无该端口IPv6监听。只管理filter/MAAS-E5-CONFIG链和18840 INPUT钩子，不修改现有推理/数据库raw链或开放GPUStack18080。

API policy.allowed_cidrs及防火墙生成器都支持多个CIDR，已用第二网段10.12.0.0/22验证策略生成和API匹配，但线上仅现有/24。扩大CIDR前须有真实地址/路由规划，不能仅改规则就声称1000节点可接入。当前单实例及每TCP来源60请求/60秒的内存限速是试点值，不是1000节点容量验收，多节点共享NAT可能需要单独调整。

更新来源范围时应root原子替换private/policy.json并保持owner10001/mode600及127.0.0.0/8本机规则，再执行：

```sh
python3 /root/wtaobj/maas-bootstrap/code/bootstrap_boundary.py \
  --policy /root/wtaobj/maas-bootstrap/private/policy.json
```

API下一请求重新读取policy；不要为此重启Docker引擎或现有服务，不依赖重启boundary单元间接操作API。不要放行0.0.0.0/0或套用未知网段。变更后用实际授权/非授权来源核对。

## 操作与边界

- 状态：`systemctl status maas-bootstrap-config.service maas-bootstrap-boundary.service`。
- 只读宿主核验：`python3 /root/wtaobj/maas-bootstrap/code/verify_bootstrap.py`，仅输出公开元数据，不打印payload/token。
- 当前部署脚本只支持首次安装，已有private目录或单元时拒绝覆盖，防止重生成注册码/证书。首启曾因tar/umask使源码目录700导致导入失败，已修复公开代码目录权限；私有目录保持受限。无需重跑首次安装来修复此类问题。
- 通用publish/轮换CLI、引导脚本、封版工具及旧维护脚本适配仍待开发。暂不要手工打印完整release响应或cat秘密文件到聊天。配置停码不撤销已外发原生token。
- 暂停新服务：`systemctl disable --now maas-bootstrap-config.service`，保留专用DROP规则及私有材料便于安全回退；不删除Server/MaaS数据，不停止其他服务。需要删除规则时只移除18840专用钩子和自有链，禁止全局flush。

## 实测

见 [部署报告](../../results/maas-e5-preparation/BOOTSTRAP-HTTPS.md)。实际镜像隔离容器中8项测试通过；002和tower已在内存临时获取公开证书/试点码做读取验证，没有安装客户端、没有写设备凭据、没有注册worker。非LAN临时netns探测被拒绝且DROP计数增长，临时网络已清理。

后续重点是新克隆对原生18080可达及注册；tower不作为worker，其18080不通仅作观察，不列阻塞，也不据此放宽原生规则。
