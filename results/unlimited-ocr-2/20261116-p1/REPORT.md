# Unlimited-OCR-2 首轮实施：P1 受阻后停止

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [执行计划](../../../docs/framework/UNLIMITED-OCR-2-PLAN.md)

用户要求使用 GPT-6.1 Terra Light 子代理推进，实际调用 `gpt-6.1-terra` / reasoning `low`。故障先由子代理尝试处理一次；未解决则主代理最多处理两次，仍未解决就停止，处理成功则交回子代理。

本轮已完成现场只读预检和 P1 静态准备，**未完成候选镜像构建，M1/M2 均未交付**。基础镜像获取故障 UOCR2-INC-003 在主代理两次处理后仍未解决，已按用户规则停止实施。不是模型推理 OOM，也不能据此判断 16GB 不够。

## 已完成的检查与准备

- P0 主机身份、GPU/驱动、空闲容量、Docker NVIDIA runtime、registry TLS/读取、网络规则及策略授权范围见 [P0 报告](../20261116-p0/REPORT.md)。worker 6 是独立候选，正式 OCR 在 worker 5；不使用历史副本数替代新鲜核验。
- 新 PC 私有目录保存了 GPUStack 对象及 MaaS 策略快照并计算摘要；不是数据库备份或恢复演练，未来具体写入前仍需复核覆盖范围与新鲜度。
- 固定模型 revision 为 `0480966938291445591f69f08a13b041c073a96a`，定制 wheel 为 `sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl`，15,286,440 字节，SHA-256 为 `576c71dab3cacad843f6870739494e13b2e8169a69cc038f84395d6503e801ea`。
- 源码确认 v2 构造前父类建立旧语言模型，覆盖前无显式释放；候选补丁仅加入 `gc` 导入、`del self.model` 与 `gc.collect()`，唯一原始上下文匹配后才生成。主代理已审阅，尚未应用到运行镜像；不能写成显存问题已解决。
- 定制 wheel 的原 v2 文件 SHA-256 为 `18b6edfdb97ac30ee93f06318ea4c7e450d3820d1c282f7cd765d1c7ec1e89ab`，候选补丁后文件 SHA-256 为 `c76770d784eca03275fa36a0ffdb3026586b428d08c481ef11e1dceb9225d83e`。
- 固定配置仍声明 `UnlimitedOCRForCausalLM`；wheel 通过 `sparse_attention_config.training_stage=dash` 识别并映射 v2。这是静态匹配证据，不是实际加载证明。
- 候选基础镜像选择官方 CUDA 13.0.2 devel / Ubuntu 22.04，以支持 Python 3.10 及可能的内核编译；amd64 manifest digest 为 `sha256:6b6617592b94e7dcc6ffbe6d00720eed27bc6e3b4f06b26b93b4070c31f57391`。
- torch/torchaudio 等依赖可解性和二进制兼容尚未验证。不能把 torchaudio 元数据未声明 torch 依赖当作兼容证据。

## 故障与处理次数

| 编号 | 故障 | 子代理的一次处理 | 主代理处理 | 结果 |
| --- | --- | --- | --- | --- |
| UOCR2-INC-001 | 旧 SSH MCP inventory 挂起 | 改用已受信 SSH 与新 PC 只读管理接口 | 不需要 | 已取得 P0 证据，继续 |
| UOCR2-INC-002 | legacy Docker builder 不接受 `--cpus` | 查 help，改为 period/quota，但随后又因不支持 `--progress` 失败 | 第 1 次：核对完整 help、上下文及 wheel 哈希，去掉 `--progress`，仅对子进程设置 `DOCKER_BUILDKIT=0` | 已进入 Step 1/7，原命令兼容问题解决，交回子代理；不代表构建成功 |
| UOCR2-INC-003 | worker 访问 Docker Hub `/v2/` 超时，构建在 FROM 失败 | worker 直接访问超时后，改在 Mac 只读验证该端点返回 401；证明有可达替代位置，但尚未交付镜像 | 第 1 次：尝试仅回环的临时双跳 SSH 中转通道，SSH 退出 255，代理探针 connection refused，未建立可用通道 | 未解决，未修改 SSH/daemon 全局配置 |
| UOCR2-INC-003 | 同一镜像获取故障 | 不再重试 | 第 2 次：Mac 使用经官方校验和验证的 crane 获取固定镜像，拟离线交付 | 镜像清单摘要通过；仅取得不完整离线包，连续 354 秒无归档写入进展后终止；未交付 worker，停止 |

第 2 次处理使用官方 `go-containerregistry` v0.20.6 的 Darwin arm64 工具包，校验和 `ae30b28498de3e1ac51a37042eab46116ec70d86aa9d10431a1fbde35b9918b9`。来源：[官方 release](https://github.com/google/go-containerregistry/releases/tag/v0.20.6)。没有安装系统级工具或改变系统代理。

经摘要校验的源 manifest 列出 11 层，压缩层总量 3,960,688,777 字节；config digest 为 `sha256:ec9a1a479ad110600f15a0959a7b4c5bc9ddaed8b34a2d020abb7aea8b5b1707`。Mac 的部分 legacy archive 为 414,546,435 字节，终止后读取确认 `tarfile.ReadError: unexpected end of data`，不能作为完整镜像使用。归档字节与源压缩层大小不是同一口径，不据此计算完成百分比。

## 停止后的现场

- 候选 worker 上仍只运行原 GPUStack worker 容器，GPU compute process 数为 0，候选镜像 tag 不存在；最后构建 exit 1，失败发生在 FROM，未产生可验证的 RUN 容器。
- main 的临时中转 SSH 与 Mac 下载进程均已退出，监测进程已结束；没有后台试点构建、模型容器或推理任务继续推进。此前用户单独建立的 GPUStack Web 通道不属于本次清理范围。
- 原始日志、候选构建上下文、模型/wheel 元数据、候选补丁和不完整离线包保存在受控位置，未加入 Git；不完整包不得导入或标记校验成功。
- 未下载模型权重，未验证 pip check/import、CUDA kernel、容器 cgroup 实际限额、模型加载、显存峰值、Higress 新路由或 MaaS 文档接口。16GiB/4 CPU 是拟定构建限制，未进入 RUN，不能宣称实际生效已验。
- 未修改正式 OCR、GPUStack 路由/策略/权限或 Docker/SSH 全局配置；未重启 VM、Docker 引擎或 worker，没有安装旧 retirement 插件。私有快照及试点临时文件是本次准备产生的文件，不宣称现场文件完全未变化。

## 恢复实施所需条件

本轮停止，不自动尝试第三条路径。后续用户决定恢复时，先解决可验证的基础镜像与依赖供应：提供同一 manifest/config/layers 可核验的完整离线镜像，或明确可用的受控拉取通道，再验证 apt/PyPI 依赖获取。不要仅换 tag、关闭 TLS 校验、开放公网代理或修改正式 daemon 代理绕过。

恢复时先重新核对候选 worker 的身份、GPU 占用、策略范围和快照新鲜度，读取保存的退出状态；从 P1 未完成步骤继续。依赖安装和候选修补的多次启动验证通过后，才进入 P2 真实 GPU 推理。

机器摘要见 [evidence.json](evidence.json)。本报告记录实际检查及停止边界，未将网络获取失败归因于模型容量。
