# MaaS E2E框架与真实验收报告

日期：2026-10-27。基线：API consumer-ocr-r1、执行器/Observer e6-r1、GPUStack v2.2.3。本次交付框架、65项模块化用例、可复用双语PNG/JPEG样本与运行手册；未修改被测业务实现或部署配置。

## 结果

| 执行 | PASS | FAIL | ERROR | SKIP | 证据 |
|---|---:|---:|---:|---:|---|
| 私有TLS全目录 | 56 | 0 | 0 | 9 | [JSON](live-tls.json)、[JUnit](live-tls-junit.xml) |
| HTTP按ID+模块+label交集，只读严格子集 | 2 | 0 | 0 | 0 | [JSON](http-selection.json)、[JUnit](http-selection-junit.xml) |
| HTTP超限上传，显式write+load | 1 | 0 | 0 | 0 | [JSON](http-boundary.json)、[JUnit](http-boundary-junit.xml) |

去重后，**65项中57项已在真实服务验证通过，8项仍待专用前置条件验证**。不能将三个run的PASS简单相加当作不同用例数；后两个run是有明确目的的新子集，不是失败重试。

框架及夹具本地自测：**18 passed**。既有回归也执行：MaaS+当时17项runner自测合计203 passed、56 skipped；bootstrap独立执行67 passed。前述56项本地pytest跳过需要独立PostgreSQL环境，不是本次黑盒E2E的SKIP。后续新增夹具哈希/目录一致性测试单独包含在最终18项框架自测中，不混加这些重叠计数。

一次合并收集maas/bootstrap时发现既有同名test_boundary.py导致pytest模块名冲突，改为不同进程运行目录，未修改原测试或删除缓存。

## 实际验证范围

- 主身份为customer-a，第二身份属于pilot-ocr：验证缺key、无效key、有效key、身份头不能绕过认证，以及跨项目文件/任务/结果隔离。
- 真实TLS通过MaaS私有证书验证；临时SSH转发只在Mac创建并在run结束关闭。HTTP子集也经临时SSH转发，不开放公网或新LAN监听。
- 使用仓库附带同字节双语图片的PNG/JPEG形式，实际识别MAAS和中文片段；兼容同步base64/data URL、v1别名、return_boxes=false、公开HTTPS图片URL和空白图通过。
- 原Chat包络及嵌套OCR JSON通过；输入字段、来源互斥、严格boolean、不支持语言、错误图片、媒体不符、超像素、私有URL等拒绝分支通过。
- 文件上传后size/sha256与下载原字节一致，下载隐私头有效；空文件、媒体类型、超10MiB上传拒绝通过。
- 异步上传→提交202→查询→SUCCEEDED→结果下载通过；同键同体复用200、不同内容409、无幂等键、坏schema、缺输入、合法取消竞态通过。
- 针对本次已成功任务ID，MaaS数据库READ ONLY检查成功事件恰好一条。未使用全局计数差值冒充当前请求的唯一计量。
- 客户入口关闭health/docs/OpenAPI/原生模型列表及未实现的生成/批次路径；query限制、请求ID和错误包络通过。
- 001控制组件、monitor新鲜与UNKNOWN=0、collector仍inactive/masked、E5配置和仓库进程运行状态通过。不是clone首启或worker网络ACL专项验证。

私有TLSrun显式新建10个输入artifact、6个任务；其中任务取消只作用于本次记录的ID。输入/结果保留在服务端按现有TTL清理，任务/成功用量按现有策略保留。成功同步响应7次；没有自动重发推理、没有新增商业扣费逻辑。

所有run结束时已知pending_tasks为空、inference_blocked=false。报告只记录请求状态/关联ID、任务/文件/幂等ID、统计和观测，不含API key、图片base64、OCR全文或数据库连接串。发布证据已检查不包含两把真实客户key。

## 未验证的8项

| ID | 缺少条件 |
|---|---|
| AUTH-005 | 可认证但禁止访问OCR的专用受限key |
| TTL-001 | 当前测试项目真实过期/已清理artifact ID |
| MAINT-001、MAINT-002 | 运维独立准备的维护暂停窗口 |
| QUOTA-001、QUOTA-002 | 独立受控环境准备的容量/队列满额窗口 |
| METER-002、METER-003 | 确认独占的测试项目，避免同步计数差值混入他人请求 |

TLS首次全目录中的第9项SKIP为ART-006大请求边界，之后已通过单独HTTP子集实测补齐。因此最终剩8项，不是9项。

框架不主动制造这些前置状态，不改TTL/队列限额、不创建或撤销客户key、不暂停服务，也不灌满线上队列。已有本地集成测试覆盖部分异常语义，但本地测试不能替代上述真实环境验收。

## 使用与边界

- [运行手册](../../e2e/README.md)
- [65项用例目录](../../e2e/CATALOG.md)
- [示例配置](../../e2e/config.example.json)
- [双语夹具及哈希](../../e2e/data/manifest.json)
- [去重汇总](summary.json)

没有执行VM/OS重启、镜像恢复、克隆创建、服务/容器重启、部署切换、维护切换、故障注入、collector恢复、8小时耐久或千节点压力测试。现有001/002不重启约束持续适用。新的服务级凭据架构、批处理、并发执行器和GPUStack升级也不属于本轮实现。
