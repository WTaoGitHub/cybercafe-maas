# 同机Docker容器接入MaaS HTTP

2026-10-27已部署到001。源代码模块是host端网络/入口工具，不要求重建MaaS API或执行器镜像。

## 当前地址及范围

下游后端kc-e2e-aliyun-slave3-server在用户定义bridge网络kc-e2e-aliyun-slave3中；当前IP172.18.0.5，网关172.18.0.1，网桥br-bac8bba30f24，CIDR172.18.0.0/16。

该网络内的消费者使用：

```text
endpoint = http://172.18.0.1:18831
model = ocr.default
Authorization: Bearer <已登记的客户推理key>
```

兼容OCR由消费者追加/ocr；任务接口为/maas/v1/tasks等既有路径。endpoint不要包含/ocr。key仍需MaaS绑定及GPUStack授权；本轮没有实现服务级认证解耦，也不取消认证。

容器内127.0.0.1指自身，无法访问宿主机回环监听。单独设置host.docker.internal/host-gateway也不会让一个仅回环监听的服务自动可达；以这里经校验的明确网关地址为准。

HTTP入口只绑定172.18.0.1:18831，不绑定0.0.0.0、LAN10.11.31.1或公网IP；Nginx仅允许172.18.0.0/16。所有业务路径、方法/query限制、20MiB请求体、超时、禁止上游自动重试、授权头透传和身份头清理沿用原入口。网络允许不代表匿名访问允许。旧127.0.0.1/[::1]:18831及私有TLS18832保持。

## 原理和运行资产

```text
下游容器172.18.0.x
  → 明确桥接来源/接口/目标/端口的pre-DNAT例外
  → 宿主机172.18.0.1:18831的独立Nginx（CIDR ACL + 路径白名单）
  → 127.0.0.1:18830 MaaS API
  → 既有鉴权/任务/GPUStack链路
```

| 资产 | 位置 |
|---|---|
| 新systemd服务 | maas-container-http.service，enabled/active，失败仅重试本入口 |
| 新容器 | maas-container-http，host network、非root、只读、drop ALL capabilities |
| 新Nginx配置 | /root/wtaobj/maas-container-http/nginx.conf |
| 原持久化边界配置 | /root/wtaobj/maas-boundary/persistent.json中的container_http_clients |
| 纯生成器 | maas_platform/container_ingress.py |
| 原raw规则生成器 | maas_platform/boundary_policy.py，新增精确18831例外 |
| 操作脚本 | scripts/maas/runtime/enable_container_http.py |

原raw防火墙将18830–18835作为受保护本机端口，曾导致即使换成Docker网关仍超时。新例外仅匹配指定bridge接口、来源CIDR、目标网关地址和18831，不把172.18整个网段设为原生管理/推理可信来源；不允许其访问18830或18080等内部端口。

服务单独运行，避免Docker网桥尚未创建时bind失败拖垮原回环入口。仅使用已存在且固定image ID的Nginx镜像，未拉取镜像，不重启Docker/已有容器/VM。systemd在Docker和早期边界单元之后启动；原boot/10秒refresh生成器读取持久策略。已实测refresh后可达，**没有进行VM重启验证**。

## 规划/重应用

需要root在001运行；操作前将持久策略、已有生成器、相关配置备份到Mac的Git忽略目录并核对。首次本地备份位于.maas/backups/container-http-20261027。

```bash
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/enable_container_http.py \
  plan --network kc-e2e-aliyun-slave3
```

plan不写文件，输出network/interface/CIDR/gateway及policy_before_sha256。确认范围及备份一致后，使用实际摘要：

```bash
python3 /root/wtaobj/maas-framework/scripts/maas/runtime/enable_container_http.py \
  apply --network kc-e2e-aliyun-slave3 \
  --expected-policy-sha256 <已核对并备份的当前策略SHA256>
```

操作共用持久边界锁，先在host network校验Nginx配置，再写策略、原子更新自有raw链，首次启动独立入口或重应用时reload本入口。不能使用network=none校验绑定宿主网关的配置，它没有该地址。步骤异常会恢复原策略；首次创建的本入口会停止禁用，原MaaS服务不动。

配置只支持显式Docker私有IPv4 bridge，每个地址一个入口，端口固定18831；拒绝默认路由、公网CIDR、非桥接接口和开放内部端口。若Docker网络重建导致接口、网关或子网变化，需要重新plan/apply及测试；不会自动信任所有新Docker网络。

若修改API路径白名单/代理参数，更新canonical edge.nginx.conf后重新生成此入口并回归，不应只更新旧回环容器而忽略本入口。001上原HTTP/TLS文件是单独部署资产，重生成此入口不会修改它们。

## 验证和回退

本轮在下游后端的实际network namespace中使用宿主Python进行请求（后端镜像没有node工具），只查询能力接口，不发送OCR：带客户key200、不带key401、health/原生模型列表404、query400；内部API18830和GPUStack18080阻断，其他Docker桥网络到本入口阻断。与下游程序自己的HTTP客户端/渠道配置联调仍需在其应用中完成。见[验收报告](../../results/maas-container-http/REPORT.md)。

```bash
systemctl is-active maas-container-http.service
systemctl is-enabled maas-container-http.service
journalctl --no-pager -u maas-container-http.service -n 30
```

首次只有本网络时的回退：停止并禁用maas-container-http.service；在persistent.json中合并移除对应network_name条目，保留所有其他策略；执行原persist_boundary.py refresh核对raw策略。保留配置/代码及Mac备份供恢复，不删库、不回退应用镜像、不动原HTTP/TLS入口。未来若一个入口管理多个网络，回退某个网络须重新生成剩余入口，而非直接停止整个服务。回退会使使用172.18.0.1的新下游连接不可用，须与下游协调。
