# E3调优工具操作说明

2026-10-29。T0工具开发与独立数据库验证；不代表新的OCR性能结论。工具是仓库中的`maas_bench`包，未加入正式应用镜像或常驻服务，没有修改正式副本、额度、路由或认证。

## 选择测试

在仓库根目录使用`.venv-maas/bin/python`；控制端使用包含httpx/psycopg的独立工具环境。

```shell
python3 -m venv /private/path/to/tool-env
/private/path/to/tool-env/bin/python -m pip install -r requirements-bench.txt
```

专用环境只需httpx/psycopg，依赖沿用已测版本锁，不需要安装FastAPI、OCR模型或向系统Python装包。离线部署可先按控制端Python/CPU平台下载锁定wheel，再用`--no-index --find-links /private/wheels`安装。2026-10-29已在独立测试目录创建专用环境，并完成真实只读运行时/两节点原生指标和独立测试库只读连接检查；正式应用目录/镜像和系统Python均未改变。

```shell
.venv-maas/bin/python -m maas_bench plan
.venv-maas/bin/python -m maas_bench plan --case-id T0-BASELINE
.venv-maas/bin/python -m maas_bench plan --module native-capacity --label gpu
.venv-maas/bin/python -m maas_bench plan --case-id T1-NATIVE --case-id T2-ASYNC
.venv-maas/bin/python -m maas_bench plan --module http-ingress
.venv-maas/bin/python -m maas_bench plan --case-id T4-HTTP-REJECT
```

同类选择条件取并集，不同类条件取交集。未知条件直接拒绝。输出包括前置依赖、3个基线重复、12个原生粗筛点和9个异步粗筛点；没有证据的依赖标记BLOCKED。条件扩展必须明确执行或记录`SKIPPED_CONDITION_NOT_MET`，不能把所有跳过都当作通过。

`plan`只规划，不连接服务、不改配置。T3以后的候选必须依据前阶段证据选择，不会自动填成“最佳”。矩阵不是一键执行所有阶段的生产调度器。

T1现提供专用`native_tuning_matrix.py --config <private.json> --execute`，持有维护锁并排空后测量完整12点，正常完成恢复原配置/真实探针，故障保留暂停现场。T2提供`async_tuning_matrix.py --config <private.json> --execute`，复用受控副本/执行器变更，运行9个有持续供给的异步点，逐图片比对固定文本基线并在结束恢复原值。两者缺少`--execute`只做dry-run；依赖哈希证据、Mac备份和动态运行快照仍须满足。具体实测进度见[执行记录](../../results/maas-e3-tune-20261029/REPORT.md)，不能把驱动可用当作所有点通过。

2026-10-29新增的三个T4-HTTP用例只支持规划选择，专用HTTP驱动与隔离API环境待实施；当前`run-async`不能执行。HTTP请求并发与执行器/槽位分开，32/64只作为条件候选，未扩大现有16工具边界。设计见[HTTP接入专项](E3-HTTP-INGRESS-PLAN.md)。

## 异步单点运行

`run-async`支持已经设置好参数的T0-BASELINE、T2-ASYNC单点；每个重复使用新run_id和独立目录。不在该命令内扩缩容、改额度、恢复维护或发布推荐值。扩缩/恢复继续使用已有带维护锁和真实探针的控制工具，先拿实际运行快照，不能假设原来就是4副本。

执行之前：

1. 准备明确的模型profile、固定镜像/权重与样本摘要，确认两卡身份、版本、方向策略和模型算法参数。清除不属于本实验的活动流量；不会替用户取消其他任务。
2. **先在Mac私有目录备份**完整模型配置、app.env及维护绑定，记录备份清单的SHA256。此回执是操作前置声明，工具不能凭一个哈希证明备份真的存在或可恢复。
3. 准备本项目已上传的40图清单，文件权限600。OCR格式为`{"fixtures":[{"input":{"image":{"artifact_id":"art_example"}},"sha256":"原图片SHA256","parameters":{}}]}`，必须匹配capabilities公布的schema。可附样本分类/版本等元数据，它们也进入摘要。执行前读取每个输入并核验字节校验和；输入准备与预热应在正式测量外完成。
4. 生成私有运行配置及运行时快照（下述capture只读）。检查配置与快照中的R/E/G/P/Q/M完全一致。参数改变必须重新capture。
5. 前置阶段证据须为JSON，包含`case_id`、`state=COMPLETE`；非T0-TOOLS证据还需相同`profile_id`、`input_sha256`。配置`completed_stages`引用证据路径和文件SHA256；不能仅填一个布尔值。T0工具证据见本项目验收记录。

私有配置示例（**需按实际私有路径填写，不直接执行占位值**）：

