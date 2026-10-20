# H3及对应公网隧道停用记录

2026-10-20，按用户要求在XZ-31-001和XZ-31-002执行。

- `cybercafe-h3`、指向127.0.0.1:11435的`cloudflared`均停止；restart policy改为`no`。
- 容器保留、镜像保留，`h3-weights`模型卷保留；停前停后文件清单与大小一致。
- 原进程结束，11435不再接受连接；没有卸载或执行镜像/卷清理。
- 更新两节点运行期网络策略的活动容器库存，规则与库存均通过核对。
- 原OCR和P2试点四个实例逐一返回HTTP200、46行；两worker ready，collector正常。

旧cybercafe-agent保留。关闭的是Docker自动重启，不能阻止未来管理端明确下发新的H3安装/启动命令；本地Agent代码按收到的命令启动组件，不是本次修改对象。请避免在旧管理页面重新部署H3，以免重新启动服务或触发该旧系统的互斥部署逻辑。

恢复方法（仅记录，未执行）：

```sh
docker update --restart=unless-stopped cybercafe-h3 cloudflared
docker start cybercafe-h3
# 确认本机11435服务就绪、资源与维护边界允许后：
docker start cloudflared
```

重启隧道可能生成新公网地址。恢复后需重新核对网络策略的inventory_matches；不能直接沿用当前停用状态的库存结论。原restart设置、容器ID及模型清单在各节点`/root/wtaobj/maas-h3-parked/before.json`中私有保存。

证据：[001停用](node-001.json)、[002停用](node-002.json)、[001网络策略](policy-001.json)、[002网络策略](policy-002.json)、[OCR复核](ocr-verification.json)。
