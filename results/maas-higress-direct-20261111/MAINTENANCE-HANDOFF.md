# 004 应用试点快照前的维护交接

2026-11-11 · 准备完成 · [Issue #59](https://github.com/WTaoGitHub/cybercafe-maas/issues/59)

004 的 OCR 已排空并停止，管理员可安排对 004 应用最新试点快照并启动。控制端保留 GPUStack worker 2 的原记录及 GPU 绑定，用于新实例的同卡接续。操作仅涉及服务和容器，VM 的快照与启动由管理员执行。

## 已完成并核验

| 项目 | 状态 |
| --- | --- |
| HTTP、HTTPS 新任务、快捷 OCR、同步推理及上传入口 | POST 返回 503 |
| 实际下游网络的模型能力查询 | 授权 GET 返回 200 |
| MaaS 维护 | paused，generation 1 |
| 等待队列、运行任务、未确认后端及同步活动 | 均为 0 |
| maas-ocr 期望 / 就绪 / 实例数 | 0 / 0 / 0 |
| 004 模型容器 | 已清除 |
| 004 worker 容器 | 已停止，restart=no |
| 004 GPU 计算进程 | 0 |
| GPUStack worker 2 | not_ready |
| MaaS 调节器 | Observe，策略修订 2，待确认写入日志为空 |
| GPUStack 内旧调节插件 | Observe |
| 集群 maas-baremetal-workers，ID 1 | worker_config.proxy_mode=direct |
| 同卡自动启动授权 | 当前 GPU 仍绑定 worker 2，已核验有效 |
| 压力采样 | 计划性停止，待新克隆身份核验后恢复 |
| MaaS、GPUStack 控制端健康检查 | HTTP 200 |

MaaS 已排空后才暂停派发。两个 Nginx 入口按方法和路径拦截新提交，避免维护期间新增队列。服务端不改变旧任务状态，也未重放请求。worker 停止后先经过 unreachable 阶段，待原生心跳宽限期结束后进入 not_ready。此状态由原生机制产生。

## 私有恢复材料

变更前已保存入口配置、两套调节策略、集群与 worker/实例元数据、容器定义及 PostgreSQL 备份，归档目录校验通过。004 的启动 ID、GPU UUID、原会话、原生凭据和 SSH 主机密钥另存私有基线。以上材料已复制到 Mac 的 .maas/direct004-maintenance-20261111，控制端备份归档的 SHA-256 与本机一致。

新 PC 控制目录的 direct004-maintenance-20261111 保留 READY.json、当前私有运行记录和 Higress 候选定义。候选沿用当前 enrollment 镜像、数据挂载及回环入口，设置 embedded 网关、原生 API 端口 30080。候选仅保存定义，切换在新克隆验收时进行。旧 Higress 回退容器的 enrollment 版本与当前版本不同，使用前需复核。

## 管理员操作及接续

1. 对 004 使用已安装自动 E5 的最新试点快照。优先将原 GPU 分配给新实例，以验证相同 UUID 接续原 worker 2。保留新 PC 控制面运行。
2. 新实例启动后提供 SSH 入口。接手人员先核对主机密钥、启动 ID、GPU UUID、镜像设置及自动引导记录。GPU 若有变化，先核对新的绑定和授权。
3. 验证原生注册返回的 worker_config 及后续上报均为 direct，确认 cluster_id=1。核对接续后的会话凭据，原凭据应失效。
4. 同步克隆防火墙、真实地址、SSH 公钥及压力采样来源，核验动态模型端口可达和非受信来源拒绝。
5. Observe 期间受控恢复一个 OCR 副本，确认实例落点，再切入当前版本的 Higress 候选。完成匿名、错误 Key、无模型权限及授权 OCR 测试。
6. 用注册模型恢复探针确认推理后，通过 MaaS 维护恢复命令解除暂停。维护入口拦截保留到异步任务和单次计量通过，再移除并校验 Nginx 配置、重载入口。需要恢复调节器 Apply 时递增策略修订，核对目标和在途日志后再启用。

调节器热更新要求修订递增，不能直接用修订 1 的备份覆盖当前修订 2。若要在管理员操作前恢复旧服务，需要受控签发新的 worker 启动会话并核验身份，不能直接复用已经消费的启动许可重启旧容器。
