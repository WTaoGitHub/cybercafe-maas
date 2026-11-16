# Unlimited-OCR-2 P3 试点权限门槛

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)

主代理第1次处理完成 E06 的辅助脚本修正及试点权限配置。原始请求和鉴权回调验证通过；尚未激活模型实例，不能视为 Higress 数据面或 M1 验收。

现场 GPUStack v2.2.3 的空 `allowed_model_names` 不缩限 Key，管理员用户可访问所有未删除路由。现有下游 inference Key 属于管理员，新增模型的 owner 隔离不能阻止它访问新路由。此前仅准备0副本，未开放新引擎。

在私有快照和写入意图之后，仅把该旧 inference Key 的允许模型明确为现场已有的两条路由；未轮换 Key 值，scope、描述、其他 Key 和原路由均读回不变。通过原有绑定和相同 Key，从实际下游容器在调整前后分别完成旧 `ocr.default` OCR，文字结果正确且 `successful_images=1`。调整前任务为 `task_263a7046ee524cb7b31df4ab197d7df9`，调整后为 `task_9c41b5f4bab64ba487283d7efcafe990`。旧辅助脚本曾因读取 `state` 而非结果的 `status` 误报退出失败；原响应明确成功，修正校验后再次新建的任务也成功，未重放未知任务。

新建仅供受信内部试点的 inference Key，允许模型只有 `unlimited-ocr-2`；明文仅保存在控制端受保护文件，未传到 worker、Git 或 Issue。新增 route 8 指向 model 11，访问策略设为 `allowed_principals`，普通用户/principal 授权集合为空。管理员访问仍受各 inference Key 的模型列表约束。模型保持0副本，无新模型推理。

| 检查 | HTTP / 结果 |
| --- | --- |
| 旧 Key → 两条原路由的鉴权回调 | 均200 |
| 旧 Key → 新路由的鉴权回调 | 403 |
| 试点 Key → 新路由的鉴权回调 | 200 |
| 试点 Key → 旧路由的鉴权回调 | 403 |
| 错误 Key / 匿名 → 新路由的鉴权回调 | 均401 |
| 旧 Key → 新路由的原始 Chat | 404，`Model not found` |
| 同上且伪造旧模型请求头 | 同样404，`Model not found` |
| 试点 Key → 新路由、当前0实例 | 404，`Model not found or no running instances available` |
| 试点 Key → 旧模型的原始 Chat | 403 |

验证使用新的 HTTP Request，未复用环境代理、重定向或 ambient cookie。初次校验器把原始 Chat 的拒绝固定写成403，实际404后保留证据并核对错误体；不能仅把404状态当作权限证明。现场原生代理源码在 Key 无模型权限时隐藏路由并返回 `Model not found`，有权限而无实例时给出不同消息；上表分别核对了响应。Higress 使用 `/token-auth` forward-auth，但本轮请求的实际数据面路径仍须在 P3 证明，不能由源码和回调替代。

E05 的启动资源守卫已在 [206ba9c](https://github.com/WTaoGitHub/cybercafe-maas/commit/206ba9c) 验证并推送；守卫派生镜像已发布且 worker reader 拉取匹配，详见[零副本阶段报告](../20261116-p3-zero/REPORT.md)。每个新试点容器仍须在启动模型前核对身份并设置 cgroup 限额，不提供无人值守创建实例的恢复能力。后续受控激活、真实 Higress 请求、实例替换与旧 OCR 回归由 Terra Light 继续，未完成项不标记通过。
