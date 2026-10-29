# 新快照无OCR缓存克隆首启验收 · 2026-10-27

结论：端口SSH_PORT_REDACTED对应的新独立克隆已完成“boot读取配置 → 独立原生注册 → 自动准备registry信任/只读认证 → 原生调度 → 首次拉取OCR镜像 → OCR/MaaS服务”的完整链路。没有手动补节点配置、传镜像或运行bootstrap apply/pull/load，模型18及路由17也未修改。实际VM/快照由管理员操作，我们未重启VM。

## 实际观测

| 项目 | 结果 |
|---|---|
| 新实例 | hostname XZ-31-002、LAN10.11.31.2复用，但boot ID为ec913d05-5caf-428a-937e-90fc537c3d2b，独立worker7/e5-4672ade0506987f34555caed |
| 开机配置 | applied.trigger=boot、APPLIED、e5-registry-r1、registry-access-v1，四模块SHA与已更新母版一致 |
| 实例身份 | 新worker7与旧worker6不同；启动容器restart count=0且后续容器ID/boot保持 |
| 首次拉取条件 | 捕获到新manifest、新config ID和旧OCR config ID都不存在；当时仅GPUStack worker和pause辅助镜像，无OCR镜像缓存 |
| 仓库自动接入 | boot生成reader专用auth config，目录700/文件600，endpoint只有10.11.31.1:18443；Docker信任证书SHA与仓库匹配 |
| 原生部署 | 旧worker6已not_ready；GPUStack自行将同一model18新实例103调度到worker7，selector仍os=linux/arch=amd64、test1、replicas=1 |
| 镜像 | 原生103.0.log记录Pulling→Pulled，固定manifest9ec6c93f…，最终config/image ID=b46b3486…且RepoDigests一致；本轮未创建新模型或改路由 |
| 隔离 | 实际推理容器只有models RO/cache RW，模型写入EROFS、缓存写入宿主可见；原生身份/socket/registry认证文件和原生token环境未共享 |
| 请求与计量 | 私有TLS同步200/46行、异步SUCCEEDED/attempts1、结果200/46行/revision ocr-registry-r1；SQL成功1/usage事件1 |
| 收尾 | worker7 ready、instance103 running、MaaS接单正常，无在途/UNKNOWN；registry两单元active |

APPLIED中的native_state=not_ready是注册刚完成时的快照，不是最终健康验收；最终原生ready、实例running和真实OCR请求共同确认可用。DOCKER_CONFIG公开路径字符串仍被mirrored继承，但没有对应认证目录或密码，不能混称为凭据共享。

本次“无缓存”指无OCR业务镜像可用，不指没有GPUStack基础镜像或任何共享基础layer。下载过程中没有手动预热；镜像/权重仍打包在同一OCR镜像内，独立权重下载/NFS未因此验证。上传的挂载检查脚本在模型已自行启动后执行，只写入并清理随机QA探针文件，不参与引导或部署修复。

## GPU与身份补充

当前GPU RTX4080SUPER、580.95.05，UUID为GPU-50b437cb-59db-64a9-05a9-ca0cdb8b356a，与旧worker6观测值一致，新VM boot和worker身份不同。这提供一例“同GPU UUID跨独立VM再次出现”的实测，不证明任意平台/重绑定都稳定，也不证明PCI直通、vGPU机制或UUID并发排他。原生身份继续独立，不把GPU UUID当认证凭据或worker主键。

SSH新端口初次未在known_hosts登记；扫描的ED25519公钥与已信任母版SSH_PORT_REDACTED一致后才加入新端口别名，未关闭host-key验证。共享SSH主机密钥是既有快照环境的事实，不代表完成了系统身份泛化。

## 证据与边界

[启动与无OCR缓存](startup.json) · [原生拉取及镜像一致](image-and-pull.json) · [实际挂载](mount-proof.json) · [OCR/计量](acceptance.json) · [最终原生/业务状态](final.json) · [GPU再现观测](gpu-reappearance.json)。

本次保留模型/路由/MaaS revision及发布记录不变；旧pin中worker清单是发布时快照，实际部署位置以原生instance103及本轮证据为准，不把历史发布清单当实时拓扑。旧worker记录未清理；新增节点也不自动增加副本数。

本轮通过的是一个新克隆的正常首启和旧节点离线后原生模型恢复，不是所有E5/E1增强、双克隆并行、凭据轮换、仓库HA/生产RPO、性能耐久或千节点容量验收。母版不需为这次QA再更新；后续按业务计划继续模型/权重分发完善及其他增强，E8仍未实施。
