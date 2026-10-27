# registry-access-v1母版更新 · 2026-10-27

结论：当前6025母版tower-KuGJk9WU8已完成仓库接入客户端更新，文件和兼容配置验证通过，可以交给管理员保存新快照。本轮没有保存快照、新建VM、启动worker或重启VM/Docker；既有旧快照不会随母版文件修改自动更新。

## 实际变更

- 安装client.py、worker.py、registry.py，storage.py摘要保持原版；基线三模块摘要与预期完全一致后才更新，新文件由已提交609c0d0源码核对。
- bootstrap.json的image_id由tower-e5-pilot-v1改成tower-e5-registry-v1，allowed_registry_endpoints仅10.11.31.1:18443；保留test1/cluster3、配置服务器TLS信任、签名公钥、共享码路径和原生身份/模型目录设置。
- 先备份旧源码/配置到Mac私有目录.maas/backups/registry-template-20261027/tower-KuGJk9WU8；节点另留root-only e5-registry-template-install备份，没有清理旧业务凭据或供应商身份。

## 核对结果

1. 从实际/opt/maas/lib导入并核对四模块摘要；只在内存获取和验证e5-registry-r1，签名、registry payload绑定、endpoint allowlist和registry-access-v1布局计算通过，没有调用prepare/apply或registry.prepare。
2. 母版guard返回PROTECTED_HOST_WORKER_FORBIDDEN。systemd单元enabled，failed为原母版开机保护拒绝留下的状态，本轮未重置/启动它，不把这个状态写成worker故障修复。
3. 没有identity/applied/prepared/generations/maintenance receipt/registry-auth；原生worker身份目录不存在，无任何Docker容器。未把reader/publisher、原生token、实例配置或克隆维护数据写入母版。
4. 固定GPUStack worker镜像缓存可用且只有一项镜像；OCR没有预置。模型公开marker/目录可用。完全空业务镜像克隆的端到端首启仍须新快照验证，母版侧检查不能替代它。
5. boot ID和Docker PID保持，未触碰001/002现网配置或运行容器。

证据：[安装](installed.json) · [实际路径导入与配置核对](verification.json)。操作脚本见scripts/maas/runtime/install_registry_template.py，只适用显式核对过的母版和源摘要，不用于worker维护或自动重试覆盖。

## 下一步

管理员保存当前母版新快照，备注registry-access-v1 / tower-e5-registry-v1 / 2026-10-27，启动普通模式独立克隆，提供SSH入口。不要从当前运行worker保存母版。我们随后观察boot自动读取配置、独立注册、证书/认证生成、被调度、首次拉OCR镜像和真实MaaS请求。

当前model18仍replicas=1，若旧worker6继续运行，新克隆加入不自动增加副本；需安排隔离测试部署或额外副本，不能将“新节点ready”误判成“新节点已拉取并运行OCR”。回收/新建VM由管理员操作，我们不执行VM级操作。

原有source bundle或image-settings.example.json仍可能是旧image_id配置，不应覆盖本次部署。重新安装应使用当前源码且包含registry.py，同时核对上述image_id和allowlist；本次没有制作新的Ubuntu镜像文件或重新生成公开交付归档。
