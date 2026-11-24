# 自动缩容重试的三节点部署验收

2026-11-24 北京时间 15:08:02，控制机上的常驻调节器恢复 Apply，运行代码 `f38506d`。部署前后核对了镜像 ID、镜像中三个变更模块的 SHA-256、策略修订号 8，以及原生模型的期望副本和健康实例 ID。故障在使用同一镜像、外部网络隔离的临时容器中注入。正式 GPUStack 的写入验收留到下次自然缩容。 [Issue #75](https://github.com/WTaoGitHub/cybercafe-maas/issues/75)

## 故障验证

本地调节器回归通过 223 项，另有 2 项依赖固定版本的 GPUStack 运行环境，本地按条件跳过。下表记录在已部署镜像中单独执行的故障验证。

输入取自当前 3 台 Ready worker 和 3 个模型的原生 GET 快照，保留 GPU 容量、已分配显存、实例落点、副本数和显存声明。回放只在内存副本中模拟一台 worker 暂时不可用。独立验证策略管理 PP-OCRv4，接收更新的是临时容器内的 HTTP 服务。该服务没有原生管理端的转发路径。两次缩容预检均在最终 GET 返回 503，生产调节器代码均进入 5 秒退避。

| 验证情形 | 观察结果 |
| --- | --- |
| 写入前失败，重算时 worker 已恢复 | 原规划目标为 3，新目标为 4。验证端收到 0 次 PUT。 |
| 写入前失败，重算时资源仍不足 | 退避期间未写入。到期重新观察后，验证端收到了调节器发出的 1 次 4→3 更新。 |
| 验证端收完 PUT 后断开，读回仍是旧值 4 | 使用同一份持久日志重建调节器对象。连续三轮只读核对，每轮写入端独立做 4 次 GET。日志保留，总 PUT 数仍为 1。 |
| 验证端随后公布已收到的更新及实例删除结果 | 核对目标 3 及实例清单后确认完成，清除日志。全程总 PUT 数为 1。 |

这次回放还复现了独立的诊断问题。在全新的控制目录中，普通缩容之前可能没有保存 Pending 作用域，导致恢复阶段的规划状态显示 `blocked`。写入端仍完成了上述只读核对。正式环境在验证结束时显示 `pending_hold`。 [REPLICA-INC-003](https://github.com/WTaoGitHub/cybercafe-maas/issues/76)

## 正式环境读回

15:14:05 核对到 3 台 Ready worker。PP-OCRv4 为 4/4，OvisOCR3 为 3/3，Unlimited-OCR-2 为 1/1，部署前后的 8 个健康实例 ID 完全相同。各模型 Pending 为 0，调节器写入日志为空，MaaS 派发开启，额度为 8／2／6（总量／同步预留／异步上限），`UNKNOWN=0`。已确认临时验证容器全部移除。

## 复用方法

在可操作控制机 Docker、且本地有验证脚本的环境中，设置运行中的调节器容器名、同一镜像名和模型 ID。选择已稳定、2 至 4 个就绪副本分布在多个 worker 上的模型。采集进程仅调用 GET。回放容器只挂载验证脚本，临时状态写入内存文件系统。 [验证脚本](../../scripts/maas/replica_retry_replay.py)

```sh
set -o pipefail
script="$PWD/scripts/maas/replica_retry_replay.py"
docker exec -i "$MAAS_REGULATOR_CONTAINER" python -B - capture --model-id "$MODEL_ID" < "$script" |
  docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=16m,mode=1777 --env PYTHONPATH=/app \
    --mount "type=bind,src=$script,dst=/verify.py,readonly" \
    --entrypoint python "$MAAS_REGULATOR_IMAGE" -B /verify.py replay
```
