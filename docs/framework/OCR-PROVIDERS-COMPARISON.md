# OCR 服务初步对比

2026-10-30。仅整理产品与接口，不纳入吞吐、延迟、容量、识别精度排名或单位成本。千问免费额度测试及统一数据整理留待后续；本轮不调用推理API。

## 四方对照

| 维度 | 千问AI平台 | 百度智能云／千帆 | SiliconFlow | 本系统当前服务 |
|---|---|---|---|---|
| 主要OCR选择 | Qwen3.5-OCR、Qwen-VL-OCR系列 | 通用OCR、文档解析、手写、卡证票据等10项目录产品 | 已关注DeepSeek-OCR、PaddleOCR-VL-1.5；不是全平台完整目录 | 逻辑名ocr.default，底层PP-OCRv4／ORT GPU |
| 产品重点 | 文字提取、定位及结构化抽取 | OCR及多格式企业文档处理、行业专用产品 | 通过统一模型API调用托管OCR模型 | 中英文图片文字识别与业务任务交付 |
| 输入范围 | 图片；qwen3.5-ocr另有Responses API直接解析PDF | 按产品区分：图片OCR、PDF解析，Standard目录说明支持Word／Excel等18种格式 | 文档明确DeepSeek-OCR支持图片和PDF；不据此推定Paddle同样支持 | PNG／JPEG；消费者门面支持base64或公开图片URL，异步使用已上传artifact；尚无原生PDF／Office解析 |
| 输出能力 | 文本、位置、信息抽取、表格／公式等，取决于模型及任务 | 文本／坐标、Markdown／JSON、文档结构或字段KV，取决于产品 | 已测DeepSeek返回文本；支持OCR／文档转Markdown等提示词，具体输出依模型 | 行文字、置信度、原图坐标；原Chat响应另封装JSON；没有文档语义结构还原 |
| 接口形态 | OpenAI兼容Chat、DashScope；PDF等特性需对应接口 | Chat、专用OCR、异步解析等多种接口，不能统一当作Chat调用 | 当前测试使用Chat Completions | /ocr、/v1/ocr、Chat及持久异步Task API |
| 免费／价格状态 | 按模型、账号权益核对；免费额度不等于长期零价 | 用户提供目录10项均isFree=false；不足以推定无试用额度或具体价格 | 用户提供两个模型输入／输出单价均为0；未核对账单或活动期限 | 自有资源成本；没有外部模型按token账单，不等于运行免费 |
| 数据与部署控制 | 本次比较公共云API，需提交数据到供应商 | 同左 | 同左 | 当前推理与文件存储链路部署在自有受控环境，可自控版本及维护 |
| 当前证据状态 | 官方文档整理，未实测；账号可用免费模型待确认 | 用户目录＋部分官方文档，未实测 | DeepSeek调用成功；Paddle VL 1.5本轮超时，已停止 | 现有接口与验收记录；不代表具备左侧全部文档能力 |

“能力”是产品文档描述或既有接口事实，不代表已经在统一样本上验证效果。只比较上述公共API及本系统现部署，不推论供应商没有私有化产品。

## 百度用户目录中的10项

| 类别 | 目录名称 |
|---|---|
| 多格式文档解析 | Qianfan-OCR-Parser-Standard |
| 多模态文档解析 | Qianfan-Parser-PaddleOCR-VL（目录说明基于1.6） |
| OCR／文档模型 | PaddleOCR-VL（版本0.9B）、DeepSeek-OCR、Qianfan-OCR |
| 文档处理流水线 | PP-StructureV3 |
| 通用／手写／票据 | Qianfan-OCR-General、Qianfan-OCR-Handwriting-Eaasy、Qianfan-OCR-Card-Invoice |
| 文字识别 | PaddeOCR-V6（保留目录原拼写，描述为PP-OCRv6） |

不能将百度Parser的PaddleOCR-VL 1.6、独立0.9B接口、SiliconFlow的VL 1.5和本系统PP-OCRv4视为相同服务。多格式接入与异步API属于服务工程；识别效果属于后续统一测试，分别评价。

## 初步定位

- 千问：适合作为文字提取与结构化抽取候选，后续先核对实际可用免费额度和精确model ID。
- 百度：产品线覆盖更广，特别是多格式文档、异步解析和行业字段抽取；接入时需要逐产品匹配协议。
- SiliconFlow：适合作为统一模型API的托管OCR候选；零价信息与可用性分开看。
- 本系统：主要价值是数据、部署、版本及业务任务流程可控；当前功能偏图片OCR，不能宣称文档解析功能比供应商完整。

异步、坐标、结构化输出均不是本系统独有优势。对本系统更准确的表述是：在自己的环境里提供可控的OCR和持久任务流程，而不是拥有云服务没有的所有能力。

## 千问免费测试前需核对

官方免费额度通常有有效期，按模型独立计算；部分历史OCR版本已列入“已下架免费额度”名单，老账号未到期额度仍可能可用。因此后续以该账号权益页为准，选定精确模型与版本，不能直接把模型目录中的所有OCR标成免费。此阶段尚未获取账号权益，不发起测试。

## 来源

- [千问模型市场](https://www.qianwenai.com/models?search=ocr)：静态页面未取得完整搜索结果；模型选型以以下官方文档补充，不宣称穷举目录。
- [千问OCR能力与模型选型](https://platform.qianwenai.com/docs/developer-guides/multimodal/ocr)
- [千问OpenAI兼容OCR接口](https://platform.qianwenai.com/docs/api-reference/specialized-models/qwen-ocr/openai)
- [千问免费额度及历史模型额度下架规则](https://platform.qianwenai.com/docs/resources/free-quota)
- 百度10项来自用户提供的模型目录JSON，仅提取产品字段，不保存附件中的签名图片URL、授权参数或日志ID。
- [百度PaddleOCR-VL解析提交API](https://cloud.baidu.com/doc/qianfan-api/s/nmu2bqtxe)
- [SiliconFlow多模态与OCR](https://api-docs.siliconflow.cn/docs/userguide/capabilities/multimodal-vision)
- [SiliconFlow测试状态](../../siliconflow/PADDLE-VL15-STATUS.md)
- [本系统接口](../api/README.md)、[消费者OCR契约](../api/CONSUMER-OCR.md)、[PP-OCRv4运行记录](../../results/maas-e3-mu-v2-20261030/REPORT.md)。
