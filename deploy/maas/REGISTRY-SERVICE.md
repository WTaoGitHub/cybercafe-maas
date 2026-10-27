# 常驻局域网镜像仓库（测试环境）

2026-10-27建立于001，https://10.11.31.1:18443；registry后端只发布127.0.0.1:18500。TLS入口仅监听LAN，Nginx只允许现有10.11.31.0/24和loopback。暂未实现多CIDR生成器、跨站点或公网服务。

服务：maas-registry-store.service、maas-registry-tls.service，enabled并实际active，Docker容器restart=no，由systemd管理；store持久目录/root/wtaobj/maas-registry/data。原临时maas-e5-registry容器/账号/数据保持停用且不变，未恢复旧测试账号。

本轮复用原有、已核对SAN的registry专用自签证书和001精确certs.d信任，重新生成reader/publisher账号。reader只允许读取，publisher可发布；私钥、明文凭据和publisher Docker登录目录仅在root私有目录，优先备份Mac，不入Git或聊天。它不是公共镜像仓库，也没有宣称生产高可用、自动异机备份或生产RPO。

操作：先核对systemctl status；停止仅使用systemctl stop maas-registry-tls.service maas-registry-store.service，保留数据/密钥及网络边界；恢复start store再start tls。不得运行旧pilot cleanup以免误认为操作的是同一仓库。setup脚本只允许首次部署，目录存在拒绝重跑；publish生成的release.json保存manifest digest，不能覆盖它重发不同内容。

发布镜像以已验证OCR为基础，新增公开release标记层，形成新的config ID、layer和manifest digest，仓库中使用maas/ocr:registry-r1。部署引用固定repo@sha256，tag只是发布名称；manifest digest和image/config ID分别登记。125MB旧探针、旧12GB镜像缓存和本次发布是不同证据。

后续worker认证应使用专用只读Docker config、按endpoint限定，排除推理容器继承；不把publisher登录文件或秘密环境变量复制进模型。证书信任、凭据撤销/轮换和缓存命中不回访仓库的边界须在后续接入验收中记录。仓库失联且无缓存时服务无法部署，不能伪成功。

代码：scripts/maas/runtime/registry_service.py；实测公开元数据位于results/maas-e5-registry-service。启用持久单元不等于做过VM重启验证；001/002禁止VM重启。
