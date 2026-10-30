# PaddleOCR-VL-1.5 测试状态

2026-10-30。用户要求停止，测试已结束，无本轮遗留的本地请求进程，不再追加调用。客户端超时不证明服务端推理已经结束。

- 模型：`PaddlePaddle/PaddleOCR-VL-1.5`。
- 接口：`https://api.siliconflow.cn/v1/chat/completions`，本地`.mykey`认证。
- 输入：与DeepSeek测试相同的1400×620中英文合成PNG；base64输入。
- 参数：非流式，temperature=0，max_tokens=2048；提示词`OCR:`。
- 已完成：并发1、独立请求4次，无失败重试，总耗时240.68秒。
- 结果：4次均为客户端读取超时（每次约60秒，`The read operation timed out`）；未取得HTTP状态、OCR结果或token用量。记录中的status=0是脚本本地错误标记，不是HTTP状态码。
- 并发2/4/8/16/32均未启动，吞吐量和能力上限未知。不能将本次零成功解释为该模型的真实处理能力为零。
- 随后已完成1次DeepSeek-OCR对照请求：HTTP 200，约1.04秒。用户发出停止消息时该调用已结束，之后未追加请求。

粗略判断：同机同Key同图片下，DeepSeek接口可用而Paddle本轮持续超时，问题更偏向Paddle服务或该请求的处理路径；不能凭此确定根因、认定模型下线或评估整体平台容量。对照调用未排除所有时序性网络因素。

用户提供的定价JSON显示输入/输出单价均为0；本次未做账单核验。

原始记录仅本地保存（被Git忽略）：

- `local-results/service-benchmark/paddle-vl15-c1.json`
- `local-results/service-benchmark/deepseek-control-after-paddle.json`

复用工具已增加`--model`、`--prompt`和`--label`，避免覆盖此前DeepSeek测量结果。本轮停止后不要自动重跑。
