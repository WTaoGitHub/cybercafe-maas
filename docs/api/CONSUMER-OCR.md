# 下游消费者兼容OCR接口

2026-10-27。代码、独立测试库及001私有TLS实测均已通过，已发布consumer-ocr-r1；详见[验收报告](../../results/maas-consumer-ocr/REPORT.md)。针对upstream-generation-api-integration.md的同步OCR契约，保留原Chat及异步任务API。文生图/音频/视频不在本轮范围，E4持久同步幂等仍未实施。

## 渠道配置与请求

消费者配置provider=openai，model=ocr.default，api_key为管理员登记到MaaS project/模型且GPUStack侧有效的客户key。provider是消费者自己的协议字段，不必传到本系统。

- endpoint=https://<可达地址>:<端口>，消费者追加/ocr。
- 也支持endpoint=https://<可达地址>:<端口>/v1，追加后成为/v1/ocr。
- 两个POST入口等价，不应再把submit_path拼进endpoint。
- Authorization: Bearer <key>；Content-Type: application/json。
- 当前仍是001回环私有TLS18832/HTTP18831，访问通道和证书由运维另行配置，本次不新增公网/LAN监听。

```json
{
  "model": "ocr.default",
  "image_base64": "<裸base64 PNG或JPEG>",
  "language": "zh",
  "return_boxes": true,
  "request_id": "consumer-business-001"
}
```

image_base64与image_url恰好一个。image_base64也接受data:image/png;base64,...或data:image/jpeg;base64,...，声明媒体须匹配实际内容；不接受URL-safe/含空白的宽松base64。image_url必须是可公开下载的HTTP(S) PNG/JPEG地址，不附加客户Bearer或其他请求头。

language允许省略、auto、zh、en；它是中英文模型的可选提示，不强制过滤输出或切换模型，不提供语言检测。因此响应不回显language；不支持的提示明确422。

return_boxes是严格JSON boolean，默认true；false省略polygon，不影响识别文字。坐标沿用上游box的原图像素坐标，门面不再做坐标旋转。model使用实际逻辑名，不把当前OCR伪装成示例qwen-vl-ocr。

request_id为可选1–200字符字符串，不允许控制字符。它与服务器生成的X-Request-ID一起用于日志关联，不透传到模型，不是幂等键。不要在关联ID中放入秘密。没有持久请求去重或结果复用保证；相同request_id再次调用仍可能再次推理/计成功用量。消费者“不重试”不改变GPUStack内部策略。

## 成功响应与计量

HTTP200，顶层直接返回lines：

```json
{
  "lines": [
    {"text": "中文 English", "confidence": 0.99,
     "polygon": [[12,12],[220,12],[220,44],[12,44]]}
  ]
}
```

score映射confidence（有限数值0–1），box映射polygon（四点有限坐标）。缺少可选分数或坐标时不丢行；无文字图片返回{"lines":[]}，是合法成功。缺lines、非行数组、错误字段类型/分数/坐标会502，且不计成功用量。转换及输出校验通过后才写入唯一成功事件；一张图片一次成功quantity=1，与原同步请求共享project/global配额。

本接口同步返回结果，不返回task_id；消费者自己的外层异步任务由消费者维护。不需要为OCR新增视频式/tasks轮询接口。

## 图片下载与资源边界

- 当前每张解码后最多10MiB（MAAS_MAX_INPUT_BYTES），JSON入口预算为其两倍；PNG/JPEG实际验证、完整解码，最多2000万像素，拒绝动画及其他格式。
- 下载仅80/443端口，拒绝URL内账号密码、fragment、私有/回环/link-local/保留/多播等IP。DNS所有答案须为可用公网IP，建立连接时固定已校验的数字IP，Host和TLS SNI仍使用原始域名并验证证书。
- 不跟随重定向、不读代理环境、不转发客户密钥/cookie、不自动重试下载；HTTP压缩响应不支持，按流限制下载字节。没有私有域名/内网图片服务白名单绕过。
- 获取与验证图片占用同一同步准入槽，先校验客户/模型权限、维护和UNKNOWN，再下载。输入阶段总预算10秒（HTTP下载8秒、连接4秒），之后推理采用当前模型预算，现为25秒，最大总活动预算35秒；下载失败在发送推理前明确结束，不产生UNKNOWN或成功计量。
- 推理发送后的超时/断连沿用UNKNOWN保护，不自动重放或擅自释放。已有Chat/异步接口行为保留，正常请求不依赖collector或逐请求SSH巡检。

## 常见错误

| HTTP | 情况 |
|---|---|
| 401/403 | Bearer无效、未登记project/模型或模型无权访问 |
| 404 | 逻辑模型不存在，不隐式映射示例模型名 |
| 413 | JSON体、解码字节或图片像素超限 |
| 415/422 | 图片格式/数据损坏、字段不匹配、URL受限、下载失败或不支持的语言提示 |
| 408 | 输入准备阶段超过10秒，没有发送推理 |
| 429 | 同步与异步共享容量已满 |
| 502 | 模型输出不符合契约；不计成功 |
| 503 | 维护、未决UNKNOWN或推理完成不明；不能默认认为可安全重发 |

错误JSON沿用error.code/message/retryable/request_id；请求校验错误不回显图片、URL或原始请求体。Nginx边界错误仍可能是非JSON，调用方应检查状态和Content-Type。实际消费者源码/渠道联调尚未执行，当前契约测试与真实OCR验证不能替代其环境验证。

## 实现与依赖

入口：maas_platform/consumer_ocr.py、image_fetch.py；共享SyncIngress通过prepare_input/map_output保持统一准入和终态/计量。HTTPX0.28.1/httpcore1.0.9的连接后端注入有专门测试，升级时复测DNS固定和TLS域名保留。

图片验证使用[Pillow官方发布包](https://pypi.org/project/pillow/13.0.0/)，固定13.0.0。控制端增量构建可用Dockerfile.consumer-ocr，基于已核对的e6镜像和离线Linux wheel；标准Dockerfile的requirements同时更新。MAAS_API_IMAGE仅覆盖API，执行器和observer可保持原e6镜像，数据库schema和任务协议没有变更；新接口不要求更新母版/worker。
