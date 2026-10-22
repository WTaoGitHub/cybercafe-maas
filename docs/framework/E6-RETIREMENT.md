# E6：collector退役执行记录

2026-10-22。目标与完整验收见 [增强计划E6](ENHANCEMENTS.md#e6退役自建collector复用gpustack服务状态)。

## 阶段一：推理入口解耦

同步SyncIngress与异步OCRAdapter不再调用动态preflight，不读取pin/preflight文件，不执行SSH、Docker或全节点控制面核对。Settings仅要求网络适配器具备静态upstream_model，继续要求revision、运行时限及已批准适配器。授权、共享配额、暂停、发送活动账本、UNKNOWN及唯一计量路径不变。旧目录中的pin_file/preflight_file作为兼容字段暂时可保留，避免改变已入队任务fingerprint；不得据此声称仍逐请求核验镜像。

旧校验移至versioning.legacy_preflight，仅供历史审计/迁移测试。它不再属于推理依赖，也不提供生产节点健康判断。

## 依赖迁移清单

| 范围 | 待迁移对象 |
|---|---|
| 推理与配置 | ocr_connector、sync_ingress、Settings.catalog、Compose trusted挂载、目录旧字段与任务fingerprint |
| 安装与初始化 | prepare_live、install_collector、旧P2命令；新安装须显式批准静态目录，不启动collector |
| 维护与恢复 | maintenance_control、bind_maintenance、test_maintenance、backup_live中的collector等待；保留真实探针、generation与实际停止证据 |
| 监控与验收 | monitor_live、verify_live、fault_matrix、耐久测试相关脚本；保留任务/DB/磁盘/UNKNOWN检查 |
| 独占资产 | maas-collector.service、consumer导出、inventory私钥/known_hosts/agent及002专用authorized_keys；逐项证明独占后私有备份/清理 |
| 手册与自动化 | API、开发/运维/管理员/用户手册、P4运行说明、耐久跟进指令 |

阶段一验证后才能进入受控发布；线上停用与资产清理须在所有依赖迁移后执行。GPUStack内置采集、原运维SSH及维护forced-command授权保留。原night-8h-20261021继续FAILED；本次代码验证不能替代新基线完整8小时验收。
