# GPUStack v2.2.3：仓库、拉取与模型目录原生能力核对

后续状态：同日已完成第一项真实认证冷拉及离线缓存验证，见[第一项报告](../../results/maas-e5-registry/REPORT.md)。下文“未实测”为本调研阶段的历史边界；目录隔离、正式凭据接入和NFS仍未完成。

2026-10-26。结论：可以复用GPUStack原生模型部署、镜像拉取、模型下载及容器挂载，不应再写一套逐节点分发调度器。但仓库服务、Docker信任/凭据接入、worker外层挂载仍是我们的部署职责。源码支持不等于私有仓库已完成实测。

本次仅调研、只读取证和独立Python进程内mock验证；没有部署仓库、修改worker/model配置、重启容器/VM或恢复collector。

## 1. 版本范围

- 本地仓库/Users/4paradigm/dev/gpustack的v2.2.3 tag，提交1cfcbbff02aacdb51f0a390378dd7bcddd9bf365。
- 新worker实际安装：gpustack 2.2.3、gpustack-runtime 0.2.1、gpustack-runner 0.1.27.post4、Docker SDK 7.2.0。与tag依赖一致；没有把main的runtime 0.2.6行为套用到现有环境。
- tag与安装包公开源码另存本地.maas/research/gpustack223-distribution；[版本与文件摘要](../../results/maas-e5-distribution-review/source-manifest.json)。当前worker运行状态及挂载只读检查，不读取真实仓库密码或原生token。

## 2. 能力与复用结论

| 能力 | v2.2.3原生实现 | 本系统要做的工作 |
|---|---|---|
| 自定义/私有仓库镜像地址 | 支持model.image_name及custom backend的version_configs.image_name，最终交给runtime/Docker拉取 | 发布镜像并填写完整仓库地址，优先固定registry manifest digest |
| 默认仓库替换 | system_default_container_registry；仅补全不含显式registry的镜像名 | 不能以为改默认仓库就会覆盖现有quay.io/...地址；显式更新OCR/backend镜像地址，盘点pause/辅助镜像 |
| 用户名/密码认证 | runtime读取默认仓库认证环境变量，传Docker SDK auth_config；也有SDK客户端配置回退 | 把只读拉取凭据交给正确的worker进程，不把它放进公共配置或源码；实际认证冷拉仍待测 |
| 拉取策略/缓存 | Always、IfNotPresent、Never；默认IfNotPresent | 明确设置策略和digest，复用Docker分层缓存，不再每节点docker save/load |
| 模型下载 | Hugging Face、ModelScope原生下载、缓存、锁和状态；local_path只读取既有路径 | 可复用现有下载器；不把local_path当成“从001复制到worker”的指令 |
| 模型路径/挂载 | 非mirrored模式挂载resolved model path的父目录；Docker普通bind使用相同Source/Target路径 | worker内部先能看到目录；非mirrored时宿主机路径也要一致，不能假设任意映射会被自动翻译 |
| mirrored部署 | 官方Docker镜像默认启用，复制worker的挂载Source/Destination/读写属性，默认过滤docker.sock，支持ignore volumes | 外层worker单独挂模型目录；排除原生身份目录。原生机制可复用，不需要自研推理容器挂载调度 |
| NFS | 已由OS挂载的目录可作为local_path和bind使用 | NFS服务、宿主机挂载、启动依赖和故障边界不由上述GPUStack路径自动完成；不能把K8s PVC/GPU Service存储功能直接等同当前Docker方案 |
| 离线/镜像搬运工具 | 自带list-images/copy-images/save-images/load-images工具 | 用于系统镜像盘点及一次性预热；自定义OCR须核对是否在选中清单，必要时显式推送。不据此承诺全模型自动发现/自动发布 |

## 3. 私有仓库的准确配置边界

runtime 0.2.1的DockerDeployer._get_registry_auth_from_envs支持：

```text
GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_USERNAME
GPUSTACK_RUNTIME_DEPLOY_DEFAULT_CONTAINER_REGISTRY_PASSWORD
GPUSTACK_RUNTIME_DEPLOY_IMAGE_PULL_POLICY=IfNotPresent
```

拉取时优先读workload环境中的同名认证项，否则读worker运行时默认值；未显式提供auth_config时，Docker SDK尝试自己的客户端认证配置。推荐凭据进入受控worker启动配置，不把密码写入模型env供所有模型管理者读取。runtime默认值不是一个完整的按registry区分的凭据管理器；多个仓库、不同账号及辅助镜像需单独验证。

仅在宿主机执行docker login不能自动证明容器内Python SDK获得该账号。挂载docker.sock是调用Docker API，不等于挂载宿主机的~/.docker/config.json或credential helper。若采用SDK配置回退，必须验证worker所处用户/路径/DOCKER_CONFIG及helper；不建议把管理员Docker登录文件直接复制进母版。

集群界面的Image Credentials、Volume Mounts在本tag文档及schema中属于Kubernetes Cluster Options/k8s_options，不应当作当前Docker worker的现成推理镜像凭据分发功能。

