# registry-access-v1部署、验收与回退

2026-10-27。范围是当前独立克隆worker6的显式容器维护，母版没有更新，不制作快照/新VM，不重启001/002或Docker引擎。此文为REGISTRY-SERVICE.md之后的操作记录，不能直接在其他boot/worker上重跑固定ID脚本。

## 已落地的契约

- 常驻测试仓库10.11.31.1:18443，稳定局域网独立主机迁移仍是上线前事项；系统单元enabled/active不代表已做VM重启验收。registry_store的数据目录需要独立备份容量与策略，当前没有生产RPO或HA。
- 活跃签名配置e5-registry-r1，compatible_image_ids仅tower-e5-registry-v1；当前克隆bootstrap.json已更新image_id及allowed_registry_endpoints。旧母版/快照仍是tower-e5-pilot-v1，现配置服务会对旧image_id返回409。这是显式兼容门槛，不是已经自动升级旧快照；下阶段必须更新母版后再请管理员保存快照，不能继续用旧快照验证仓库首启。
- manifest.registry_access只记录endpoint、credential_id和payload_sha256。证书/只读用户名/密码位于HTTPS受保护registration.registry_access；Ed25519签名绑定payload摘要。本地allowlist再限制允许的registry目标。
- bootstrap生成独立DOCKER_CONFIG文件：state_dir/registry-auth/<payload_sha256>/config.json，目录700、文件600。专用目录RO挂到worker /run/maas-registry-auth，并加入原生mirrored排除。Docker SDK按endpoint选择凭据，不对quay.io发送这个账号，不配置全局username/password环境。
- Docker daemon的精确certs.d/<host:port>/ca.crt由bootstrap准备，发现不同证书拒绝覆盖；没有insecure registry、没有关闭TLS或重启daemon。当前Docker28.2.2已实际接受新增信任。
- 认证轮换改变签名配置/fingerprint，已有worker报NEEDS_MAINTENANCE，不做自动停容器或热轮换。共享码/原生token仍遵循现有边界，root/privileged能接触本机秘密，目录隔离不构成强沙箱。

## 本次实际维护

Mac私有备份.maas/backups/registry-access-v1-20261027含旧源码/配置/worker inspect、新registry认证与TLS材料及控制窗口。001另存root-only window.private.json，克隆保存root-only before.private.json。

1. registry_rollout.py prepare精确核对worker6/旧model17、无排队/在途/UNKNOWN，pause generation27，启用native维护，model17降0并确认实例消失。
2. 发布新签名配置；install_registry_client.py核对boot、旧源码摘要和staged新摘要，再安装client/worker/registry。停止并保留旧worker为maas-gpustack-worker-before-registry-v1-20261027，restart=no；root-only receipt仅批准这个旧容器ID。原生name/UUID/token/JWT文件不变，新worker仍ID6，重复apply=UNCHANGED，Docker PID及boot不变。
3. 确认新manifest引用和新image/config ID在worker不存在；SDK解析到reader，仅作用于本仓库。registry_rollout.py release解除native维护，MaaS仍暂停。
4. 创建backend22、model18/route17，固定仓库manifest digest。selector使用原生os=linux、arch=amd64并限定cluster3/test1，不使用随机worker-name。replicas=1；新worker加入不代表自动增加副本。下阶段若保留旧worker同时上线新克隆，需单独安排测试副本/部署。
5. GPUStack原生拉取并启动instance102，没有在worker手工pull/load。仓库日志来自10.11.31.2的manifest/config/layer请求200，image ID与发布对应。旧OCR基础镜像已缓存，因此这不是整块磁盘完全无缓存的首次开机实验。
6. 新MaaS revision ocr-registry-r1完成恢复探针及同步/异步/下载/单次计量验收；generation28恢复接单，UNKNOWN=0。旧model17副本0及旧worker记录保留。

实际推理容器仍只有模型RO与缓存RW两个卷，认证目录、原生身份和socket不继承。原生mirrored会继承DOCKER_CONFIG的公开路径字符串，但该目录/文件在推理容器不存在，未继承registry密码或GPUSTACK_TOKEN。不要把路径字符串误判为凭据本身；探针已按实际边界核对。

发布前operator settings曾指向克隆的公钥路径，发布在写入前被拒；核对旧active未变后改为001本地operator公钥。部署时原生标签为小写，确认仅backend创建、未创建model后执行显式create-model阶段，没有重复提交不确定模型请求。两项均已记录在最终脚本及实际验收中。

## 回退材料与手动步骤（未演练）

不运行旧泛用维护binding，它仍不是此次worker6维护配置。回退应显式pause/排空并核对generation；native维护worker6，model18降0且确认实例停止，任何UNKNOWN不得自动解除或重放。

先保留当前数据与私有配置，再按installed.json中的精确新容器ID停止/移除新worker，将停止的旧容器改回maas-gpustack-worker。保留原生身份目录，恢复Mac或节点备份中的旧client/worker/bootstrap.json，以及配置服务旧active指针（window.private.json old_active），核对对应旧release仍存在；不要删除registry数据、镜像或秘密备份，也不能让新旧worker并行启动。

旧实例恢复ready后恢复旧model17副本和原MaaS catalog/route，确认真实OCR探针，再恢复接单。key模型许可如果回退，使用switch-backup中的旧列表并保留实际scope，不能扩权。若数据库未改，无需数据库回退；遇未知状态保持暂停。此次保留可执行材料，未声称实际回退通过。

## 母版/新克隆待办

仅下一阶段向母版安装新client.py/worker.py/registry.py及相同storage.py，配置new image_id和registry endpoint allowlist；保留母版guard，不复制本次state、native身份、auth文件、applied或receipt。秘密由克隆从受信配置服务器获取，publisher账号不能入母版。

管理员再保存快照并新建独立克隆，以无业务镜像/配置缓存、无人手动补配置为条件验证boot→注册→被调度→认证拉取→OCR。旧基础镜像缓存存在的本次测试不能替代该验收。模型权重仍image-contained，NFS/HF权重分发、凭据热轮换、千节点拉取风暴与2.3升级均未实施。
