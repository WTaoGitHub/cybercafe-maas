# 同机容器HTTP接入验收

2026-10-27。用户授权修改MaaS入口，允许001上其他容器访问。实际仅开放当前下游Docker网络，不开放公网。

## 已完成

- 新地址http://172.18.0.1:18831，桥接口br-bac8bba30f24，允许源172.18.0.0/16。
- 独立maas-container-http Nginx/systemd入口运行，复用现有Nginx image ID，active/enabled、restart count=0；原API/执行器/HTTP/TLS/GPUStack/下游容器等ID和启动时间保持。
- 防火墙使用原持久策略新增精确interface+source CIDR+destination gateway+18831例外；原boot/refresh规则生成器保留。手动执行原refresh后再次200，未依赖临时iptables规则。
- 原HTTP/TLS配置哈希与Mac备份一致；VM boot ID及Docker主进程PID保持。
- 只执行能力/边界检查，没有OCR请求、任务提交或成功用量新增。

## 真实网络验证

在kc-e2e-aliyun-slave3-server的network namespace中使用宿主Python探测。使用该网络的源IP和路由，但不是下游应用客户端/业务渠道本身的联调；后端镜像缺node工具，未安装软件。

| 检查 | 结果 |
|---|---|
| 能力查询，不带key | 401 |
| customer-a-key查询ocr.default | 200 |
| health及GPUStack模型列表 | 404 |
| 业务query参数 | 400 |
| 同容器网络直连API18830 | 阻断 |
| 同容器网络直连GPUStack18080 | 阻断 |
| 其他Docker bridge访问新入口 | 阻断 |
| 原回环HTTP能力查询，无key | 401，原入口保留 |
| 持久边界refresh后能力查询 | 200 |

本地边界/渲染专项18项通过；MaaS整体本地回归198项通过、56项跳过（缺少独立测试数据库），1条既有依赖弃用警告。未测试公网可达性，未进行VM重启；service enabled及refresh成功不写成跨VM启动验证。

首次安装在Nginx校验阶段失败：校验采用network=none，没有宿主网桥地址，导致bind检查失败；原策略自动恢复，原服务未改。改为host network校验后重新发布成功。该动作不是不确定推理重放，整个过程没有推理请求。

下游仍需将其endpoint由127.0.0.1改为http://172.18.0.1:18831，使用有效MaaS客户key；本轮未修改下游应用配置。未来网络重建改变地址/接口后须重新生成并验收，当前不支持自动信任任意新网段。

证据：[部署](deployment.json)、[网络检查与关联ID](verification.json)。操作/回退：[手册](../../deploy/maas/CONTAINER-HTTP.md)。Mac私有备份保存在.maas/backups/container-http-20261027，不入Git。
