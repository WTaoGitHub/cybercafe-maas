# E17 GPU 每日在线与模型部署时长

1. 使用 GPUStack v2.2.3 的分页 worker、model-instance 接口读取状态。按 worker ID 和 GPU index 关联 UUID，包含分布式副本的下属 worker。仅采集状态和标识，不保存 API 凭据或原始响应。
2. 运维定时执行独立采样命令，使用单独的 SQLite 文件。建议每分钟一次，连续有效样本的最大间隔为 120 秒。定时任务部署前核对 UUID 是否跨克隆稳定，以及列表访问范围是否覆盖目标集群。
3. 查询北京时间自然日。前一有效样本代表到下一样本前的区间。中断、过长间隔、卡未上报或 UUID 冲突计为未知；ready 与非 ready 分别计入在线和非 ready。按 GPU 和模型 ID 去重，同卡多模型时长可重叠。时长表示状态观测估算，不表示实际推理时间。
4. 用离线测试验证分页、故障、跨日和去重，再在独立状态库试采。定时上线后才开始积累历史。后续按实际数据量确定留存策略。

## 运维查询入口

设置 `GPUSTACK_URL` 与授权读取 worker / model instance 的 `GPUSTACK_API_KEY`。v2.2.3 的模型副本列表有组织所有者权限检查，请按目标组织配置运维凭据。将数据库放在仅运维可访问的目录。首次执行采样会创建数据库。

```bash
python3 -m maas_platform.gpu_time --db /var/lib/maas-ops/gpu-time.sqlite sample
python3 -m maas_platform.gpu_time --db /var/lib/maas-ops/gpu-time.sqlite report 2026-11-03
```

报告只列截至查询时曾观测到的 UUID，当天统计截至查询时间。最后一个样本之后等待下一次采样确认，在此期间计为未知。未能映射到 GPU 的副本绑定在采样摘要中计数；含未映射绑定的区间单列为模型统计未知。

## 验证状态

分页、跨日、失败间隔、UUID 冲突和重复副本去重的离线测试已通过。实机独立状态库试采使用现有推理凭据时收到 HTTP 错误。需使用具备目标组织列表权限的凭据完成实机验收，再配置定时采样。
