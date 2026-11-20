# 两台 worker 三模型自动扩容实测

2026-11-20 · GPUStack v2.2.3 · 策略 revision 8 · [调节器 Issue #39](https://github.com/WTaoGitHub/cybercafe-maas/issues/39)

显存规划给出 **1 / 1 / 3**，实测因 Unlimited-OCR-2 的模型目录不可见停在 **目标 1 / 0 / 1，Ready 0 / 0 / 1**。三个数字依次代表 Unlimited-OCR-2、OvisOCR3、PP-OCRv4。

## 现场经过

两台 Ready worker 保持在线，每张卡可用于规划的显存约 15.99 GiB。三套部署均无 worker/GPU 选择器，权重为 1:2:4，最小副本各 1，显存申报分别为 12、6、3.5 GiB，逐个启动。暂停 MaaS 派发并核对零在途、零未知后，将原目标 0 / 2 / 2 设为 0 / 0 / 0。约 15 秒后，GPUStack 确认无实例，两张卡的已分配显存均为零，实际占用均约 433 MiB。用该现场快照重算得到 1 / 1 / 3。

| 恢复调节器后 | 目标数 | Ready 数 | GPUStack 回读 |
| --- | --- | --- | --- |
| 0 秒 | 0 / 0 / 0 | 0 / 0 / 0 | 两台 worker 继续 Ready |
| 4.1 秒 | 0 / 0 / 1 | 0 / 0 / 0 | 提交第一个 PP-OCRv4 副本 |
| 16.2 秒 | 0 / 0 / 1 | 0 / 0 / 1 | PP-OCRv4 在 worker 8 上 Ready |
| 28.3 秒 | 1 / 0 / 1 | 0 / 0 / 1 | Unlimited-OCR-2 在 worker 7 上进入 ERROR |
| 40.4 秒 | 1 / 0 / 1 | 0 / 0 / 1 | 调节器未继续启动副本，写入日志无待确认项 |

实例 262 的原生错误为 `Path does not exist: /srv/maas/models/unlimited-ocr-2-0480966`，部署关闭了 `restart_on_error`。串行模式等待已提交副本就绪，当前实现遇到这次启动错误后也继续等待。确认状态持续后，于恢复调节器约 140 秒时停止观测并恢复基线。目录可见性故障记为 `UOCR2-INC-023`。共享存储、宿主挂载与容器路径中具体哪一环缺失仍待定位。 [故障 Issue #69](https://github.com/WTaoGitHub/cybercafe-maas/issues/69)

## 恢复结果

- GPUStack 目标与 Ready 均恢复至 0 / 2 / 2。失败实例已移除，OvisOCR3 和 PP-OCRv4 各有两个健康实例，四个实例的 `GET /health` 均为 HTTP 200。
- GPUStack 重新放置后，两台 worker 各运行一个 OvisOCR3 和一个 PP-OCRv4。MaaS 派发已恢复，容量快照新鲜，PP-OCRv4 / OvisOCR3 的准入槽位分别为 2 / 1，测试前为 0 / 1。
- 调节器恢复至 Apply、全局暂停。策略 revision 8、模型资源声明和空选择器保持一致，写入日志无待确认项。独立回读时排队、在途及未知均为零。
- 未提交测试推理、未创建测试数据或一次性文件。保留正常维护审计；临时启动的失败实例及额外副本已清理，未重启 VM、worker 或 Docker。

复测前需确认候选 worker 能以相同路径读取权重，并核对容器内的读取结果。本轮尚未验收 1 / 1 / 3 全部 Ready、MaaS 三模型准入和实际推理。
