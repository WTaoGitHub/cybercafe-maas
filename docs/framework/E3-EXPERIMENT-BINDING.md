# E3实验矩阵与新profile证据绑定

2026-10-30。仅测试工具；不修改MaaS业务服务或部署，不启动正式负载。

新矩阵为[E3-TUNING-GC-R1-MATRIX.json](E3-TUNING-GC-R1-MATRIX.json)，profile为`ppocrv4-ortgpu-2x4080s-gc-r1`，绑定GC-r1实际应用image ID。配置中须同时提供`matrix_file`绝对路径与该文件原始字节的`matrix_sha256`，profile必须匹配。不能通过更换矩阵删除原阶段依赖；未显式指定矩阵的旧配置仍执行原旧profile校验，不能用于解锁新profile。

新的阶段证明、选择清单及单点结果记录matrix_sha256、runtime_binding_sha256。旧profile、不同输入、不同矩阵或缺少框架绑定的证明即使写着COMPLETE也拒绝。新配置在参数变更前核对证明、选择点、参数、已有输出和任务命名空间；实际控制端API/executor/observer的镜像ID也必须符合绑定。该校验使用控制端Docker inspect，不增加worker SSH轮询或逐业务请求门禁。

## 已完成基线的只读导入

三轮GC-r1新基线已经独立核对，无需为格式转换重新发送任务。`scripts/maas/runtime/bind_baseline_proof.py`只读指定验证文件，核对文件SHA256、profile、实际框架镜像、原参数、三轮时长/完成数/供给、结果下载和唯一执行/计量核对标志，生成带来源摘要的T0-BASELINE证明。它是现有验证证明的显式导入，不是新测量，也不能把旧profile记录改名后当成新结果。

私有配置包含：`profile_id`、`matrix_file`、`matrix_sha256`、`verification_file`、`verification_sha256`。验证文件可指向已保存的`results/maas-artifact-gc-20261030/baseline.json`；运行快照、质量引用和后续阶段证明仍须分别匹配，不由导入工具补造。矩阵/框架/输入版本改变后重新验证绑定，不能复用旧SHA。

默认命令只验证，不写文件、不连接推理服务：

```sh
python -m maas_bench plan --matrix docs/framework/E3-TUNING-GC-R1-MATRIX.json --case-id T0-BASELINE
python scripts/maas/runtime/bind_baseline_proof.py --config /absolute/private/binding.json
```

确需导出时显式增加`--write-proof /absolute/private/t0-proof.json`；目录须700，文件600且不能已存在。T1/T2/T3仍须在新profile完成对应验证，不因导入T0而继承旧阶段结果。v2队列规则适配单独交付，不能直接以旧统一5秒驱动声称已执行新标准；HTTP/T5/T6不由本次工具更新自动启动。
