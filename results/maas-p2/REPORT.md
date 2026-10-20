# P2 首批实机报告：统一任务 API 接入真实 OCR

2026-10-20。结论：固定版本 OCR 接入、两节点同内容镜像和未知执行隔离已跑通。P2 整体尚未验收。

## 交付

- `gpustack.ocr`：图片 artifact → GPUStack 鉴权入口 → 结构化 OCR 结果 → 控制端持久 artifact。
- 独立的版本目标、配置 pin、新鲜控制/镜像/运行容器清单；任务容器不持有管理 key。
- 派发前持久记录、未知活动隔离、人工解除及审计原因；客户端不自动重放远端调用。
- 绝对 HTTP 总时限、响应大小限制、JSON/schema 校验及原有项目隔离、幂等、租约 fencing。

## 验证

| 项目 | 实测 |
|---|---|
| 回归 | 64 passed、1 skipped；7.82 秒 |
| PostgreSQL 停机/恢复专项 | 单独执行，1 passed；对应全量中的 skip |
| 两节点专用镜像 | image ID 一致，实际运行容器也核对一致 |
| 真实图片任务 | 上传→202→SUCCEEDED→下载；46 行；attempt=1 |
| 幂等 | 同一 key 第二次返回 200，任务 ID 相同 |
| 跨项目访问 | 另一项目查询任务/输入 artifact 均返回 404 |
| 两种模型 | OCR 与 mock 使用同一套任务 API，不同输入 schema |
| 真实超时 | 只暂停两个专用测试 OCR 容器；返回 UNKNOWN_BACKEND_ACTIVITY，attempt=1 |
| 后续派发 | 未知活动存在时新任务保持 QUEUED |
| 隔离解除 | 恢复并停止专用容器，核对两节点无对应运行进程后人工解除；队列恢复 |
| 结果独立性 | 专用后端暂停及全部移除后，既有成功结果仍能下载 |
| 最终持久记录 | 4 个任务、3 条成功事件、0 条 UNKNOWN、1 条 RESOLVED |
| 原服务 | 原 OCR 两实例仍为 running；直接图片回归 200、46 行 |

专用镜像：`quay.io/cybercafe-maas-ocr:p2-r1`；内容 ID：`sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5`。原两节点 `0.3` 标签继续保持各自原内容，没有被覆盖。

单元及数据库用例另覆盖：快照过期、镜像/配置漂移、503、传输错误、畸形/过大响应、慢速分块响应总时限、取消时未知占用、执行器进程退出后的隔离和旧 attempt 拒绝。Starlette/AnyIO 有一条弃用警告，不影响结果。

## 试验观察与限制

- 首轮把专用模型缩容到 0 时网关返回明确拒绝，任务为 UPSTREAM_REJECTED；这不能证明超时隔离。随后改为仅暂停专用容器，真实请求超时后验证隔离，再停止专用进程解除隔离。
- v2.2.3 Higress 的 POST 重试未关闭。P0 同策略实测上限为单次客户请求触发 3 次后端调用；本轮核对专用路由策略一致。OCR connector 只发送一次客户端请求，派发后不会再应用重试，但不保证 GPU 恰好执行一次。
- 30 秒控制快照/60 秒镜像观察存在竞态窗口；管理员变更需暂停派发。当前本地临时 collector 只用于测试，不是常驻服务。
- 暂停专用模型容器不等于验证整台 worker 离线；未停止原 GPUStack worker、原 OCR 或整台服务器。
- 未完成真实 worker 离线/实例重建全矩阵、权限撤销/版本漂移在真实任务链路的完整故障矩阵、常驻 collector、P3/P4 发布与长时验收。

## 清理与下一步

测试 API、执行器、专用 GPUStack route/model/backend、临时用户/key、独立 acceptance 数据库、传输归档和临时 artifact 均已清理。专用镜像保留供后续复用。当前没有新增长期运行的任务端点；原同步 OCR 保持运行。

下一步优先完成常在线控制端的受信任 collector，并补 P2 的 worker 离线/实例重建及权限/漂移实机故障矩阵。操作说明见 [P2-OCR.md](../../docs/framework/P2-OCR.md)。

证据：`p2-smoke.json`、`pin.json`、`preflight.json`、`cleanup.json`、`p2-regression.log/.xml`、`p1-db-interruption.log/.xml`、`original-image-inventory.json`。
