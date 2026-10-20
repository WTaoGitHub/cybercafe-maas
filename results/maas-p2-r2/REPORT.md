# P2 收尾进展：常驻采集、权限与恢复验证

2026-10-20。当前可执行项已完成；**worker 离线测试按用户要求暂缓，P2 不标记为完整验收通过。**

## 本轮完成

| 项目 | 结果 |
|---|---|
| 控制端采集器 | XZ-31-001 的 `maas-collector.service` 已启用开机启动并持续刷新，不依赖本地电脑 |
| 002 清单通道 | 专用 SSH 私钥仅保留在001；固定来源地址、forced command、restrict、固定主机公钥；任意命令实测被拒绝 |
| 采集失败处理 | 立即删除旧预检文件；缺失或过期时拒绝新推理；控制面 socket 超时有界，避免无限挂起 |
| 模型权限撤销 | 排队后移除上游模型权限 → AUTH_DENIED，后端派发记录为0 |
| 本地绑定禁用 | 排队后禁用绑定 → AUTH_REVOKED，后端派发记录为0 |
| 上游 key 删除 | 接受任务后删除真实key → AUTH_DENIED，后端派发记录为0 |
| 真实部署漂移 | 修改专用模型env → 采集器撤销预检，任务被拒绝；pin未重写；恢复原配置后采集恢复 |
| 采集器停机 | 停止自己的采集服务，等待旧快照超过31秒 → PREFLIGHT_STALE，后端派发记录为0 |
| OCR实例重建 | 删除专用001模型容器后，GPUStack以新容器重建；直接OCR返回46行，新任务成功 |
| 历史结果 | 实例重建后既有结果仍能下载 |
| 恢复后全链路 | 再次通过上传→真实OCR任务→持久结果下载，46行 |
| 最终回归 | **82 passed、1 skipped、0 failures，7.89秒** |

skip 是需宿主机启停数据库的专项，本轮未重跑；前轮已有通过记录。本轮新增功能未修改数据库状态机。存在一条 Starlette/AnyIO 弃用警告。

## 实验影响与恢复记录

本轮 worker 离线实验出现了两次对现有环境的影响，不能描述成“原服务全程未受影响”：

1. **同一 Docker 引擎上的独立 worker 不安全。** GPUStack v2.2.3 的 WorkloadCleaner 枚举整个引擎的 workload，再按当前worker认识的实例判断孤儿。新测试worker删除了001原OCR容器。测试worker已停止并清理，原worker自动重建原OCR实例，随后两个节点都返回200、46行。
2. **同机追加 Docker daemon 也不能证明宿主机隔离。** 第二次尝试的daemon使用了独立数据目录和引擎ID，但仍共享宿主机网络与containerd；初始化后原docker0网桥消失，导致控制端发布端口不可达。测试daemon已停止，按原Docker网络配置恢复网桥、原MAC和4个端口，未重置服务数据库。已重新验证GPUStack版本接口、两节点OCR、常驻采集器、统一任务链路和新建bridge容器通信。

准确中断时长没有连续测量，不给出伪精确可用性数字。恢复证据见 `shared-engine-incident.json`、`bridge-recovery.json`、`final-verification.json` 和 `runtime-smoke.json`。

**后续不再在001/002上追加worker或daemon做离线注入。** 不安全实验脚本已撤除；新增只读环境检查，当前两个业务节点会被拒绝。用户已确认目前没有独立测试VM，worker离线实验暂缓。这不影响本轮权限、配置漂移和实例容器重建测试的有效性，但不能用后者冒充worker离线验收。

## 当前保留与清理

保留：

- 001常驻 `maas-collector.service`；root私密配置位于 `/root/wtaobj/maas-runtime`。
- 专用清单SSH通道；只允许读取限定的镜像及运行状态，不是通用root shell。
- `maas-ocr-p2-r2` 专用试点模型，两个节点各1个副本，与原 `rapidocr-gpu` 分开；这两个额外副本会占用GPU显存。

已清理：临时任务API/执行器、测试用户/key、验收数据库、临时artifact、额外worker/cluster、实验Docker daemon及其磁盘数据。**当前没有新增常驻任务API端点。** 原OCR两节点均ready/running，最终图片回归200、46行。

## 下一步

1. worker离线：等待独立、可销毁的测试VM；当前按用户要求暂缓。
2. 可以先准备P3的维护与运营设计：把maintenance、未知活动、同步旁路和“是否可停机”分开；缺少后端停止证明时不得自动确认安全停机。
3. 常驻任务API/执行器发布、HTTPS隔离、备份恢复、8小时耐久及Windows/Ubuntu切换仍属于后续交付，未在本轮完成。

实现说明：[P2常驻采集与操作手册](../../docs/framework/P2-COLLECTOR.md)。主要证据：`collector-bootstrap.json`、`fault-matrix.json`、`p2r2-regression.log/.xml`。
