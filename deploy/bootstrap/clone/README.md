# E5 克隆客户端与离线母版工具

2026-10-23：代码和本地模拟测试已完成；真实新版母版快照、克隆启动与原生注册未验收。001配置API已在线，客户端真实HTTPS读取/验签已验证。新增worker/封版/发布工具本轮未部署到母版，不自动创建VM或操作快照。

## 启动和手工操作

镜像安装后的命令（仅用于另行创建的新克隆）：

```sh
sudo /opt/maas/bin/maas-worker-bootstrap prepare
sudo /opt/maas/bin/maas-worker-bootstrap apply --trigger manual
sudo /opt/maas/bin/maas-worker-bootstrap status
```

`prepare`只拉取、验签和私有暂存，不启动worker。`apply`是boot/manual统一入口：拿进程锁→共享码HTTPS取配置→验证签名、scope、image_id、架构、固定原生地址、镜像摘要→核对依赖→启动自身worker→以原生worker_token查询`/v2/workers?me=true`并核对cluster/name/UUID。`status`只输出脱敏本地状态，**不是实时健康接口**。GPUStack原生state单独输出；注册成功不等于模型已经部署或可推理。

新worker使用本机随机名称和独立空数据目录，不使用模板固定worker名，也不建立GPU UUID台账。CLI拒绝在001、002、tower及已知001/002地址上启动。实际克隆必须具有区别于受保护节点的主机名/IP；这依赖供应商首启初始化，当前工具不会更改hostname或网卡。

- APPLIED：已启动或恢复所属容器，原生注册确认通过。
- UNCHANGED：匹配的容器已经运行且此前相同配置已确认；不重复创建/启动。
- NEEDS_MAINTENANCE：现有容器与新配置指纹不同，包括已停止容器。保留原状，由管理员在维护窗口处理替换；没有自动删除/重建命令。
- REGISTRATION_UNCONFIRMED：本次未能确认，保留实际容器；再次执行只检查/恢复相同所属容器，不创建第二个worker。
- OTHER_WORKER_PRESENT / NATIVE_IDENTITY_WITHOUT_LOCAL_OWNER：发现其他worker或无本地所有权标记的旧身份，拒绝接管或清除。
- BOOTSTRAP_BUSY：已有本机引导在执行，等待后再手动检查。

systemd单元`maas-worker-bootstrap.service`等待网络与Docker，调用`apply --trigger boot`。失败每30秒重试，10分钟最多5次；超过次数需管理员处理后重新启动该单元（只用于新克隆）。容器`restart=no`，防止绕过开机配置核验。单元成功后不常驻轮询：**容器随后退出不会被本单元持续自动恢复**，管理员可手动apply。无SSH采集、配置后台刷新、云备用切换、容器自动停止或VM重启。

## 离线镜像安装

工具只向一个**离线挂载/解包的Ubuntu文件系统目录**写入固定文件，不修改当前宿主、不安装包、不拉取镜像、不输出qcow2/供应商快照。磁盘格式导出/快照保存仍由供应商平台或镜像构建流程完成。

在具备构建材料的受控机器上，从本仓库运行，路径替换为实际离线目录：

```sh
install -m 600 deploy/bootstrap/clone/image-settings.example.json /secure/e5-image-settings.json
# 在受控编辑器中核对该文件里的源码证书/公钥/共享码文件路径，禁止把秘密写入命令参数。
python3 scripts/maas/prepare_worker_image.py \
  --root /mnt/offline-ubuntu \
  --settings /secure/e5-image-settings.json
```

settings示例包含试点公开地址及秘密文件路径，没有实际码。工具把克隆中的引用重写为`/etc/maas/`，安装客户端到`/opt/maas/lib/maas_bootstrap`、固定入口到`/opt/maas/bin/maas-worker-bootstrap`，创建systemd启用链接。共享码和本机JSON为600，配置目录700；以root构建实际交付镜像，确保所有安装文件归root所有。macOS临时目录测试产物不能直接认作完成Ubuntu镜像。

封版前必须另行满足：

