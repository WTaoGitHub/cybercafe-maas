# P0：模型版本固定与执行前预检契约

2026-10-20 · 实现：[versioning.py](../../maas_platform/versioning.py) · [真实控制记录验收](../../results/maas-p0/version-acceptance.json)。

## 决策

v2.2.3没有main中的revision API。本项目采用**专用版本化route/model/backend + 配置指纹 + 实际镜像内容ID + 新鲜worker镜像清单**，不把可更新的生产别名直接当作不可变版本。

- 客户别名可以变；任务受理时解析为不可变revision及具体route/model ID，后续执行不重新解析别名。
- 版本目标只允许一个本地模型target、正权重、无fallback、非public权限。外部provider和可变外部权重不在本期已批准范围。
- 指纹包含route/target、模型来源、backend版本/参数/env/selector，以及backend运行命令、env、镜像配置。只保存哈希，不把可能含secret的配置抄进任务或报告。
- 每个版本目标登记完整sha256镜像内容ID；所有可执行worker必须具有同一内容。清单必须由可信运维通道生成，默认60秒内有效；新worker、缺失/过期清单、tag复用均失败关闭。
- 第一批支持镜像内权重（现有OCR适合此方式）。外部路径权重需额外hash/分发契约，不能只记录路径而声称版本已固定。
- 发布新版本使用新目标，不改写仍被非终态任务引用的旧目标；控制端升级/改route必须遵守维护流程。旧目标退役前明确等待、失败或保留策略，不静默换模型。

此方法是应用层预检+运维发布约束，不是GPUStack提供的原子不可变部署。预检与真正推理之间仍有TOCTOU窗口：P2须限制管理写入口、核对实际派发目标、登记发布锁/维护边界。不能声称一个只读快照已解决任意外部管理员并发改配置的问题。

## 实现及验收

新增VersionPin注册与verify_pin预检，当前未接入mock任务执行；P2真实connector必须在受理/执行时调用并持久保存pin。瞬时ready_replicas/副本数量不作为语义指纹，实际可执行worker集合需单独核对。

真实GPUStack临时资源验收：

1. 登记专用route/model/backend与两worker一致的probe镜像，原配置预检通过。
2. 实际修改临时model的env后预检拒绝，返回MODEL_REVISION_CHANGED。
3. 同tag镜像ID变化拒绝，返回MODEL_IMAGE_CHANGED。
4. 单元测试覆盖新worker/过期清单、target改绑、fallback、public route、外部可变权重、上游版本变化等。

累计30项鉴权/版本/PostgreSQL测试通过。临时资源已删除，未更新现有OCR。为测试同内容镜像，复制的是独立probe镜像，未改原OCR镜像tag。

## P2需执行的准备

- 原两节点OCR 0.3由不同基础镜像构建，不能因同tag就假定相同内容ID。新任务目标先将选定镜像以**新专用tag**统一分发，原生产tag与运行容器保持。
- 部署专用版本目标，保存pin；不直接把当前rapidocr-gpu别名当成固定历史版本。
- 使用受限管理connector获取配置；可信运维生成新鲜镜像清单。清单采集程序为scripts/maas/p0/collect_image_inventory.py，只通过既有SSH认证查询image inspect，不读取或转发私钥。
- 上游透明重试边界遵循P0-GATEWAY；未知执行不得自动叠加POST。

版本策略已确定且预检原语通过实测。以上真实OCR准备属于P2实施前置，不表示P0部署了新OCR服务。
