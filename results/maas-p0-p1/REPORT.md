# MaaS任务层第一批实现与验证

日期：2026-10-20。范围：**P0基线/鉴权探针 + P1模拟后端持久闭环**。

## 已实现

- 独立Python 3.11包maas_platform；FastAPI任务/输入上传/结果查询/取消/能力接口，OpenAPI已导出。
- PostgreSQL持久任务队列、带校验和及advisory lock的SQL迁移；不访问GPUStack数据库。
- project+Idempotency-Key并发幂等，同键不同请求409；项目级任务及artifact授权。
- 执行器领取、租约续期、过期恢复、attempt token条件提交、有限重试、任务截止、运行取消及唯一成功计量。
- 控制端文件输入/结果持久化，fsync/原子提交、大小与摘要验证、活动输入引用保护、结果TTL。
- 有界排队/全局与project并发槽、受保护secret文件、请求体限制、私有pause/resume/status CLI。
- Dockerfile、Compose部署模板、模拟模型能力/登记模板、版本固定依赖和测试环境依赖锁。

首期只启用mock.echo；任务执行不消耗GPU，未把新任务API接入真实OCR推理。任务身份确实复用了真实GPUStack key。

当前001没有Docker Compose插件；本轮测试使用独立docker run。Compose文件为后续部署模板，不视为已验收的部署入口。

## 现网基线与鉴权实测

证据：maas-baseline.json、maas-auth-probe.json。

- `/version`返回v2.2.3、git_commit=1cfcbbf，与本地tag基线前缀一致。
- 原rapidocr-gpu为backend0.3，配置两副本、ready两副本；generic_proxy=false。
- 临时创建非管理员身份并签发inference-only key：认证/允许模型200，管理接口403。
- key模型白名单不包含现有OCR时，真实模型鉴权返回403；无效key401；伪造auth缓存不能让无效key通过。
- 删除key后立即重新请求返回401；这是本次配置下的观测，不承诺所有缓存/负载条件下相同传播时间。
- 所有临时用户/key已删除，原模型、backend及route配置未改变。

P0剩余：原生generic proxy未启用/未验收；实际Higress入口及透明POST重试、OCR/generic用量覆盖仍待进一步核对。多组织授权完整矩阵尚未验收，不能据此开放公网多租户。

## PostgreSQL集成测试

15项通过，证据：test-results.log、test-results.xml。

覆盖：上传→任务→持久结果；API对象重建；8个并发相同提交只生成一个新任务；内容冲突409；project隔离/拒绝身份伪造；上游撤销和本地禁用；旧租约/旧attempt不能提交；取消；有限重试和耗尽；全局pause；配额；deadline；输入引用保护和过期410；模型revision变化拒绝；文件摘要损坏503；并发槽；上传/JSON体积与参数限制；低磁盘拒绝且不虚报成功。

这组测试使用**真实PostgreSQL**，鉴权上游使用HTTP MockTransport注入可控失败；两project隔离验证的是任务层映射，不代表已完成GPUStack多组织端到端验收。数据库名要求以_test结尾，测试会清空专用测试库public schema。

## 独立进程端到端验证

证据：smoke-live.json、openapi.json。

使用真实GPUStack临时非管理员key、真实HTTP任务API、独立执行器进程与PostgreSQL：

1. 上传输入并提交任务，收到202。
2. 在执行前重启API，任务仍为QUEUED。
3. 启动执行器，任务进入RUNNING后强制SIGKILL执行器。
4. 启动新执行器，等待原30秒租约到期后回收，第二次attempt完成。
5. 查询为SUCCEEDED，持久结果可以通过鉴权下载。

临时API/worker容器及用户/key已清理；不作为常驻线上服务。独立测试数据库容器测试后停止，持久数据/私有env仍留在/root/wtaobj/maas-framework供复现；没有把实际secret复制到本地报告或提交。

## 明确未交付

- P2真实OCR异步connector、模型目标指纹预检、网络失联后的未知后端占用隔离。
- P3完整全局排空、同步/Playground旁路限制、告警及孤儿文件自动巡检。
- P4公网TLS、生产secret加密管理、生产备份恢复和8小时负载。
- 精确逐节点drain、自动Windows/Ubuntu切换、计费账本及控制端高可用。

现有pause仅暂停任务派发，不能作为安全关机证明。当前secret采用控制端600文件而非独立加密凭据库。结果文件支持TTL；未登记孤儿文件保留供诊断，自动回收待P3。

## 复现入口

[运行说明](../../deploy/maas/README.md)、[实现代码](../../maas_platform/)、[测试](../../tests/maas/test_integration.py)。

requirements-maas.txt固定直接依赖；requirements-maas-tested-lock.txt记录已测试Python3.11/Linux环境的全部实际依赖。测试容器Python为3.11-slim，PostgreSQL为16-alpine；原GPUStack与OCR镜像未更新。

下一步优先补完P0剩余探针，再进入P2真实OCR异步接入；不把模拟闭环当作所有路线图阶段完成。
