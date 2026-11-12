# 旧 worker 自动退役插件移除

2026-11-12，用户明确要求立即彻底移除旧自动退役插件，并授权重建 GPUStack 容器和重启服务。相关源码保留，但不适用于当前默认 GPU UUID 注册方式，不予部署。关联 [Issue #34](https://github.com/WTaoGitHub/cybercafe-maas/issues/34)。

## 完成结果

- 从运行文件系统卸载 `maas-gpustack-worker-retirement`，去除模块、发行包元数据和 `gpustack.plugins` 入口。
- 生成新的部署镜像 `cybercafe-gpustack-no-retirement:20261112`，采用清理后的文件系统和不含退役环境变量的镜像配置；重建当前 GPUStack 容器并删除其旧运行容器。
- 镜像配置、当前容器配置及实际进程环境均无 `MAAS_WORKER_RETIREMENT_*` 设置。容器内确认发行包、两个退役模块和插件入口均不存在。
- enrollment 与 handoff 模块保留。健康接口返回 200，原有 worker 身份行保持一致，worker 4、5 均为 Ready、集群 1；数据挂载和网络地址保持原值。
- 当前部署镜像已记录到新 PC 私有运行清单，数据库及容器配置备份保持 root 私有。历史备份不作为默认部署指令。

操作中两次校验触发了预设回退：首次健康检查使用了错误的宿主机端口；随后发现镜像会继承旧退役环境变量。纠正检查入口并生成不带继承项的镜像后，最终切换和所有检查通过。未执行操作系统或 VM 重启，没有删除 worker 记录。

## 部署约定

根目录 AGENTS.md、实体机部署说明及旧插件 README 已明确：当前默认以 GPU UUID 作为 worker name 和 external_id，旧后台自动退役插件不得打包、安装或部署。插件源代码及历史安装说明仅作归档参考；预登记、接续和显式永久退役入口继续保留。

核验见 [verification.json](verification.json)。公开记录不含真实连接信息、凭据或私有数据库备份。
