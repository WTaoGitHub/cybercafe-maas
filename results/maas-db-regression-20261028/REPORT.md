# 56项数据库集成测试补测

2026-10-28，用户明确确认无其他测试占用并授权复用001测试库。**此前跳过的56项全部补测通过：55项常规集成 + 1项真实数据库中断专项，0失败、0错误、0跳过。**

| 测试组 | 结果 | 时间 | 证据 |
|---|---|---|---|
| 常规数据库与OCR兼容接口集成 | 55 passed，故障专项1项另行deselected | 15.33秒 | integration.log / integration.xml |
| 真实PostgreSQL中断/恢复 | 1 passed | 4.04秒 | db-interruption.log / db-interruption.xml |

## 对象和隔离

使用既有`wta-maas-p1-test-db`（postgres:16-alpine，role=p1-disposable-test），仅回环18834，目标库`maas_test`。先核验容器ID、role、精确回环映射、连接URL的host/port/dbname及无其他测试连接。测试fixture只清空并重建该测试库public schema；没有连接正式任务库或GPUStack库，也没有清理`maas_smoke_test`。

测试在001单独的临时Docker容器运行，使用已有`cybercafe-maas-tasks:consumer-ocr-r1`镜像提供Python依赖，挂载本地仓库当前源码的独立快照`/root/wtaobj/maas-test-runs/db-regression-20261028`，PYTHONPATH=/work；没有覆盖正式代码目录、重建/替换业务容器或安装新软件。仅传入专用测试数据库env；推理和上游HTTP通过测试mock模拟，不代表这56项运行了真实GPU OCR。运行镜像ID、Git基线及源码哈希见summary.json。

常规测试按两文件顺序运行，排除真实中断专项；专项单独运行，避免停库波及同批其他测试。专项通过ready/stopped/observed/restarted握手，仅stop/start已核验ID的测试数据库容器，确认临时不可用时查询/提交返回503，恢复后任务成功、过期执行不能覆盖结果、成功计量唯一。没有VM重启、Docker引擎重启或业务数据库停止。

## 首轮执行方式调整

最初在Mac通过SSH转发连接测试库，因跨网络多次数据库往返，180秒超时，未完成全部用例，不能计为通过。保留于initial-mac-tunnel-attempt。随后切换为001临时容器就近连接，完整补测通过；正式结果为上表及两份JUnit，不把首次超时隐藏或当作产品故障修复。SSH转发已关闭，临时测试运行容器使用--rm退出。

## 补测后状态

见before.json、after.json：001 boot ID及所有原先运行的容器ID保留；测试数据库同一容器恢复running；MaaS未暂停，活动/UNKNOWN为空，monitor ok、alerts=[]。正式计量未被mock测试写入。

额外观察：model18当前**期望4、实际4 running**，与此前维护演练14副本基线不同。本轮未调用GPUStack配置写接口，不自动将其改回14；未取得变更时点/操作者，不能归因。已有schema-v2维护绑定批准值仍14，下一次begin会因配置漂移拒绝执行；运维须确认实际目标后再audit/bind，不绕过保护。

本次关闭的是此前56项数据库测试跳过缺口，不扩大为新的8小时耐久、E1逐worker维护、VM重启、真实GPU负载或千节点验收。
