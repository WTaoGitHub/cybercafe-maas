# 异步图片输入字段统一命名

> **2026-11-02当前已发布ocr-metering-r2**：规范字段image_base64/image_url、旧别名兼容、异步远程URL及E16两个计量查询均已完成真实验收。4异步+3同步成功，7条唯一收据；当前tokens实际null/unavailable，不能按tokens实际收费。generation488、监控健康、UNKNOWN=0，8副本profile保持，无VM/worker/Docker引擎重启。OpenAPI已内嵌真实脱敏响应；以下待部署状态为历史。[发布报告](../../results/maas-ocr-release-20261102/REPORT.md)。


2026-11-02，用户要求将data_url改为image_base64、url改为image_url，保持input.image层级。状态：代码与测试完成，未部署；现网仍为artifact_id或data_url，远程URL能力亦尚未发布。

新规范字段三选一：

```json
{"input":{"image":{"artifact_id":"art_xxx"}}}
```

```json
{"input":{"image":{"image_base64":"data:image/png;base64,..."}}}
```

```json
{"input":{"image":{"image_url":"https://example.org/image.png"}}}
```

image_base64与同步OCR语义一致，接受严格PNG/JPEG裸base64或data URL，仍校验10MiB/20MP。image_url沿用远程安全下载、准入、幂等、配额及错误规则。原Chat messages[].content[].image_url.url属于上游Chat协议，不改名。

为兼容现网已发布的data_url，新代码保留旧data_url作为image_base64的弃用输入别名；url同样作为image_url的弃用别名。新旧同义字段或不同来源同时提供均422。capabilities/OpenAPI只公布新规范字段；旧别名作为兼容说明，不作为推荐schema。已有任务已规范化为artifact引用，无数据库迁移。

保留原始TaskCreate.input参与幂等指纹，别名不改变既有已受理请求的指纹。重试必须保持原key与原请求：同键从旧字段换成新字段、或裸base64换成data URL，均为内容变更409，不提供跨表示自动合并。

本地76项通过；独立PostgreSQL/API+mock推理105通过、1旧条件跳过。覆盖新名称联合schema、旧别名与互斥、裸base64、幂等重复、结果计量、URL锁/下载/安全回归等。OpenAPI与三种示例同步校验。原始测试日志存Mac私有目录，无现网变更、无VM/worker/Docker引擎重启。

后续按受控发布流程将字段改名与异步URL能力一并验证；发布前不可在现网直接使用新字段。旧参数和现网发布证据保留历史，不改判旧测试。
