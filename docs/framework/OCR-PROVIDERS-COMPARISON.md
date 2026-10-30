# OCR 服务初步对比

2026-10-30。仅整理产品与接口，不纳入吞吐、延迟、容量、识别精度排名或单位成本。千问免费额度测试及统一数据整理留待后续；本轮不调用推理API。

## 四方对照

| 维度 | 千问AI平台 | 百度智能云／千帆 | SiliconFlow | 本系统当前服务 |
|---|---|---|---|---|
| 主要OCR选择 | 仅qwen3.5-ocr、qwen-vl-ocr | 仅PaddleOCR-VL（0.9B）、PP-StructureV3、DeepSeek-OCR、Qianfan-OCR | 已关注DeepSeek-OCR、PaddleOCR-VL-1.5；不是全平台完整目录 | 逻辑名ocr.default，底层PP-OCRv4／ORT GPU |
| 产品重点 | 文字提取、定位及结构化抽取 | 指定的OCR模型与文档处理流水线 | 通过统一模型API调用托管OCR模型 | 中英文图片文字识别与业务任务交付 |
| 输入范围 | 图片；qwen3.5-ocr另有Responses API直接解析PDF | 逐模型核对；不沿用其他Parser产品的18格式支持声明 | 文档明确DeepSeek-OCR支持图片和PDF；不据此推定Paddle同样支持 | PNG／JPEG；消费者门面支持base64或公开图片URL，异步使用已上传artifact；尚无原生PDF／Office解析 |
| 输出能力 | 文本、位置、信息抽取、表格／公式等，取决于模型及任务 | 文字／文档元素及结构化内容，取决于指定模型；不沿用卡证专用产品能力 | 已测DeepSeek返回文本；支持OCR／文档转Markdown等提示词，具体输出依模型 | 行文字、置信度、原图坐标；原Chat响应另封装JSON；没有文档语义结构还原 |
| 接口形态 | OpenAI兼容Chat、DashScope；PDF等特性需对应接口 | DeepSeek／Qianfan为Chat，PP-StructureV3为专用OCR；四项异步支持均待核对 | 当前测试使用Chat Completions | /ocr、/v1/ocr、Chat及持久异步Task API |
| 免费／价格状态 | 按模型、账号权益核对；免费额度不等于长期零价 | 四项目录均isFree=false；具体当前价格未确认，不套用其他产品的页价或次价 | 用户提供两个模型输入／输出单价均为0；未核对账单或活动期限 | 自有资源成本；没有外部模型按token账单，不等于运行免费 |
| 数据与部署控制 | 本次比较公共云API，需提交数据到供应商 | 同左 | 同左 | 当前推理与文件存储链路部署在自有受控环境，可自控版本及维护 |
| 当前证据状态 | 官方文档整理，未实测；账号可用免费模型待确认 | 用户目录＋部分官方文档，未实测 | DeepSeek调用成功；Paddle VL 1.5本轮超时，已停止 | 现有接口与验收记录；不代表具备左侧全部文档能力 |

“能力”是产品文档描述或既有接口事实，不代表已经在统一样本上验证效果。只比较上述公共API及本系统现部署，不推论供应商没有私有化产品。

## 百度范围：仅以下4个模型

| 模型 | 目录ID／调用模型名 | 特点（来自用户目录） | 当前价格 | 异步支持 | 实测吞吐 |
|---|---|---|---|---|---|
| [PaddleOCR-VL（0.9B）](https://console.bce.baidu.com/qianfan/modelcenter/model/buildIn/detail/am-011b24c8feea) | am-011b24c8feea／paddleocr-vl-0.9b | 紧凑视觉语言模型，109种语言，文本、表格、公式、图表识别 | 待确认 | 待确认 | |
| [PP-StructureV3](https://console.bce.baidu.com/qianfan/modelcenter/model/buildIn/detail/am-d63da0202bf5) | am-d63da0202bf5／pp-structurev3 | 文档处理流水线，图片／PDF转结构化内容、版面与阅读顺序恢复 | 待确认 | 待确认 | |
| [DeepSeek-OCR](https://console.bce.baidu.com/qianfan/modelcenter/model/buildIn/detail/am-5cf0198e8d70) | am-5cf0198e8d70／deepseek-ocr | 视觉语言OCR、图像转文字与视觉文本压缩 | 待确认 | 待确认；已知Chat接口 | |
| [Qianfan-OCR](https://console.bce.baidu.com/qianfan/modelcenter/model/buildIn/detail/am-52d29fea1063) | am-52d29fea1063／qianfan-ocr | OCR专精多模态模型，兼具一定通用视觉理解能力 | 待确认 | 待确认；已知Chat接口 | |

四项均未实测，吞吐留空。上述控制台页面本次未取得可读详情，身份对应关系依据用户提供的原始模型目录JSON。

不纳入Parser Standard、Parser PaddleOCR-VL 1.6、General、Handwriting、Card-Invoice或PP-OCRv6。此前查到的0.05元/页文档解析价、手写／卡证／PP-OCRv6等价格均不属于本表4项，不用于估算它们的费用。百度独立PaddleOCR-VL 0.9B、SiliconFlow VL 1.5与本系统PP-OCRv4也不视为同一模型服务。

## 初步定位

- 千问：适合作为文字提取与结构化抽取候选，仅跟踪qwen3.5-ocr与qwen-vl-ocr，后续核对各自账号免费额度。
- 百度：仅评估指定4项的OCR与文档处理能力，价格和异步逐项核对，不再以其他百度产品补足能力。
- SiliconFlow：适合作为统一模型API的托管OCR候选；零价信息与可用性分开看。
- 本系统：主要价值是数据、部署、版本及业务任务流程可控；当前功能偏图片OCR，不能宣称文档解析功能比供应商完整。

异步、坐标、结构化输出均不是本系统独有优势。对本系统更准确的表述是：在自己的环境里提供可控的OCR和持久任务流程，而不是拥有云服务没有的所有能力。

## 千问免费测试前需核对

官方免费额度通常有有效期，按模型独立计算；部分历史OCR版本已列入“已下架免费额度”名单，老账号未到期额度仍可能可用。因此后续以该账号权益页为准，选定精确模型与版本，不能直接把模型目录中的所有OCR标成免费。此阶段尚未获取账号权益，不发起测试。

## 来源

- 千问仅限：[qwen3.5-ocr](https://www.qianwenai.com/models/qwen3.5-ocr)、[qwen-vl-ocr](https://www.qianwenai.com/models/qwen-vl-ocr)。
- [千问OCR能力与模型选型](https://platform.qianwenai.com/docs/developer-guides/multimodal/ocr)
- [千问OpenAI兼容OCR接口](https://platform.qianwenai.com/docs/api-reference/specialized-models/qwen-ocr/openai)
- [千问免费额度及历史模型额度下架规则](https://platform.qianwenai.com/docs/resources/free-quota)
- 百度4项身份与特点来自用户提供的模型目录JSON，仅提取产品字段，不保存附件中的签名图片URL、授权参数或日志ID。
- [SiliconFlow多模态与OCR](https://api-docs.siliconflow.cn/docs/userguide/capabilities/multimodal-vision)
- [SiliconFlow测试状态](../../siliconflow/PADDLE-VL15-STATUS.md)
- [本系统接口](../api/README.md)、[消费者OCR契约](../api/CONSUMER-OCR.md)、[PP-OCRv4运行记录](../../results/maas-e3-mu-v2-20261030/REPORT.md)。