1. x86_64 Ubuntu、Python3和`cryptography==41.0.7`（优先复用Ubuntu24.04已核对的包）；Docker、NVIDIA驱动和Container Toolkit已配置可用。
2. 正式manifest里的GPUStack v2.2.3固定摘要镜像已预缓存；客户端不会在线自动pull，也不会自动修改驱动、Docker或Toolkit。`docker image inspect`必须能匹配RepoDigest。
3. 原生状态目录、E5实例状态目录和Docker容器为空；缓存镜像允许保留。不得封入已注册token/UUID、固定worker名称、运行过的E5 identity.json。
4. 供应商完成模板generalize及首启身份生成契约：空/`uninitialized` machine-id，克隆首次生成不同machine-id、SSH主机密钥，保留管理员公钥授权；已有SSH主机密钥会阻止本工具封版。工具不删除这些材料，也不提供批量清理命令。
5. 已有CyberCafe/gpustack开机服务与新引导的责任边界经核对，避免两个worker自动启动。工具只拒绝已知冲突单元，**不能发现所有第三方启动钩子**，需检查供应商机制。
6. 新克隆能访问001的18840及原生18080，原生worker所需反向端口/容器网络符合GPUStack要求。母版到18080不通不作验收；不得因此放宽无关端口。

污染或文件已存在时拒绝写入；它不是重入安装器，也不自动清理部分构建。写盘/权限失败后丢弃该离线构建副本，从干净base重新构建；不要恢复001/002镜像。输出`/opt/maas/image-manifest.json`只有公开元数据、源码/公钥摘要和待满足条件，**不是启动验收合格证明**。

## 配置发布与回退

`maas_bootstrap.publisher`只在配置服务器受控操作环境执行，不对外提供写API。需要当前客户端范围settings、公开manifest JSON、600权限的registration JSON（cluster_id和token）、只留operator的Ed25519私钥。先把本仓库该模块安装到受控Python路径；本轮未将新发布CLI部署到001。

```sh
python3 -m maas_bootstrap.publisher \
  --root /root/wtaobj/maas-bootstrap/private \
  --settings /secure/e5-image-settings.json \
  --signing-key /root/wtaobj/maas-bootstrap/operator/release-signing.key \
  publish --manifest /secure/manifest-r2.json --registration-file /secure/registration.json
```

工具验签回读、核对客户端scope和policy、互斥发布、不可变revision、原子切换active摘要；写入文件继承private目录owner，避免root操作后API UID10001不可读。签名只覆盖公开manifest，原生token由HTTPS保护；均不写到输出或shell参数。失败不切换active，可能留下完整未激活版本，保留供检查。

回退以旧内容发布**新revision**，不覆盖历史、不操作任何worker：

```sh
python3 -m maas_bootstrap.publisher \
  --root /root/wtaobj/maas-bootstrap/private \
  --settings /secure/e5-image-settings.json \
  --signing-key /root/wtaobj/maas-bootstrap/operator/release-signing.key \
  rollback --source e5-pilot-r1 --new-revision e5-pilot-r3
```

回退可能重新外发旧原生token，操作前确认它仍有效且没有因泄漏撤销；停用共享码不撤销已经外发的原生token。运行中的worker读取新revision后仍可能需要维护，不把配置回退当作推理进程回退。

## 下一批实际验收

先完成母版generalize/依赖和旧启动钩子核对，再安装E5文件；准备完成后交由管理员保存新快照，并由管理员创建、启动独立克隆A/B。管理员交付访问入口和快照版本后，我们再执行真实验收。验证同码同配置、独立名称/UUID、真实GPU容器、原生注册和模型请求、SSH手动重复apply、错误配置不破坏原worker。不能重启001/002，不能在母版上启动实验worker。详见[E5部署矩阵](../../../docs/framework/E5-TEST-DEPLOYMENT.md)。


## 2026-10-26部署状态更新

客户端已手动部署到tower，固定镜像已缓存；旧agent/provision disabled/inactive，E5开机单元enabled/inactive，母版不注册worker。发布CLI仍未在001线上演练，封版/真实新克隆验收待完成。后续以[实际部署手册](MANUAL-TOWER-DEPLOYMENT.md)为准；其第15节记录本次证据，不把本文早期未部署状态作为现状。