```json
{
  "case_id": "T0-BASELINE",
  "point_id": "T0-BASELINE-r1",
  "run_id": "baseline-r1-new-id",
  "profile_id": "ppocrv4-ortgpu-2x4080s-r1",
  "source_root": "/private/path/to/tool-source",
  "model_id": 18,
  "logical_model": "ocr.default",
  "revision": "ocr-registry-r1",
  "project": "your-validation-project",
  "base_url": "https://your-private-endpoint",
  "ca_file": "/private/path/to/pinned-certificate.pem",
  "customer_key_file": "/private/path/to/registered-customer-key",
  "database_dsn_file": "/private/path/to/read-only-database-dsn",
  "fixture_file": "/private/path/to/preuploaded-inputs.json",
  "snapshot_file": "/private/path/to/runtime-before.json",
  "output_parent": "/private/path/to/runs",
  "private_mac_backup_receipt_sha256": "64位备份回执SHA256",
  "parameters": {
    "replicas": 4, "executors": 4, "global_limit": 4, "project_limit": 4,
    "global_queue": 1000, "project_queue": 100, "vram_claim_bytes": 2147483648
  },
  "policy": {
    "seconds": 60, "min_successes": 200, "max_seconds": 300,
    "sample_seconds": 0.05, "max_sample_gap": 0.25, "submit_clients": 4
  },
  "completed_stages": {
    "T0-TOOLS": {
      "state": "COMPLETE", "proof_file": "/private/path/to/tool-validation.json",
      "proof_sha256": "64位证据文件SHA256"
    }
  }
}
```

配置、快照、DSN、key均为普通私有文件（600）；输出父目录700。证书是公钥材料，但不应把私有运行配置上传Git。数据库观察连接强制只读、单条查询超时200ms、持久复用，不迁移表、不逐次启动psql、不经SSH采集worker。生产账号仍建议具有最小只读权限。

```shell
python -m maas_bench capture --config /private/run.json --out /private/runtime-before.json
python -m maas_bench run-async --config /private/run.json
# 上一条仅DRY_RUN；确认前置后才显式执行单点：
python -m maas_bench run-async --config /private/run.json --case-id T0-BASELINE --execute
```

capture在控制端本地复用GPUStack管理API、原生状态及现有应用状态命令，读取实时副本、实例、worker/GPU身份、驱动/显存容量、容器与额度、catalog和环境摘要。测量时独立线程检查这些固定条件和原生资源指标；不会用慢速状态查询堵塞队列采样。不新建collector，不重启任何VM、worker或Docker引擎。

## 读结果及失败处置

- 先预填至低/高水位，约为`2I/4I`（`I=min(E,G,P)`），按Q裁剪并计入尚未确认的提交预算。结果下载不参与补量。请求不重试，任务max_attempts=1；每个POST前持久记录intent，响应不明确时保留intent并停下，不猜测后重发。
- 正式测量取边界数据库快照中已提交成功ID之差，至少60秒且至少200次成功。尾部排空及下载独立统计，不能补进测量分子。采样同时核对G/P/Q、generation、UNKNOWN及计量；纯异步点混入外部活动即停止。
- 断粮为`INVALID_UNDERSUPPLIED`；采样空窗为`INCONCLUSIVE_SUPPLY_EVIDENCE`；异常、计量/身份/配置漂移为`INTERRUPTED_REQUIRES_INSPECTION`。供给不足的结果不进入容量排名，不截取忙碌窗口，不自动重开或恢复。不覆盖已有run目录。
- `COMPLETE`表示该次供给/对账条件通过，且仅在记录的采样分辨率内未观测到断粮，**并不证明任意时刻都非空或已找到最大吞吐**。长期生产SLA、两小时调优确认和更大节点规模均未由T0证明。

每次运行保存私有before/after、events.jsonl、summary.json或interrupted.json。原始失败记录不删。程序退出码非0即停止当前批次，先检查受理task ID及维护状态；不自动解除UNKNOWN。

`load.py`另提供有界的原生/同步预备请求引擎，以及独立到达时钟：客户端忙或迟发会记录未发送量，不降低目标速率伪装服务达标、不积累无限线程任务。相应T1/T3/T4的完整在线案例编排、混合/429分类和候选选择将在各阶段集成；旧fixed-count脚本不能充当持续吞吐证据。T6长时驱动也不由当前最长300秒的单点命令代替。

参见：[总体计划](E3-TUNING-PLAN.md)、[T0验收记录](E3-T0-TOOLS.md)、[测试设计矩阵](E3-TUNING-MATRIX.json)。

## 固定到达率与队列工具（2026-10-29）

新增 `arrivals.py` / `arrival_point.py`：独立到达时钟和最多16个HTTP在途的有界突发；持久intent、QUEUE_FULL分类、拒绝无任务核对、原deadline排空及结果文本/唯一计量核对。队列允许自然为空，不用于饱和吞吐排名。`queue_tuning.py`按明确候选生成45个队列/到达/突发点，只有哈希前置和选择清单一致才执行。运行参数变更通过既有维护锁及实时快照，正常结束恢复原参数，异常不自动恢复。

验证：38项相关本地测试通过；新队列驱动的2项独立PostgreSQL/API测试通过，推理明确mock，不能当作真实OCR容量证据。首次独立库测试在用例通过后遇到JUnit目录权限错误，保留原日志，修正临时容器运行权限后完整重跑通过；正式服务未受影响。T4实机尚未开始。
