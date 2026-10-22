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

## 阶段二：运行流程迁移

Compose移除trusted导出挂载；新安装prepare_live要求运维显式提供已批准的--revision与--upstream-model，不再读取collector目录或改其配置。旧collector安装入口拒绝运行，systemd模板移至archive并使用.disabled后缀；旧P2故障矩阵/P3临时部署入口明确拒绝执行，历史行为保留在Git。

正式维护绑定、恢复和备份去掉collector等待，仍通过注册凭据及真实OCR探针后恢复。宿主监控保留容器、API、observer、磁盘、UNKNOWN与TLS检查，移除collector告警依赖。verify_live改为核验非root、无trusted挂载/管理密钥/Docker socket。旧目录字段可暂留作任务fingerprint兼容，运行时不再读取。

验证：候选e6-r1在独立maas_test运行194通过、1跳过，14.69秒；新增异步OCR无collector的完整API→领取→推理→结果下载及幂等唯一计量用例。正式服务器尚未切换版本。本地149通过、46项数据库相关跳过；脚本编译及diff空白检查通过。

## 阶段三：正式发布与资产清理

已在001发布e6-r1，三个应用去掉trusted挂载，保留原任务库/artifact卷和catalog字节；旧pin/preflight字段仅作为fingerprint兼容保留，不再读取。collector停止、禁用并永久mask；001运行目录中的配置、私钥、known_hosts、pin、health及consumer导出已清理，私有回退备份仍保存在001。002仅删除唯一匹配的collector forced-command授权行及inventory配置，其他authorized_keys逐行保持。原运维/维护访问与GPUStack内置采集保留。旧夜间timer禁用，原FAILED证据未修改。

真实私有入口验证：无collector文件/挂载/专用inventory私钥时，同步OCR与异步OCR均返回46行；异步同幂等键返回同任务，UNKNOWN=0，宿主监控无告警。报告见 [实机验收](../../results/maas-e6/live-acceptance.json) 与 [发布记录](../../results/maas-e6/deployment.json)。尚需新基线8小时；本次短测不代表P4通过。

### 回退材料与操作边界

001 e6-deployment.json记录私有rollback_directory：含旧source、app.env、catalog、maintenance及collector-assets和原service单元；002私有maas-inventory-retired含原authorized_keys与配置。不得上传或提交这些备份。回退前先暂停、确认无在途/UNKNOWN，恢复旧源码/Compose/配置与collector资产、解除mask并恢复单元及002唯一授权，再等待旧证明链路新鲜、运行真实探针后恢复。不能只切旧镜像。当前保留材料，未进行生产回退再发布演练；备份保留期由运维决定。
