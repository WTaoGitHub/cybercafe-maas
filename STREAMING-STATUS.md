# Playground SSE 适配状态

- 源码：gpu-ocr/server.py、gpu-ocr/ocr_protocol.py。
- 两台节点已预加载 quay.io/cybercafe-gpu-ocr:0.2，保留 0.1 便于回退。
- 完成两个协议单元测试；001 的独立探针已完成12次真实图片流式调用、非流式回归、缺图400验证。
- 原始结果：results/streaming-direct-verification.json。
- 已清理独立探针容器；已通过 API 管理权限将线上两个副本滚动切换到 0.2。
- 已使用用户更新的 API key 完成部署管理及统一入口验证；未重置用户密码。
- gpustack_admin.py 现在支持 GPUSTACK_API_KEY_FILE（默认远程 /root/wtaobj/.gpustack-api-key）、GPUSTACK_API_KEY 和 GPUSTACK_PASSWORD_FILE。
- upgrade_ocr_stream.py 用于添加0.2后端版本并切换OCR部署。
- verify_streaming.py 不带 --direct-base 时测试 GPUStack 统一入口及双节点分发。
- 用户已确认 Playground 图片上传、OCR 请求到显示结果完整流程通过（2026-10-19）。

识别完成后才发送SSE，事件包括assistant角色、纯识别文字、finish_reason=stop以及[DONE]。
不伪造token用量；非流式JSON格式保留。具体页面操作见MANUAL-OCR.md。
