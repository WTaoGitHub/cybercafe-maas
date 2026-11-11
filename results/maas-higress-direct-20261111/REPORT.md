# Higress direct 模式验证

2026-11-11 · GPUStack v2.2.3 · [Issue #59](https://github.com/WTaoGitHub/cybercafe-maas/issues/59)

独立 GPUStack/Higress 通过原生 direct 路由访问现有克隆上的真实 CUDA OCR 引擎，授权请求返回 HTTP 200 和两行识别结果。匿名、错误 Key 和无模型权限请求均被拒绝。原生 McpBridge 配置和 Envoy 实际后端都匹配模型实例端口。

## 测试范围

使用新 PC 已缓存的 GPUStack 镜像，启动独立 Docker 网络和无宿主机端口发布的隔离容器，数据库及运行目录位于临时存储。隔离库创建合成 worker 与模型实例元数据，worker 的 proxy_mode 为 direct，实例指向当前真实 OCR 引擎。未让真实 worker 切换集群或重新注册。当前常驻部署、worker 和原生 API 入口继续运行。

测试图片为仓库 e2e/data/ocr-bilingual.png。CUDA 健康接口确认引擎使用 GPU。隔离网关生成原生 model 实例注册项，Envoy 配置显示上游为该实例实际地址与端口。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 原生 worker 配置解析 proxy_mode=direct | 通过 |
| 新 PC 到真实 OCR 引擎健康检查 | HTTP 200，CUDAExecutionProvider |
| Higress 匿名 OCR | HTTP 401 |
| Higress 错误 API Key | HTTP 401 |
| Higress 有效 Key 但无目标模型权限 | HTTP 403 |
| Higress 有权限 Key 调用真实 OCR | HTTP 200，2 行 |
| McpBridge 原生 direct 注册项 | 匹配真实模型端口 |
| Envoy 实际模型后端 | 匹配真实模型端口 |
| 旧控制端直连模型端口 | 3 秒探测未能建立请求，URLError；克隆边界规则覆盖该端口 |
| 隔离容器发布宿主机端口 | 无 |

首轮测试缺少隔离模型的模型路由，授权请求返回 404。显式创建原生模型路由并等待配置同步后，上表推理及拒绝测试通过。此问题属于隔离环境准备。注册项持久文件为 YAML，最终以解析后的对象及 Envoy 实际端点交叉核对。

## 清理与常驻状态

隔离容器及其网络已移除。私有操作文件保留在新 PC 的控制目录中。收尾时常驻 maas-ocr 为 1 个期望、1 个就绪，worker 2 为 Ready，运行模式仍为 worker。待确认副本写入日志为空。MaaS 与 GPUStack 健康检查均为 200，压力采集服务活跃。

## 验收边界和后续

本轮证明了 Higress 原生 direct 路由、实际 GPU OCR 以及模型鉴权。合成元数据与真实模型引擎的组合不能代替真实无盘 worker 启动验收，尚未验证以下内容。

- 由原生集群 worker_config 交付 direct 后，真实克隆启动、注册、状态上报和网关更新。
- 真实实例重建或地址变化后的 direct 后端刷新。
- 两台真实 worker 的多副本分发、失效剔除及凭据轮换。
- 与原生 API 的同条件吞吐、延迟和控制端资源对照。

下一轮可先给集群 maas-baremetal-workers（ID 1）配置原生 worker_config.proxy_mode=direct，用独立试点克隆验证注册响应与真实状态上报，再安排现网路由切换。控制端要能访问模型端口，克隆边界只允许受信来源。当前 OCR 引擎没有独立 API Key；其他引擎的鉴权需逐项核验。

源码依据为 GPUStack v2.2.3 的 schemas/config.py、cmd/start.py 和 gateway/utils.py 中的 ModelInstanceProxyModeEnum、--proxy-mode 以及 model_instance_registry。

## 真实启动验收准备

同日已通过原生集群更新 API 将 maas-baremetal-workers（ID 1）的 worker_config.proxy_mode 设为 direct，保存变更前私有配置并核对读回值及其他字段。此默认值在 worker 启动注册时读取。当前 worker 2 仍报告 Ready / worker，模型保持 1/1，当前服务入口为已验收的原生 API。

管理员当前不能及时响应，真实克隆启动、direct 状态上报及常驻 Higress 切换待后续验收。新克隆可用后，先核对注册返回的 worker_config 和实际 proxy_mode，再核对动态模型端口、隔离规则及压力样本覆盖。Higress 候选采用当前 enrollment 版本的镜像，保留原生 API 回退配置。通过授权与拒绝测试后，核验一笔 MaaS 异步 OCR 的结果和唯一成功用量。

同日用户安排先排空 004，再由管理员应用新快照。维护准备已完成，OCR 0/0、实例和 GPU 计算进程均为 0，worker 2 为 not_ready，两个调节器保持 Observe。入口已拦截新提交，MaaS 已暂停且排空。备份已保存至 Mac。详见[维护交接](MAINTENANCE-HANDOFF.md)。
