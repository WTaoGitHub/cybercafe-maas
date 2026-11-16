# Unlimited-OCR-2 执行故障登记

2026-11-16 · [执行计划](UNLIMITED-OCR-2-PLAN.md) · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)

执行故障统一编号为 `UOCR2-INC-<三位序号>`。`UOCR2` 标识工作流，`INC` 标识执行故障，末尾序号在本工作流内递增。增强列表继续使用原有 `E<number>`，例如本工作流所属增强项为 E10；GitHub Issue 编号独立维护。

编号迁移保留故障顺序、处理次数及当时的验证结论和停止状态；后续接续在同一登记中追加，不覆盖历史。正文、接力记录和 Issue 引用均使用完整新编号；旧编号只用于下表回查。历史 Git 提交、提交链接及私有原始证据保留。

| 旧执行编号 | 新编号 | 故障 | 当前结论与证据 |
| --- | --- | --- | --- |
| E01 | `UOCR2-INC-001` | 旧 SSH MCP inventory 挂起 | 已改用受信替代路径，见[首轮记录](../../results/unlimited-ocr-2/20261116-p1/REPORT.md) |
| E02 | `UOCR2-INC-002` | legacy Docker builder 不支持部分命令参数 | 已纠正参数，见[首轮记录](../../results/unlimited-ocr-2/20261116-p1/REPORT.md) |
| E03 | `UOCR2-INC-003` | 固定 CUDA 基础镜像获取受阻 | 首轮按上限停止，后经用户授权重试解决，见[交付证据](../../results/unlimited-ocr-2/20261116-base-image/REPORT.md) |
| E04 | `UOCR2-INC-004` | 缺少 Python 开发头文件，Triton warmup 编译失败 | 修复后 GPU JIT、模型 warmup 和真实解析通过，见[P2](../../results/unlimited-ocr-2/20261116-p2/REPORT.md) |
| E05 | `UOCR2-INC-005` | GPUStack 自定义后端缺少 CPU/RAM 硬限额入口 | 试点资源守卫及实际 cgroup 已验证；无人值守恢复未闭环，见[运行限额](../../results/unlimited-ocr-2/20261116-p3-image-authority/REPORT.md) |
| E06 | `UOCR2-INC-006` | 权限核验辅助脚本报错及授权范围确认 | 核验完成；用户指定的 All models 保留，见[当时快照](../../results/unlimited-ocr-2/20261116-p3-auth/REPORT.md)和[最终状态](../../results/unlimited-ocr-2/20261116-uocr2-inc-008-stop/REPORT.md) |
| E07 | `UOCR2-INC-007` | 后端镜像 registry 地址不适用于 worker | 更正后实际 GPUStack 拉取、启动通过，见[镜像地址证据](../../results/unlimited-ocr-2/20261116-p3-image-authority/REPORT.md) |
| E08 | `UOCR2-INC-008` | Higress 非幂等请求重试及不重放验证未完成 | 历史两次禁重试覆盖未通过，见[停止检查点](../../results/unlimited-ocr-2/20261116-uocr2-inc-008-stop/REPORT.md)；经用户重新授权，本轮按既有P0契约实测受限策略并完成[M1](../../results/unlimited-ocr-2/20261116-m1/REPORT.md)，未进行第三次覆盖 |
| — | `UOCR2-INC-009` | 采样脚本在后台接收stdin时写出空文件 | 子代理一次修正为先传完整脚本再后台启动；采样不覆盖初始化及10页前段，不能据此报告完整峰值 |
| — | `UOCR2-INC-010` | SSE客户端断开后跨窗口KV残留1280 tokens | 子代理有限等待未释放；主代理第1次候选清理补丁单测通过但真实断连仍失败。第2次按M1范围强制JSON-only，流式拒绝、正常1/2/10/11页、短超时后资源归零及文档隔离通过；M1契约下规避，SSE故障未修复，不继续第3次处理 |
| — | `UOCR2-INC-011` | CPU探针后端名缺少`-custom` | 路由层400、无对象创建；主代理一次修正后创建成功，见[M1](../../results/unlimited-ocr-2/20261116-m1/REPORT.md) |
| — | `UOCR2-INC-012` | 辅助脚本固定17条路径不适用于generic路由的19条 | 主代理一次改为动态核对，实际retry_policy与试点相同；不是模型故障 |
| — | `UOCR2-INC-013` | CPU探针仅按Content-Length读取，未支持实际chunked请求 | 初次探针400拒绝，不作为重试计数；主代理一次补有界分块读取，本机真实chunked自检及现场12次请求验证通过，探针对象和容器已清理 |
| — | `UOCR2-INC-014` | M2离线构建辅助脚本错误替换变量，随后BuildKit将裸image ID当作仓库名 | 主代理第1次修正生成脚本语法，第2次为已核验本地镜像创建固定tag并禁用pull；断网构建成功，PDFium wheel哈希及pip check通过。未修改旧服务或Docker守护进程 |
| — | `UOCR2-INC-015` | M2测试辅助脚本未使用现有PostgreSQL端口 | 首次连接默认5432被拒，未创建或修改数据库；主代理一次读取实际DSN端口后仅创建新的`_test`数据库，执行隔离测试 |
| — | `UOCR2-INC-016` | 扩展回归收集依赖的maas_bench未挂载 | 主代理一次补挂仓库既有测试依赖后，165项通过、1项跳过；一项既有同步历史测试仍请求旧根路径/ocr导致404，已在[#25](https://github.com/WTaoGitHub/cybercafe-maas/issues/25)另行记录，不冒充模型故障或全套通过 |
| — | `UOCR2-INC-017` | PDF查看辅助脚本将PdfPage误作context manager | 一次改为显式close后，15份PDF重新打开、页数核对及渲染查看完成；未影响部署代码 |
| — | `UOCR2-INC-018` | 实际下游只有curl，无Python/jq | 推理前停止；一次改由新PC编排、每次HTTP仍在实际下游执行curl，15份/35页完整链路通过；交付无Python的curl步骤，不在下游安装工具 |
| — | `UOCR2-INC-019` | M2中文场景区域精确召回42.1%，低于冻结60%门槛 | 主代理第1次原始JPEG绕过PDF、第2次167DPI候选均为26.3%，未解决，按两次上限停止。新入口关闭提交、独立执行器暂停且排空，历史查询保留；不进入容量加压或M2发布，见[M2停止报告](../../results/unlimited-ocr-2/20261116-m2/REPORT.md) |

2026-11-16后续：用户明确要求“忽略 中文场景的质量未达标问题。继续推进。”本故障及两次处理额度保持原记录，质量未修复；按用户豁免解除其发布阻断后完成[M2受控交付](../../results/unlimited-ocr-2/20261116-m2-release/REPORT.md)。没有第三次质量修复或将失败成绩重判通过。

`UOCR2-INC-020`：收尾离线页关联检查器初版只保留至少6字符的锚点，将某页全部5字符参考文本过滤后误报。一次修正为长锚点为空时允许至少4字符的短锚点，22份/68页既有结果关联通过；未发送新GPU请求，未改变模型、任务、原质量指标或豁免。见[M2报告](../../results/unlimited-ocr-2/20261116-m2-release/REPORT.md)。
