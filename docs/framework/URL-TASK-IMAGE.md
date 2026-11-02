# 异步远程图片URL输入

> **2026-11-02当前已发布ocr-metering-r2**：规范字段image_base64/image_url、旧别名兼容、异步远程URL及E16两个计量查询均已完成真实验收。4异步+3同步成功，7条唯一收据；当前tokens实际null/unavailable，不能按tokens实际收费。generation488、监控健康、UNKNOWN=0，8副本profile保持，无VM/worker/Docker引擎重启。OpenAPI已内嵌真实脱敏响应；以下待部署状态为历史。[发布报告](../../results/maas-ocr-release-20261102/REPORT.md)。


> **2026-11-02异步图片字段统一（待部署）**：新代码将data_url改为image_base64、url改为image_url，保持input.image层级；image_base64接受裸base64或PNG/JPEG data URL。旧字段保留弃用别名兼容，但同次重试不可换字段。76本地/105隔离通过（1旧条件跳过），现网不变。[说明](TASK-IMAGE-FIELDS.md)。


> **2026-11-02异步远程URL代码完成，待部署**：input.image.url与已上线artifact_id/data_url三选一，下载并保存后才202；同键不重下载、跨进程下载槽位全局4/项目2、整体10秒，复用公网安全限制。75本地、103隔离通过（1旧条件跳过），未变更现网。[验收](../../results/maas-url-task-20261102/REPORT.md)。


2026-11-02，用户授权实现，E14图片OCR首阶段增量。先完成本地/隔离测试，不自动部署。

新增input.image.url，与artifact_id/data_url三选一，仅gpustack.ocr使用。接受公开HTTP(S) PNG/JPEG，复用同步下载器：限制端口80/443，检查所有DNS地址并固定已校验IP连接、保留原TLS主机名验证，不跟随重定向、不转发凭据/Cookie、不使用环境代理，不接受HTTP压缩；默认10MiB/20MP。下载整体上限10秒，超过返回408/IMAGE_FETCH_TIMEOUT，失败不创建任务。

提交先鉴权、模型/参数/请求schema检查和幂等查找，初步检查队列/存储；再取得跨API进程的短期下载准入：PostgreSQL session advisory locks，每个(project,Idempotency-Key)仅1个下载，全局最多4、每project最多2。锁均非阻塞，竞争同key返回409/IDEMPOTENCY_IN_PROGRESS，容量满429/IMAGE_FETCH_CAPACITY；不占全局维护行锁、文件生命周期锁等待网络。持锁连接断开时锁自动释放，不新增常驻服务/数据库表。锁只协调此URL路径。

下载后核验图片，重新鉴权及核对模型版本配置，进入现有文件/任务原子受理流程，再查幂等/队列/配额并保存artifact。数据库任务input只存本地artifact引用，不存URL/签名查询字符串。原始请求指纹仍含原URL；同key已受理时直接返回旧任务、不再下载，即使外部URL已变化/失效。相同URL的新key为新操作，可取得新内容；不保证并发不同key内容一致。不把URL带到worker，不排队后再下载。

下载失败没有任务ID；未受理时使用原key再次提交会重新下载，不是GPU推理重放。接受后才开始任务deadline（保持现有语义），整体HTTP等待含额外下载时间。429拒绝之后排队容量可能变化，最终原子准入为准；无等待队列容量预留。

测试须覆盖旧输入/新URL联合schema、SSRF与下载器安全回归、已受理不重下载/冲突、并发同key不重复下载、跨进程下载上限、网络等待不阻塞全局维护/文件锁、下载后队列/配额/权限/目录漂移、失败无文件/任务、结果与唯一计量。OpenAPI明确未部署、示例不指向真实用户资源。
