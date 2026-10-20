# 当前框架文档索引

当前版本：**v0.2，2026-10-20**。设计已更新；P0部分探针与P1模拟任务闭环已实现，真实OCR异步接入及后续阶段仍待推进。

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
