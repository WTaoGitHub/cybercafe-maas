# 当前框架文档索引

> 跨会话接续请先读：[当前状态与接力入口（2026-11-02）](CURRENT-HANDOFF.md)。

当前版本：**v0.2，2026-10-20**。设计已更新；P0已验收、P1模拟任务闭环已实现，真实OCR异步接入及后续阶段仍待推进。

## 当前有效

- [HTML总览](ROADMAP.html)：设计、实施计划、路线图和组件架构图。
- [详细设计](DESIGN.md)：固定GPUStack v2.2.3、复用边界和最小任务层。
- [实施计划](PLAN.md)：工作包、代码组织、测试矩阵与发布步骤。
- [路线图](ROADMAP.md)：P0–P4阶段、门槛及初步工程量。
- [架构图SVG](ARCHITECTURE.svg)、[PNG](ARCHITECTURE.png)、[Mermaid源文件](ARCHITECTURE.mmd)。

核心决策：同步/SSE走GPUStack；异步经持久任务API、PostgreSQL、执行器，再调用GPUStack；结果持久保存于001。复用既有身份和模型路由，但新任务端点需要验证安全身份桥接。首期采用全局受控维护，不承诺普通worker精确逐节点无损drain。

## 参考与历史

- [GPUStack源码复用分析](GPUSTACK-REUSE-REVIEW.md)及 [版本化证据](GPUSTACK-SOURCE-EVIDENCE.json)：静态分析输入，不是现网验收报告。
- [v2.2.3初次收敛建议](GPUSTACK-V2.2.3-SCOPE.md)：**Superseded**，其中暂缓任务层的建议已被本版撤回。
- [v0.1设计](archive/DESIGN-v0.1.md)、[v0.1路线图](archive/ROADMAP-v0.1.md)：历史归档，不执行旧F0–F6。
- 根目录 [MILESTONES.md](../../MILESTONES.md)：Deprecated，历史OCR业务规划。

## 生成HTML和图示

```sh
python3 docs/framework/render.py
# macOS：同时生成PNG；使用系统sips转换SVG，不需安装依赖。
python3 docs/framework/render.py --png
```

HTML样式与架构SVG内嵌，可离线查看。Markdown为内容来源；修改后重新生成。所有文档均不包含实际凭据，不会触发部署。

## 实现进展

[首批实现报告](../../results/maas-p0-p1/REPORT.md) · [任务层运行说明](../../deploy/maas/README.md)。

## P0验收

[P0完整报告](../../results/maas-p0/REPORT.md) · [入口与计量](P0-GATEWAY.md) · [身份](P0-IDENTITY.md) · [版本预检](P0-VERSIONING.md) · [参数基线](P0-ACCEPTANCE.md)。


## 当前最高优先级增强：E5

[克隆启动配置与自动注册设计](E5-DESIGN.md)：2026-10-23修订四为当前实施范围；母版不承担在线配置服务，首版仅启动闭环。历史调查见 [E5调研记录](E5-RESEARCH-20261023.md)。设计完成、环境取证部分完成，服务与克隆验收未实施。P4两节点私有试点已通过，阶段证据以PLAN及验收报告为准。


### E5当前基线：修订六

共享码直接取批次统一配置，不登记VM、不发会话凭据、不增加数据库或GPU资源映射。详见 [设计](E5-DESIGN.md)、[开发F0–F7](E5-IMPLEMENTATION.md)、[部署T01–T14](E5-TEST-DEPLOYMENT.md)。[旧会话草案](E5-SESSION-PROPOSAL.md)仅历史参考。当前尚未实现/部署。
