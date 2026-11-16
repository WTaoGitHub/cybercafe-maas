# Unlimited-OCR-2 执行故障登记

2026-11-16 · [执行计划](UNLIMITED-OCR-2-PLAN.md) · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)

执行故障统一编号为 `UOCR2-INC-<三位序号>`。`UOCR2` 标识工作流，`INC` 标识执行故障，末尾序号在本工作流内递增。增强列表继续使用原有 `E<number>`，例如本工作流所属增强项为 E10；GitHub Issue 编号独立维护。

本次只更名，保留故障顺序、处理次数、验证结论和停止状态。后续正文、接力记录和 Issue 引用均使用完整新编号；旧编号只用于下表回查。历史 Git 提交、提交链接及私有原始证据保留。

| 旧执行编号 | 新编号 | 故障 | 当前结论与证据 |
| --- | --- | --- | --- |
| E01 | `UOCR2-INC-001` | 旧 SSH MCP inventory 挂起 | 已改用受信替代路径，见[首轮记录](../../results/unlimited-ocr-2/20261116-p1/REPORT.md) |
| E02 | `UOCR2-INC-002` | legacy Docker builder 不支持部分命令参数 | 已纠正参数，见[首轮记录](../../results/unlimited-ocr-2/20261116-p1/REPORT.md) |
| E03 | `UOCR2-INC-003` | 固定 CUDA 基础镜像获取受阻 | 首轮按上限停止，后经用户授权重试解决，见[交付证据](../../results/unlimited-ocr-2/20261116-base-image/REPORT.md) |
| E04 | `UOCR2-INC-004` | 缺少 Python 开发头文件，Triton warmup 编译失败 | 修复后 GPU JIT、模型 warmup 和真实解析通过，见[P2](../../results/unlimited-ocr-2/20261116-p2/REPORT.md) |
| E05 | `UOCR2-INC-005` | GPUStack 自定义后端缺少 CPU/RAM 硬限额入口 | 试点资源守卫及实际 cgroup 已验证；无人值守恢复未闭环，见[运行限额](../../results/unlimited-ocr-2/20261116-p3-image-authority/REPORT.md) |
| E06 | `UOCR2-INC-006` | 权限核验辅助脚本报错及授权范围确认 | 核验完成；用户指定的 All models 保留，见[当时快照](../../results/unlimited-ocr-2/20261116-p3-auth/REPORT.md)和[最终状态](../../results/unlimited-ocr-2/20261116-uocr2-inc-008-stop/REPORT.md) |
| E07 | `UOCR2-INC-007` | 后端镜像 registry 地址不适用于 worker | 更正后实际 GPUStack 拉取、启动通过，见[镜像地址证据](../../results/unlimited-ocr-2/20261116-p3-image-authority/REPORT.md) |
| E08 | `UOCR2-INC-008` | Higress 非幂等请求重试及不重放验证未完成 | 主代理两次处理后按约定停止，见[停止检查点](../../results/unlimited-ocr-2/20261116-uocr2-inc-008-stop/REPORT.md) |
