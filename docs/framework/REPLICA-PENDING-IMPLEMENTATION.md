# Pending 观察、收敛和保持的实现与发布

2026-11-12 · 候选已实现，本地验证完成，部署验证进行中 · [Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

业务规则仍以[Pending 设计](REPLICA-CAPACITY-DROP.md)为准。该实现接入现有 v2 E17 对账，不新建节点采集器，不访问 worker SSH，只更新原生模型 `replicas`。

## 触发与状态

当前可执行的资源不足判定要求：完整原生清单；目标与实例数一致；原生 ready 计数与有效 Ready 实例一致；实例只由有效 Ready 和无落点、无资源预留的 Pending 组成；原生 Pending 原因为 `No suitable workers` / `No available workers`；按当前声明和原生预算，已有 Ready 加剩余可新增容量仍不能满足目标。下载、初始化、未知原因、删除中、未核对落点均不能取得缩容权限。

按部署持续观测 180 秒，使用单调时钟；每次完整成功查询更新观测。原因/条件消失、观测失败、过长缺样或观察进程更换时重置尚未完成的观察，不计不可观测间隔。Ready 数变化不重启同一观察期，执行目标用最新 Ready 计算。

观察完成后目标 `T=max(Ready,min_replicas,auto_scale_down_min_replicas,1)`；只在 T 低于当前指定数时逐个降低。v2 规则最小值、自动缩容下限和上限均必须为正；旧 v1 不改变。每笔读回确认、删除收敛后再继续，同轮不重复等 3 分钟。

私有控制卷新增 `pending-control.json`，使用同一跨进程发送锁和原子落盘。状态为 observing / contracting / holding，持久记录本轮目标、观测代次、受保护实例 ID、输入指纹和请求确认摘要。holding 可跨进程保留；忽略自身目标更新、Pending 删除和心跳，只在实际资源/Ready/策略/配置或外部目标变化后释放，避免反复制造同一批 Pending。达到下限仍 Pending 时保持，无须清空 Pending。

观察/保持不影响全局暂停，满 180 秒不能绕过暂停、授权、压力、版本、最新快照或未知写入保护。收敛前的未确认请求仍只能只读确认；受保护实例意外消失时保留未确认日志，不重发、不自动解除。

## 原生缩容能力确认

为了既不删除唯一健康副本、又能删除多余 Pending，放宽保护仅限：固定 GPUStack v2.2.3、核验过的 controllers/scorers 源码、有效评分配置、SPREAD、所有非健康实例为无分配/无 claim Pending。该版本这些 Pending 的组合评分为 0，健康 Ready 的状态分为正数，且其他已核验项非负，因此原生先选择 Pending。

部署时在控制机运行：

```sh
python3 scripts/maas/build_replica_native_proof.py \
  --native-container "$NATIVE_CONTAINER" \
  --controller-container "$E17_CONTAINER" \
  --output /absolute/private/native-proof/proof.json
```

脚本只在部署时读取两个本机容器的版本、源码摘要及评分设置，输出私有证明，权限 600/UID10001；不输出原生 key 或地址。原生升级、换镜像或调整评分配置时必须重新生成并验证，不应复用旧证明。该机制依赖固定部署契约，不宣称通过公开版本号即可远程识别任意热修改。

通过 `MAAS_REPLICA_NATIVE_SCALE_DOWN_PROOF_FILE` 将证明只读提供给 E17；v2 Apply Compose 覆盖已加入对应挂载。缺失/不匹配时返回 `native_scale_down_proof_required` 并保留原健康保护。无健康实例时仍需完整 Pending 证明和非零下限，不会归零。

这不是逐请求/每节点 SSH collector。原生读到发送间仍无 CAS，无法承诺外部持续并发变更零中断；发送前复核完整指纹，发送后核对受保护实例 ID，异常保留 PendingWrite。

## 验证与发布顺序

本地覆盖三模型 4/10、10/20、15/20 收敛到 4/4、10/10、15/15，并保持全部 29 个 Ready ID；179/180 秒边界、缺样/进程重建、最新 Ready、最小值 2 全 Pending、资源恢复、持久 hold、暂停、缺少/伪造原生证明、未知写入和意外健康实例丢失。

先固定新镜像，在独立试点验证真实 180 秒资源不足、原生选择不删除健康实例、到下限仍 Pending 无额外写入、实际容量释放后恢复及管理员接口。验证通过后，再保存新鲜回退材料，将现有 E17/API 受控迁移，原模型初始 Observe/adopt 当前目标再 Apply/resume。Native Server、VM、worker 和 Docker 引擎不重启。正式实际结果以后续部署报告为准。
