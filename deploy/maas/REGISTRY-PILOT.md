# GPUStack原生私有仓库试点操作

本轮001控制端和端口16482的新克隆（worker5/test1）使用独立探针。不停止OCR，不重启VM或Docker，不修改worker启动参数；新建测试registry与模型是独立资源。本轮已完成并清理，详细结果见[报告](../../results/maas-e5-registry/REPORT.md)。

## 组件与端口

- registry:2只发布127.0.0.1:18500，持久数据在/root/wtaobj/maas-e5-registry/data。
- nginx:alpine以host网络仅监听10.11.31.1:18443，允许测试LAN网段，使用独立自签证书。GET/HEAD使用reader或publisher，写操作只允许publisher。日志不记录Authorization头。
- 凭据、TLS私钥、publisher Docker认证文件均在root私有目录，密码不在shell参数或Git中。发布调用password-stdin。
- 只向两个Docker daemon新增精确registry host:port证书目录，未修改daemon.json、全局insecure registry或既有证书。不以重启强制应用信任。

## 分阶段脚本

1. registry_pilot.py setup：检查资源名及端口，生成临时凭据/证书，启动独立registry和TLS入口；目录已存在时拒绝覆盖。
2. registry_pilot.py publish：TLS认证/只读正反例；通过publisher推送维护的125MB探针，记录manifest digest和image/config ID。当前状态已PUBLISHED，不能直接重复运行setup/publish。
3. 将server.crt公钥证书复制到新克隆；按registry_worker_pilot.py prepare核对预期boot、证书SHA256、空缓存，保存原worker/OCR容器和Docker PID基线，再新增trust目录。只复制公钥证书，不能发送服务端私钥或publisher凭据。
4. probe_registry_models.py create --case wrong，随后observe确认授权失败；本轮的首次backend创建因缺少-custom后缀返回400，无资源创建，已核对后修正，不是盲目重试不确定操作。
5. create --case cold，等待observe记录running及health通过；registry_worker_pilot.py image核对digest引用和image ID。此步骤是GPUStack自己拉取，不是在worker先行docker pull/load。
6. 仅停止新建maas-e5-registry-tls入口，确认worker连接被拒绝，再create/observe --case cache，验证新模型实例仍可运行。不要停止GPUStack/worker/OCR或Docker引擎。
7. cleanup删除本次模型和backend，原位清空两个htpasswd文件撤销测试账号；临时启动TLS入口验证旧账号均401，再停止两个registry测试容器。保留数据和镜像，restart=no；最后检查原OCR请求、容器/PID/boot未变和无遗留探针。

脚本均在scripts/maas/runtime：registry_pilot.py、probe_registry_models.py、registry_worker_pilot.py。prepare等阶段使用公开证书摘要、预期boot和image digest参数；它们必须取自本次核验，不按复用hostname盲目操作另一台机器。

## 当前收尾状态与后续使用

服务已停止、测试账号失效；私有回退文件已备份Mac。若后续建设正式registry，先选择服务生命周期、重新签发/确认信任与配置新只读账号，不直接启动后假定旧账号可用。不要删除镜像、原服务数据或旧模型来制造冷缓存；使用独立新digest测试。

存量原生worker身份卷的mirrored继承是独立待整改项。本轮未修改其挂载、镜像或母版；也没有做NFS、千节点分发、生产可用性或长期存储验收。
