# 下游消费者API兼容现状

2026-10-27更新。先前2026-10-21的差异分析及决策背景保存在[历史分析](archive/CONSUMER-API-GAP-20261021.md)。旧文中的collector前置已由E6退役，不再适用于当前接口。

## 当前结论

同步OCR兼容门面已实现、部署并通过001私有TLS实测；现有Chat/异步接口保留。文生图尚未提供，音视频仍按用户决定不纳入当前对接。消费者自身网关源码/真实部署环境尚未联调。

| 项目 | 当前状态与对接要求 |
|---|---|
| OCR路径 | POST /ocr，以及等价别名/v1/ocr；对应endpoint不带前缀或以/v1结尾 |
| 输入 | model+image_base64或image_url恰好一个；PNG/JPEG真实校验，支持裸base64及data URL |
| 输出 | 顶层lines，text/confidence/polygon，空文字图返回[]；格式失败不计成功 |
| URL | 仅公网HTTP(S)80/443，无重定向/额外认证，DNS解析与连接IP固定；私有图片域名/内网服务不在当前支持范围 |
| language | 支持auto/zh/en提示，不强制选语言或做检测，响应不回显language；其他提示422 |
| return_boxes | 默认true，false省略polygon |
| model | 消费者须配置实际逻辑名ocr.default，不自动冒充示例qwen-vl-ocr |
| Bearer | 已支持；消费者key还需按既有流程绑定MaaS project/模型并在GPUStack有效 |
| request_id | 可选日志关联；没有同步幂等，E4继续暂缓，不采用内容hash短窗去重 |
| 重试 | 消费者不重试仅为其自身声明；没有因此改变GPUStack内部策略，UNKNOWN保护保留 |
| 文生图 | 尚无API/运行模型/按n计量，需作为后续完整业务功能接入 |
| 网络与证书 | 原私有回环监听保留，实际下游远程部署须另安排可达通道及证书信任；本次不开放公网/LAN |

## 实施与验收

复用SyncIngress的身份/模型授权、共享准入、维护、UNKNOWN和唯一成功计量；输入准备10秒预算在同一准入槽内，推理25秒预算；响应转换与校验通过后才计成功。没有改变原Chat/异步协议、数据库schema、模型部署或worker/母版。

[完整接口参数及限制](../api/CONSUMER-OCR.md) · [API总表](../api/README.md) · [实际验收](../../results/maas-consumer-ocr/REPORT.md) · [部署与回退](../../deploy/maas/CONSUMER-OCR.md)。

消费者文档9.2中OCR外层返回task_id是消费者自己的任务包装，不要求本系统OCR返回202；当前按4.1同步返回lines。最终仍须在消费者渠道配置provider=openai、endpoint、实际模型名及受限key，进行其环境的联调。