自签HTTPS仓库还需宿主机Docker daemon信任仓库证书，和E5客户端访问配置服务器的信任是两条链。Linux Engine的证书目录为/etc/docker/certs.d/<registry-host:port>/ca.crt，见[Docker官方说明](https://docs.docker.com/engine/security/certificates/)。是否可在当前环境无Docker引擎重启生效仍需验证；不得因此重启001/002或擅自重启其Docker引擎。

digest拉取由runtime按repo@sha256格式处理。这里是registry manifest digest，不是前次docker image inspect得到的本地image/config ID；不能把两者直接互换。发布后取得仓库digest再登记版本。

## 4. 目录与当前试点的重要更正

官方pack/Dockerfile将GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT默认设为true。实际新worker也为true；运行中OCR容器实际挂载如下：

```text
宿主机 /var/lib/maas-bootstrap/gpustack-worker
  → OCR容器 /var/lib/gpustack，读写
```

这是worker整个原生数据目录，其中有原生身份文件；当前没有独立模型bind。见[实际挂载](../../results/maas-e5-distribution-review/current-mounts.json)。本次不读取身份文件内容、不认定发生凭据泄漏，但必须在后续部署修正共享范围。

此前试点文档“运行时仅挂载公开模型目录父目录”的描述不准确：那个规则只在非mirrored模式生效。复制进worker的公开deployment.json让local_path检查通过，并不证明对应目录已挂入OCR容器；OCR服务成功是因为程序和权重在OCR镜像内。请求、GPU识别及计量通过的证据仍有效，不能据此宣称外置权重分发/挂载已验收。

### 建议优先复用mirrored机制

1. 增加独立公共模型根目录，例如/srv/maas/models；worker启动时显式bind。OCR镜像内权重场景可先预置固定公开model marker目录，解决v2.2.3 local_path不能为空的约束，明确它不是权重目录。
2. 本地只读发布目录/NFS目录可read-only挂入worker；mirrored机制会保留只读属性并传给推理容器。
3. 将/var/lib/gpustack加入GPUSTACK_RUNTIME_DEPLOY_MIRRORED_DEPLOYMENT_IGNORE_VOLUMES，保留镜像已有ignore列表；不让推理容器继承原生token/UUID等目录。认证配置的挂载也需排除，不能只排除docker.sock。
4. Hugging Face/ModelScope下载需要可写cache时，将cache_dir单独放在可共享的模型cache卷；不能排除原生卷后又让模型文件留在被排除的路径。只读发布与可写cache分开设计。
5. 可显式指定GPUSTACK_RUNTIME_DEPLOY_MIRRORED_NAME=maas-gpustack-worker，避免查找自身容器时依赖不稳定hostname。镜像继承的GPU能力由原生runtime处理。
6. E5检查启动所需目录、挂载和启动规范版本；布局变化应报需要维护，不让已有旧布局容器因manifest fingerprint不变而误报UNCHANGED。新增布局只在获准的worker容器维护或下一新克隆生效，不做VM重启。

不建议为了省挂载配置就把整个/root、/root/wtaobj或原生身份目录共享给模型容器，也不建议再用docker cp作为正式分发方式。

## 5. 源码与验证依据

以下行号均来自固定tag或实际安装包，而非main：

| 源码 | 关键位置 |
|---|---|
| gpustack/utils/config.py | 74–117，显式registry不被默认仓库覆盖 |
| gpustack/worker/backends/base.py | 604–623，非mirrored模型父目录挂载；926起镜像解析 |
| gpustack/schemas/models.py | 174–195，local_path非空约束 |
| gpustack/worker/downloaders.py | 25–51，HF/ModelScope/local_path三种来源 |
| gpustack/schemas/clusters.py | 75起ImageCredential及K8sClusterOptions，Kubernetes范围 |
| pack/Dockerfile | 735–745，官方镜像默认mirrored=true |
| runtime 0.2.1/deployer/docker.py | 422–468，认证与pull；478–519，策略；699–759，同路径bind；1278–1289/1332–1357，mirrored及排除 |
| runtime 0.2.1/envs.py | 506–512，IfNotPresent默认值 |
| Docker SDK 7.2.0 ImageApiMixin.pull | auth_config为空时用客户端registry auth配置，有值时覆盖 |

针对已安装runtime做了7项纯内存mock检查：缓存命中不拉取、未命中拉取、Always拉取、Never不拉取、默认认证解析、workload认证覆盖、普通bind路径/只读属性保持。全部通过，未调用Docker API、未连接仓库、未改变worker服务。见[mock证据](../../results/maas-e5-distribution-review/native-runtime-mocks.json)。源码支持、mock与真实私有仓库验收明确区分。

可复用核对脚本：[probe_distribution_runtime.py](../../scripts/maas/research/probe_distribution_runtime.py)，在worker容器独立Python进程运行；不将它安装为常驻服务或请求前置门禁。

## 6. 下一步最小验证顺序

先确定“复用原生”方案，不立即实现自研分发器：

1. 准备局域网registry，发布一个很小的专用测试镜像，配置只读拉取账号和Docker信任。只规划本轮允许的环境，不通过VM/Docker引擎重启强制生效。
2. 由原生runtime从空缓存拉取；验证错误账号失败、正确账号成功、digest一致。已有大OCR缓存不能作为冷拉成功证据，不能删除正在用的OCR镜像制造冷缓存。
3. 在独立新克隆中验证：模型目录在host/worker/推理容器可见，原生身份目录未被继承；重建模型后布局正确；母版保护与自动注册仍有效。
4. 复用原生GPUStack部署OCR，随后做MaaS同步、异步和结果核对，再决定是否封新快照。真实首次启动仍由管理员新建克隆完成。
5. 最后验证仓库断连时缓存命中行为、缓存未命中失败和明确错误；不要把它变成透明自动重放推理。

本轮尚未做实际认证仓库冷拉、自签仓库无重启信任验证、目录隔离整改、NFS实测或新快照验证。注册凭据管理、1000节点容量等后续工作不因这次能力确认而自动视为完成。
