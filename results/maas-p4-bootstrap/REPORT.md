# P4 首批部署验收：前置项 1–3

2026-10-21 · XZ-31-001 控制端 + 既有两worker · GPUStack v2.2.3。

## 结论

真实OCR常驻配置、collector只读证明交付、Compose环境及容器连通/重启验证已完成。API、执行器、observer和独立PostgreSQL留在001常驻运行。P4整体尚未完成；当前只开放回环私有入口。

## 完成事项

| 前置项 | 交付与实测 |
|---|---|
| 1 常驻部署配置 | 真实ocr.default目录、固定revision/route、独立非管理员推理身份、30天受限key、独立数据库及共享artifact卷；不再自动加载mock目录 |
| 2 collector交付 | 原始快照私有；导出pin摘要/时间/网关尝试上界；root:10001，目录750/文件640；应用只读，连续刷新实测通过 |
| 3 Compose与连通 | Ubuntu Compose2.40.3；不升级Docker引擎；API回环18830、DB回环18835，Nginx回环18831；真实同步/异步OCR通过 |

## 验证证据

- 实际镜像、UID10001运行全套回归：126通过、1跳过、1个既有Starlette弃用警告，12.97秒。实际数据库中断专项本轮未重复。
- 最终collector原子权限发布修正：19项专项通过，0.12秒。
- 同步OCR：HTTP200，46行；异步任务成功并下载46行结果。
- 应用不能写证明目录；证明观测时间持续更新，未挂载管理凭据、完整collector目录或Docker socket。
- 四个新增服务全部重启；容器身份/持久卷不变，排队任务、维护暂停状态和历史结果保留；真实恢复探针通过后任务成功，attempts=1。
- 首次验收脚本曾因挂载数组顺序变化误判，已按挂载目标排序修正。通过探针恢复首次排队任务后复测；两轮共4个成功任务，4个异步成功事件。同步成功计量2，恢复探针不计；最终UNKNOWN=0、告警=[]。
- 原OCR与P2模型各两实例保持running，四次直接请求均200/46行；collector健康。H3/cloudflared仍停止、restart=no。可销毁测试PG已停止。

## 当前运行与限制

服务使用host网络，API/DB只监听127.0.0.1；未创建新worker、Docker daemon或网桥，未修改推理防火墙，未重启宿主机/Docker daemon。应用、执行器、observer为UID10001。restart policy为unless-stopped；主机启动后的网络边界尚未验收，不能据此宣称发布完成。

正式配置仅存001私有目录，key未返回聊天/本地报告。30天内必须轮换受限推理key，尚无自动轮换提醒。HTML为验收快照，本地observer不等于独立监控。

未完成：正式节点维护配置、网络启动持久性、HTTPS、备份恢复与回退演练、独立监控、8小时负载、实际VM/双系统切换。

## 文件

[运行手册](../../docs/framework/P4-LIVE.md) · [真实验收](acceptance.json) · [部署状态](deployment-status.json) · [成功用量](usage.json) · [原有服务核对](final-verification.json) · [镜像回归](image-regression.log) · [collector专项](collector-regression.log)

[observer常驻循环证据](observer-loop-proof.json)
