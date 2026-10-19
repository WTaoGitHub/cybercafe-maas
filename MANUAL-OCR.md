# 手动测试 GPUStack OCR

## 1. 建立 SSH 隧道

在本机终端 A 执行并保持运行：

```sh
ssh -N -L 127.0.0.1:18080:127.0.0.1:18080 -p 2023 -i ~/.ssh/id_ed25519 root@111.4.255.126
```

如 18080 已占用，可把左侧端口改成 18081，后续 --base-url 对应调整。
浏览器打开 http://127.0.0.1:18080 ，用户 admin。
初始密码可在自己的终端读取（不要提交进仓库或聊天）：

```sh
ssh -p 2023 -i ~/.ssh/id_ed25519 root@111.4.255.126 'cat /root/wtaobj/gpustack-server/initial_admin_password'
```

如已经改过密码，应使用新密码。管理界面中查看 Model Deployments / Model Routes，rapidocr-gpu 应有两个运行中的副本和同名路由。

## 2. 调用本地图片

在终端 B：

```sh
cd /Users/4paradigm/dev/cybercafe/wtaobj
python3 ocr_client.py /absolute/path/to/image.jpg --username admin --output results/manual-ocr.json
```

按提示输入管理密码，输入不会回显，不会写进脚本或命令行参数。
终端显示文字、耗时和执行 GPU UUID；JSON 还包含文本框和置信度。
若本机没有 python3，可使用本次环境的 Python：

```sh
/Users/4paradigm/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 ocr_client.py /absolute/path/to/image.jpg --username admin --output results/manual-ocr.json
```

也可在 GPUStack 的 API Keys 页面创建测试用 API key，然后运行下列命令并按提示输入 key：

```sh
python3 ocr_client.py /absolute/path/to/image.png --output results/manual-ocr.json
```

该方式不需要 --username。客户端优先使用环境变量 GPUSTACK_API_KEY；未设置时安全交互输入。
输入目前只支持 JPG/PNG，文件不超过 10MB，解码后不超过 2000 万像素；PDF 需另做页渲染。
0.2 适配层支持 Chat Playground 的 stream=true：完成识别后以 SSE 返回纯识别文字。非流式客户端仍返回完整 JSON。新镜像需要在 GPUStack 切换后才生效。

## 3. 请求格式

接口为 POST /v1/chat/completions，model=rapidocr-gpu，stream=false。
messages[0].content[0].type=image_url，image_url.url=data:image/png;base64,...。
响应 choices[0].message.content 是包含 text、lines、elapsed_ms、node 的 JSON 字符串。
该接口只是 OCR 的传输适配，不运行语言模型，也不提供真实 token 用量。

## 故障排查

- SSH 登录失败：检查本地私钥和 2023 端口。
- 连接拒绝：先确认隧道存活及本地端口正确。
- 401：API key 或账号密码无效。
- 404：检查 rapidocr-gpu 模型路由。
- 503：副本不可用或正在恢复；本次已观测故障窗口，当前客户端不会静默重试。

## 已验证

客户端已在 001 上通过统一入口完成合成图片识别并返回 GPU UUID，输出到远程 /root/wtaobj/manual-client-test.json。

## Chat Playground（部署适配层 0.2 后）

1. SSH 隧道连接后，进入 Playground → Chat。
2. 选择 rapidocr-gpu，使用 Upload Image 上传 JPG/PNG。
3. 输入“提取图片里的文字”并发送；文字指令不改变 OCR 模型行为。
4. 页面显示识别文字。识别完成后才开始发送 SSE，不是逐字执行 OCR。
5. 不上传图片会返回 400；多轮对话不提供语言理解能力。

流式响应不伪造 token usage；非流式响应保留文字、坐标、置信度、耗时和 GPU UUID。
