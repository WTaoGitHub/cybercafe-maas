# 新克隆OCR端到端验收 · 2026-10-26

结论：通过。当前MaaS客户模型ocr.default已切到test1中的maas-ocr-e5-r1，revision为ocr-e5-r1；新克隆worker ID5、模型ID12、route ID14、运行实例ID94。私有TLS入口保持不变，无公网开放。

| 检查 | 结果 |
|---|---|
| 镜像传输 | 6,911,974,419 bytes压缩包SHA256校验通过，导入后完整image ID匹配既有验证版本 |
| 原生模型 | 单副本运行于worker5，ready_replicas=1；检测/分类/识别均有CUDAExecutionProvider，方向保护guarded |
| 同步OCR | 经证书校验的https://127.0.0.1:18832/v1/chat/completions，200、46行 |
| 异步OCR | 上传输入201、提交202、任务SUCCEEDED、attempts=1 |
| 结果下载 | 200、46行、revision=ocr-e5-r1；同步/异步node均为新克隆GPU UUID |
| 计量 | SQL核对该任务成功1条、usage event 1条 |
| 收尾 | API未暂停、无活动任务/请求、UNKNOWN=0，worker及模型ready；临时18841服务停止并无监听 |

原路径/root/wtaobj/gpu-ocr在宿主机存在，但bootstrap worker没有挂载它，首次实例93报路径不存在。已改用独立公开元数据目录/opt/maas-models/ocr-e5-r1/model，并在宿主机与现有worker内准备相同目录；只删除失败实例，由GPUStack生成运行实例94。未重建worker、未重启Docker引擎或VM、未恢复collector；没有在不确定请求上重试推理。

## 限制与下一步

1. 本次模型分发和元数据目录为人工准备，worker容器重建后元数据需要重新准备；规范的模型卷/分发尚未实现。不能因此宣称新克隆会自动具备模型文件及立即提供OCR。
2. 常驻维护binding仍指向旧worker2/model1、9，新目标为worker5/model12，SSH入口16482。先重绑及校验，再执行维护；当前不要直接运行旧绑定维护命令。
3. 尚未做两克隆同时在线、故障迁移、当前版本长时间负载、其他GPU型号或生产验收。母版5070兼容问题未修改。
4. 只变更新建test1模型/route、推理key对新route的许可及MaaS catalog；旧route、模型、历史任务/结果保留。回退材料在001私有目录和Mac .maas/backups/e5-ocr-activation-20261026，旧route无ready目标时不自动恢复流量。

证据：[请求及计量](acceptance.json) · [部署目标](target.json) · [静态版本登记](pin.json) · [镜像校验](image-transfer.json) · [最终状态](final-status.json)。操作流程见[运行手册](../../deploy/maas/E5-OCR-PILOT.md)。
