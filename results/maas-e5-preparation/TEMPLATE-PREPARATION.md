# E5 母版准备：本地材料完成，暂停部署

2026-10-23。用户要求“准备完成后先不要部署到tower”。本轮只在本地构建/验证材料，并通过SSH对tower执行无落盘的只读清单采集。未上传或安装文件，未pull镜像、启动容器、停服务、清理身份、操作快照或创建VM；001002未做任何变更。

## 已交付

- 本地公开交付包：[e5-worker-template-tower-e5-pilot-v1.tar.gz](../../dist/e5/e5-worker-template-tower-e5-pilot-v1.tar.gz)，20227字节，16个文件；source commit `f7de733f7f1d7c7b58380527fd601dc69c811212`。
- SHA256：`cb8dd386020c95db1b79d6bc5d2a8858c3d060ef51fa7accd0703b0d64ba9e71`；见[校验文件](template-bundle.sha256)。压缩包在本地dist目录，不入Git；源码、构建工具和验证证据已提交，可按source commit重建。
- 包含客户端/本机启动脚本、systemd单元、离线Ubuntu安装工具、只读清单脚本、校验器、依赖锁、公开TLS证书/签名公钥、示例设置及交接清单。
- 不含真实共享码、原生token、私钥、Docker镜像、Ubuntu磁盘镜像或平台快照。不向云备用地址请求材料。

## 验证

- 本地bootstrap测试：**34 passed**，一个已有Starlette/AnyIO弃用警告。
- 真实产物解包后15个有效载荷逐文件SHA256通过；打包器测试覆盖相同源码可重复输出、脏源码拒绝、篡改/越界/额外文件拒绝。
- 使用合成注册码在临时Ubuntu文件树执行包内安装工具，通过私有文件权限、systemd启用链接、shell入口语法检查；临时文件已清理，没有获取真实码或运行worker。
- 这不是完整Ubuntu系统镜像、systemd实际开机或GPU容器测试。详见[验证证据](template-bundle-verification.json)。

## tower只读核对与封版剩余条件

| 项目 | 当前观察 | 后续动作（本轮未执行） |
|---|---|---|
| 基础依赖 | Ubuntu24.04 x86_64、Python3.12.3、cryptography41.0.7 | 与当前实现兼容，优先复用现有包 |
| GPU运行环境 | 驱动580.95.05、Container Toolkit1.20.1、Docker28.2.2，Docker已识别nvidia runtime | 新克隆上验证GPU容器，不据此声称容器推理通过 |
| worker镜像缓存 | 固定RepoDigest未缓存，当前无Docker容器 | 获准部署后按摘要预缓存；不用母版注册worker |
| 开机职责 | cybercafe-provision/agent enabled；新E5单元未安装；供应商hostname服务enabled | 核对各自职责，拟定具体停用/保留方案，不盲停供应商服务 |
| 克隆身份 | machine-id已初始化，3个SSH主机密钥，未发现标准路径E5/原生实例状态 | 管理员确定离线清理及克隆首启重新生成流程，保留管理员authorized_keys；此刻不删tower密钥 |
| 镜像构建方式 | 当前工具只支持离线文件系统root；尚未获得实际离线base/导出文件 | 管理员提供离线构建方式；若平台仅支持在线母版热快照，需另行适配并验证该流程 |

只读证据见[template-readonly-inventory.json](template-readonly-inventory.json)。启动单元进一步核对：provision执行`/opt/cybercafe/provision.sh`且先于agent，agent在network-online/Docker/provision之后执行`/opt/cybercafe/cybercafe-agent.py`；供应商swcfghostname位于network-pre之前。旧脚本包含Docker/hostname/systemctl等能力词，仍需完整控制流审查后决定替换范围，不能据关键词直接停用。元数据及源码摘要见[启动职责线索](template-startup-metadata.json)，没有采集脚本中的配置或凭据。清单不是完整秘密/第三方启动钩子扫描，`snapshot_ready=false`。当前不能通知管理员“母版已可制作快照”。

## 后续交接

现在停在用户指定的“本地准备完成，等待部署指令”。获准后先完成启动职责审查、私有码安全注入与母版实际安装/封版前核对；检查通过后才通知管理员保存新快照、创建并首次启动独立A/B。管理员交付入口后，我们完成真实自动注册和GPU/模型验收。不会自动推进tower部署或触发平台操作。

详细动作与责任见[交接清单](../../deploy/bootstrap/clone/HANDOFF.md)。
