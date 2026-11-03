# OpenAPI / Swagger接口文档

> **2026-11-03 代码契约待部署**。OpenAPI 中的 `POST /v1/ocr` 已改为异步提交，需要 `Idempotency-Key`，返回 202 或幂等复用 200。下文现网同步说明适用于切换前。详见[消费者接口](CONSUMER-OCR.md)。

> **2026-11-02现网已发布**：11个操作均已部署，新图片字段与远程URL、E16计量查询已完成真实OCR验收。当前PP-OCRv4 tokens为null/unavailable；Responses中的真实示例已脱敏，历史ID不可复用。

2026-11-02。机器可读文档：[openapi.yaml](openapi.yaml) / [openapi.json](openapi.json)，OpenAPI **3.1.0**，可导入支持3.1的Swagger Editor或Swagger UI。JSON本身就是合法OpenAPI格式，并不要求YAML后缀。

覆盖当前11个客户操作：`POST /ocr`、`POST /v1/ocr`、`POST /v1/chat/completions`、上传/下载artifact、创建/查询/取消任务、模型capabilities和两个计量查询。每个操作有说明、成功/错误响应、请求头/路径参数、请求体和响应schema。共用Bearer鉴权，项目身份由管理员绑定决定。

## 阅读与使用

1. 导入openapi.json；选择HTTP或HTTPS服务器，替换host/port为运维提供的可达入口。
2. 在Authorize中输入已登记推理key，不能使用管理key代替。不把真实key保存进文档。
3. 同步推荐`/ocr`；异步仍为上传原始图片字节→保存artifact ID→带Idempotency-Key提交任务→查询→下载结果。
4. “Try it out”需要浏览器能访问目标地址、证书受信且跨域策略允许。当前入口未配置通用CORS，浏览器调用失败不等于curl不可用；本次不改变CORS、监听或服务器路由。实际调用可按[API手册](README.md)使用curl/Python。

当前未暴露Swagger UI、`/openapi.json`、`/healthz`或`/v1/models`客户路径；不能把文档文件路径当成服务器API。内部FastAPI自动schema未补全所有动态返回，不作为此客户文档替代。三种图片输入及E16计量已于2026-11-02部署；多模型/批次接口不包含在内。

capabilities当前实际返回`execution_modes: ["async"]`，独立同步接口另有提供；此差异如实记录。能力查询不是真实GPU健康检查。错误可能来自代理并返回HTML，调用方需检查HTTP状态和Content-Type；模型输出及运行预算的可配置值以实际环境为准。

## 生成和校验

- 使用项目Python：`.venv-maas/bin/python scripts/api/export_openapi.py`，从当前Pydantic模型导出并补充客户接口语义；不连接数据库或服务器，不改变部署。
- `scripts/api/validate_openapi.py`需要`openapi-spec-validator`和`pyyaml`，用于OpenAPI标准校验并核对当前API路由、入口白名单及操作描述。可在独立工具环境安装，避免改变业务依赖。
- 运行校验器时添加`--write-yaml`可从JSON刷新等价YAML；默认校验两种格式内容一致。

本次为源码核对后的文档交付，不是新一轮在线验收或接口发布。[当前接力状态](../framework/CURRENT-HANDOFF.md)。

## VSCode插件生成curl的注意事项（2026-11-02）

Idempotency-Key已提供非空演示值：每个新操作应替换为新UUID，同次重试保持原值及原请求。artifactReference中的art_REPLACE_WITH_UPLOADED_ID须替换为真实上传ID；inlineBase64为另一可选示例，已部署。Server的localhost不是跨容器地址，请配置实际可达host。

Bearer认证在Authorize或插件的认证配置中填写，生成后检查Authorization头；OpenAPI不会保存真实key，也不能保证所有插件自动附带认证。Accept含text/html源于入口错误媒体类型，不代表业务成功响应是HTML；保留该事实而非删除错误定义来美化curl。支持x-codeSamples的工具另可展示手写curl示例，部分VSCode插件可能忽略该扩展。

手写任务curl示例直接使用`--data`内嵌JSON，不依赖task.json等额外文件。仍须设置Base URL及凭据、替换真实artifact ID，并在同次重试时保持幂等键及JSON不变。

异步远程URL输入及remoteImageUrl对应curl已部署。409可能表示IDEMPOTENCY_IN_PROGRESS（稍后同键同body再提交）或IDEMPOTENCY_CONFLICT（请求不一致）；下载在202前完成且有独立容量限制。现网支持artifact_id/image_base64/image_url，兼容旧别名。

Responses真实示例数据取自[本轮验收](examples/real-responses-20261102.json)，生成器会将其内嵌到YAML/JSON，无外部$ref依赖；节点已脱敏，不包含APIKey或连接地址。默认请求白图与真实响应的自建文字测试图不同。

## OpenAPI Preview空白问题修复（2026-11-02）

本机OpenAPI Preview 2.3.2使用json-schema-ref-parser的bundle处理文档。capabilities的真实响应示例含独立JSON Schema的`$defs`及`#/$defs/...`；插件把实例数据中的引用也按OpenAPI根解析，报`MissingPointerError: Token "$defs" does not exist`，未把spec发送到渲染面板。此前标准OpenAPI校验通过，并不能排除此预览兼容性问题。

现在仅对OpenAPI中的capabilities显示示例，把嵌入schema的局部引用等价内联，保留约束；服务器实际响应及examples/real-responses-20261102.json原始脱敏集合不变。其他OpenAPI组件引用保持规范格式。不改服务、不降级OpenAPI版本、不需要更换插件。

生成后除标准校验外，可用插件自带解析器复验：

```bash
node scripts/api/check_preview_bundle.cjs /absolute/path/to/plugin/node_modules/@apidevtools/json-schema-ref-parser
```

检查YAML/JSON均能bundle及JSON序列化，对应插件发送给界面的流程。本次已使用本机插件依赖实际通过；不将该检查混称为VSCode界面截图验收。修改文件后请关闭旧预览，重新打开openapi.yaml并执行OpenAPI Preview。
