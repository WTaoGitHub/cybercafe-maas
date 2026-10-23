# E5测试cluster与001 worker清理进展

2026-10-23。用户已自行建立test1并删除001的worker记录，随后明确现有模型服务不重要、可以随时停止。先只读核对，再执行001已规划的worker角色清理；没有重启VM、Docker引擎、GPUStack Server或MaaS控制/任务容器。

## 核对与操作

- GPUStack cluster 3=test1（Docker），当前无worker、无模型；cluster1=cybercafe-poc。002仍ready且在cluster1。
- 删除001原生worker记录后，wta-gpustack-worker容器仍running且restart=unless-stopped；001的独立cybercafe-ocr也运行。仅删除UI记录不代表已停进程。
- 已将这两个001容器restart设为no并停止；保留容器、镜像和卷，没有删除数据。
- 原生worker列表只剩002，001本机nvidia-smi计算进程列表为空。
- GPUStack Server、MaaS API/executor/observer/DB的容器ID、StartedAt和运行状态前后相同；MaaS /healthz=200。该检查只证明控制组件存活，不代表旧OCR业务逻辑路由和全部模型可推理。
- 操作前模型列表只剩rapidocr-gpu，1副本在002 running；旧model9不再在列表中，本轮未重建/修改模型，未操作002。

## 前置状态变化

独立test1已存在，不再列为待创建；还需通过受控方式读取并保存其原生注册凭据供E5使用，不能把现有cluster1凭据混入测试配置，不能在报告/Git打印秘密。

001的旧worker及独立OCR进程已停止并禁自动重启。旧维护/恢复工具固定worker集合及model1/9假设仍需适配，不应执行旧恢复命令把001 worker拉回；本轮没有完成这项代码适配，也没有创建配置服务器。

E5配置API、本机引导、签名/自签名证书、共享码、镜像封版、新快照/新VM及原生注册行为验证仍待完成。001002永久禁VM重启不变；旧P4报告保留，不把服务删除后的新拓扑写成已通过业务验收。

证据：[操作前](before-role-cleanup.json) · [停止后](controller-role-cleanup.json)。
