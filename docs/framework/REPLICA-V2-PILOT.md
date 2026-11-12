# R4 独立试点操作与边界

2026-11-12 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

试点使用 `maas_platform.replica_trial`，只对已经明确准备为 0/0 且无实例的独立部署执行。工具不创建/删除模型、不缩容，不调整原部署、不启动 E17 日报进程，不访问 worker SSH。只有原生管理 API 读取和对试点部署的单步 +1 更新。

操作员先保存原部署、现网策略及待确认日志材料，核对原日志为空、目标版本 v2.2.3 和可用 GPU。新建不同名称的 OCR 试点部署，沿用核对过的固定引擎配置、运行命令、目录和显存申报；同时准备只指向试点部署的独立 Model Route，复核认证与客户模型权限。原生 API 创建部署不替代 Model Route 的准备，不修改原业务 route。创建请求若结果不确定，先只读核清，不能重发。

用含新版代码的锁定镜像运行独立容器；只挂载私有管理凭据（只读）和本试点的持久目录（可写），不发布端口、不替换常驻 E17。沿用 MaaS 依赖，运行 UID 10001。试点只在本机回环管理入口上工作。

```sh
# MODEL_ID 是独立试点；PROTECTED_ID 是保持不变的原业务部署。
# 使用已准备好的私有环境，不要把 key 放到命令行。
python -m maas_platform.replica_trial \
  --model-id "$MODEL_ID" --protected-model-id "$PROTECTED_ID" \
  --directory /var/lib/maas-replica-r4/disabled-serial-01 \
  --max-replicas 1 --startup-limit 1 \
  --pressure-mode disabled --deadline 180 --permit-pilot-writes
```

每次试点使用新目录，原目录存在时拒绝开始，防止重复执行停止的实验。工具按当前 0 目标显式采用基线，通过同一 v2 writer 执行。日志保留 status.json、trace.jsonl、policy.json、protected.json、state/ 和未确认时的 write.json，权限私有。不得因为一次试点失败就清理 UNKNOWN 或盲目换目录重跑。

原部署每轮按目标和受保护配置指纹核对；它被外部修改时立即停止。试点控制进程每秒读取完整现状，属于有界实验的 polling 模式，不可将其耗时直接当成常驻 E17 事件循环的延迟 SLA。两个进程不会争用 E17 数据库采集锁。

## 实验顺序

1. required + missing / stale：期待 pressure_unavailable，观察至少 5 秒，零新增写入。使用 `--expected-block pressure_unavailable`，stale 额外 `--pressure-case stale`。
2. advisory + missing：采用默认 30 秒缺样宽限和 60 秒后续间隔，自动 0→1。required 不允许用此路径绕过。
3. required + high：`--pressure-case high --expected-block pressure_high`；recover：`--pressure-case recover` 在初始 6 秒合成高压后改为合成低压，验证恢复观察。合成输入明确标记，不当作实测网络/磁盘压力。
4. disabled + 串行 1：自动 0→1，核对原生 Running 与实际 Higress/OCR。工具 NATIVE_READY 只表示原生实例就绪，不等于路由或推理已验收。
5. 在独立模型停止并确认实例清空后，分别以最大目标 4、在途 1/2/4 比较启动过程，至少重复观察。2/4 档须额外 `--parallel`。未分配目标仍等待，实际同时启动数由可核对资源限制。
6. 私有操作员流程在试点之间执行严格绑定的归零及实例清空核对，试点模块没有此权限。原部署保持不变。中断后先查日志/原生目标再决定恢复，不能自动清空模型或恢复参数。

工具只接受每轮至多 4 副本、在途 1/2/4、总时长 5—600 秒。超时、实例 Error/Unreachable、快照不完整或未知写入立即停止并保留证据。阶段时间是本轮实际观测值，Running 时间单独标为 native ready；Higress 可服务及 MaaS 唯一计量需另行验收。缓存状态默认 unknown；没有可信证据时不得称为冷启动或热启动。

本工具不提供客户 API、不参与常驻策略选择，也不会因为测试修改 worker、VM 或 Docker 引擎状态。R5 正式切换沿 [R3a 操作顺序](REPLICA-V2-APPLY.md)单独实施。

首轮实际结果见[R4 报告](../../results/maas-replica-v2-r4-20261112/REPORT.md)。本地工具后续新增了预期零写入参数组合校验；required + missing/stale 可期待 pressure_unavailable，required/advisory + high 可期待 pressure_high，其他矛盾组合在任何 API 访问前拒绝。试点期间的状态保存、返回 Native Ready 与实际路由/推理成功必须分开报告。
