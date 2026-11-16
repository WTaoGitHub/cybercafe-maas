# CUDA 基础镜像：Mac 离线交付与新 PC registry 发布

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [执行计划](../../../docs/framework/UNLIMITED-OCR-2-PLAN.md)

用户在[首轮停止](../20261116-p1/REPORT.md)后授权再次从 Mac 下载并离线交付，并指出镜像应保存在新 PC registry。本轮已完成：Mac 固定版本下载与校验、候选 worker 离线导入、新 PC 私有 registry 发布，以及 worker 使用已有 reader 凭据按仓库摘要拉取。没有重新构建 Unlimited-OCR-2 服务镜像或运行模型推理。

## 可使用的镜像与摘要

新 PC 私有 registry 的 repository 为 `maas/base-cuda`，tag 为 `13.0.2-devel-ubuntu22.04-uocr2`。实际构建应使用下列固定引用，registry authority 从受控部署配置取得，不写入本仓库：

```text
<REGISTRY_AUTHORITY>/maas/base-cuda@sha256:00fde7d4b087eb46c66aecebba22543c978f9071fbd0270a7a4c54d7da756dcb
```

| 对象 | 已验证内容 |
| --- | --- |
| 上游镜像 | `nvidia/cuda:13.0.2-devel-ubuntu22.04` 的固定 Linux amd64 内容 |
| 上游 manifest | `sha256:6b6617592b94e7dcc6ffbe6d00720eed27bc6e3b4f06b26b93b4070c31f57391` |
| 新 PC registry manifest | `sha256:00fde7d4b087eb46c66aecebba22543c978f9071fbd0270a7a4c54d7da756dcb` |
| config / image ID | `sha256:ec9a1a479ad110600f15a0959a7b4c5bc9ddaed8b34a2d020abb7aea8b5b1707` |
| Mac 离线包 | 3,960,720,896 字节，SHA-256 `af90c8b24a899deae2a11c8eef3d497c4ffef32d11c83f6c5d4c6099b2e9a7bc` |
| 层数 | 11；Mac 逐层压缩内容摘要与上游逐项匹配，离线导入后的 11 层 RootFS 与源 config 相同 |

**上游与私有仓库的 manifest 摘要不同，image ID 保持相同。** Skopeo 从 Docker 离线包重建镜像表示，首次发布检查发现层摘要不同，因此没有错误宣称原 manifest 已原样镜像。随后保留原始来源记录，核对 config ID，发布并读回实际仓库 manifest，再通过 worker 的 Docker 拉取确认相同 image ID 与 11 层 RootFS。后续引用私有仓库必须使用 `00fde…`，不能把上游 `6b661…` 拼到私有仓库路径。

## 实际操作与验证

1. 在 Mac 使用此前已校验官方工具包的 crane v0.20.6，以压缩 `tarball` 格式和独立层缓存重试固定源镜像；下载 exit 0，耗时 1,372 秒。保留之前的不完整包及停止记录，不将其当成本次有效输入。
2. 校验源 manifest、config、11 个压缩层的大小与 SHA-256，再计算完整离线包 SHA-256。旧 legacy archive 的写入停滞与当前压缩格式的进度分开记录，不把旧文件视为完整镜像。
3. 重新经受信 SSH 和已认证的 worker 身份接口核对 worker 6 的 GPU UUID/name/external_id、Ready、空闲 GPU 和磁盘。上传后接收端独立计算离线包 SHA-256，Docker load 成功，检查 image ID、amd64/Linux 和完整 RootFS 层列表。
4. 将同一包经已核验主机公钥的 SSH 跳板连接上传新 PC，独立校验 SHA-256。publisher 凭据仅在新 PC 本地使用，临时 authfile 为 0600，使用后删除；未复制到 worker 或模型镜像。
5. 使用新 PC 已有 skopeo 和 registry CA 发布新 repository 下的 staging tag，再发布实际 manifest 对应的固定 tag；按 digest GET 返回 200 且内容摘要一致。首次上游/仓库层摘要不一致的检查记录保留，不覆盖为成功。
6. worker 使用现有 bootstrap reader 配置和 registry CA，先 GET 指定 manifest，再执行 Docker pull；TLS 与 reader 鉴权通过，RepositoryDigest 与实际仓库 manifest 相符，image ID 未变。该 worker 先前已有离线导入内容，这是带缓存的拉取验收，不是冷拉取吞吐测试。

worker 的本地辅助 tag `maas-uocr2-cuda-base:13.0.2-ec9a1a479ad1` 仅用于离线接收核验；新 PC registry 是后续 worker 拉取的统一来源。原始包、层缓存、私有操作脚本及失败/成功日志均留在受控目录，未加入 Git。

## 当前边界与下一步

- 本轮解决的是 CUDA 基础镜像供应。完整 Unlimited-OCR-2 服务镜像仍需在 P1 固定依赖、安装定制 wheel、应用候选补丁并实际验证，再推到新 PC registry 的独立服务镜像 repository；正式部署拉取最终服务镜像运行，不在每个 worker 启动时在线安装依赖。
- 基础镜像已存在，不代表 apt/PyPI 下载、依赖组合、CUDA kernel、初始化显存修复或 16GB 模型容量已通过。P1 整体及 M1/M2 均未验收。
- 本轮没有启动推理容器或 GPU 任务，没有修改正式 OCR 的路由、权限或副本策略，没有重启 VM、Docker 引擎或 worker，也未安装旧 retirement 插件。worker 拉取后仍只有原 GPUStack worker 容器，compute process 数为 0。
- 后续继续 P1 时，重新核对现场，并将构建基础引用改为上面的私有仓库实际 digest；不要重用旧脚本中直连 Docker Hub 的 FROM，也不要直接恢复此前失败的构建命令。

机器证据见 [evidence.json](evidence.json)。原始连接信息、凭据和私有日志不发布。
