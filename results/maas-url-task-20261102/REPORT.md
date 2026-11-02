# 异步远程图片URL输入：实现与隔离验收

2026-11-02。代码完成、验证通过，**未部署现网，未执行真实外部网络/GPU验收**。现网继续使用artifact_id/data_url；8副本等配置未改，无VM/worker/Docker引擎重启。

## 新能力

POST /maas/v1/tasks增加input.image.url，与artifact_id/data_url三选一。服务鉴权并校验模型/参数后，先查幂等及初步队列/存储，再安全下载PNG/JPEG并保存本地artifact，最终原子检查队列/配额并创建任务。worker只读取artifact，TaskView、查询/取消/下载及已有输入方式保持。

下载复用PublicImageFetcher的公网IP/DNS固定连接、TLS验证、禁重定向/私网/环境代理/凭据转发/HTTP压缩和大小控制，另加整体10秒预算。默认10MiB/20MP；URL长度≤4096，仅HTTP(S)80/443，URL任务JSON仍64KiB，内嵌base64仍单独20MiB。任务deadline从受理开始，不含下载准备等待；URL不保证内容永久不变。

## 幂等与资源保护

- 原请求指纹含URL，数据库任务仅保存artifact引用，不保存URL及签名查询参数。受理后同键原body直接返回旧任务，不再次下载；不同body为409/IDEMPOTENCY_CONFLICT。同URL的新key可取到不同内容。
- 无任务时通过PostgreSQL非阻塞session advisory locks保护准备阶段：同(project,key)只有一个下载，全局4个、每project2个。竞争返回409/IDEMPOTENCY_IN_PROGRESS或429/IMAGE_FETCH_CAPACITY，不排队持锁等待。常量为首版保守值，不与OCR执行并发等同，无容量性能承诺。
- 使用独立、autocommit连接跨网络持session锁，不开启跨下载事务，不持全局维护行锁或文件生命周期锁等待网络；退出/断连自动释放，无迁移、新表或常驻服务。
- 下载后重新取得凭据及模型配置，拒绝撤权/版本漂移，复用既有原子输入及任务受理；最终容量可能不同于预检，拒绝则不保存文件。无受理任务的下载失败后可用原key再次提交并重新下载，不是重复GPU推理。
- 下载超时408/IMAGE_FETCH_TIMEOUT，下载器其余失败遵循已有安全错误。未受理响应无任务ID；并发准备中的409不是内容冲突，调用方应检查error.code。

## 测试

- 本地75项通过：新增三来源互斥、URL长度、整体下载超时与锁key命名空间；同步下载SSRF/DNS重绑定、TLS主机名、重定向/头/大小限制及既有内嵌OCR/连接器回归。
- 独立PostgreSQL+API/mock推理：103通过，1条件跳过（已有host侧数据库中断harness未提供）。本地/隔离有重叠，不相加作为独立用例数。
- URL→本地artifact→mock推理→任务成功/唯一计量→下载通过；同键重复不重下载，冲突不重下载；任务持久输入中无URL/签名查询字符串。
- 私网、非法图片、下载失败/超时、字节、初始队列、最终配额和模型权限拒绝均无新任务或文件。
- 同键正在下载返回特定409；跨进程锁协调与退出后释放；全局4/项目2容量边界及恢复通过。
- 下载阻塞期间维护状态切换及上传仍能完成，无全局/文件锁网络等待；最终队列满、配额不足、权限撤销、模型版本变化均拒绝受理。
- 既有artifact/data_url、提交确认丢失、孤儿GC和同步OCR回归通过。未做生产容量、长测或真实公网URL稳定性测试。
- OpenAPI3.1/9操作路由/JSON-YAML一致校验；三种输入示例及curl解析通过。

## 交付与后续

功能代码maas_platform/task_image_url.py、ocr_connector.py及service.py；复用原下载器，没有修改其安全规则。OpenAPI、API说明及接力文档标注URL输入待部署，不能把候选schema视为现网支持。隔离原始日志已存Mac私有目录，不入Git。

后续受控发布需先核验并备份现网镜像/源码/env，保持8副本profile及现网已部署data_url兼容，在独立可控外部图片源上验收下载/重定向/失败、幂等、并发上限及真实OCR后再标上线；不开放私网下载，不自动启动新负载、部署或解除UNKNOWN。

[设计](../../docs/framework/URL-TASK-IMAGE.md) · [OpenAPI](../../docs/api/openapi.yaml) · [内嵌图片发布](../maas-inline-deployment-20261102/REPORT.md)
