# Unlimited-OCR-2 P1 固定环境与私仓发布

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [计划](../../../docs/framework/UNLIMITED-OCR-2-PLAN.md)

P1 已完成固定候选环境构建、依赖检查、模型文件校验和私有 registry 发布；GPU kernel、模型加载和输出仍由 P2 验证，不能将本记录当作 M1 验收。

## 固定输入与模型

- HF 模型 revision `0480966938291445591f69f08a13b041c073a96a`。worker 到 HF 直连超时，改用 ModelScope 固定 revision `410c3a90d307b128e2d957aee2ea8a5e85222bfc`。两个权重分片的 ModelScope SHA-256 与 HF LFS 摘要一致；其余 17 个文件以 HF 固定 revision 实际内容计算 Git blob SHA-1 匹配 HF 标识，文件 SHA-256 匹配 ModelScope 清单。19 个实际下载文件均验证大小和 SHA-256，合计 6,699,209,288 bytes，详见[文件清单](model-manifest.json)。
- 模型 card 标识 MIT；所用定制 wheel 为 `sglang-0.5.20.dev13+g75b9b06b02-py3-none-any.whl`，SHA-256 `576c71dab3cacad843f6870739494e13b2e8169a69cc038f84395d6503e801ea`。固定 config 以 `training_stage=dash` 触发 wheel 的 v2 架构映射，该判断仅为静态代码核对。
- 上游客户端与后处理来源固定为 [Unlimited-OCR commit 64e6f066](https://github.com/baidu/Unlimited-OCR/tree/64e6f066023b305ab03860603a9cf358a8b70148)：`infer.py` SHA-256 `59f8779715b0ef02632ea5863e1078669c8c960f836e5cb0c193daf9790799e2`；`utils/process_unlimitedOCR2.py` SHA-256 `fb38738cd5f09625fb1095d201533b6059a85820bca99c4262347cc7b078a782`。受信请求的 processor 来自同一固定 wheel；原始自定义 processor 反序列化使用 dill，不能开放给不受信下游。

## 构建和发布

[构建文件及版本清单](../../../deploy/unlimited-ocr-2/README.md)提交于 `8838aea`。基于私仓固定 CUDA 基础 manifest `sha256:00fde7d4b087eb46c66aecebba22543c978f9071fbd0270a7a4c54d7da756dcb`，worker 6 使用版本清单完成隔离复构，`pip check` 通过，torch `2.13.0+cu130`、torchaudio `2.11.0+cu130`、transformers `5.12.1`、flashinfer `0.6.18` 和 v2 模块在无 GPU 容器中导入，torchaudio 原生扩展可用。由于没有给导入容器暴露 GPU，CUDA 架构未检测到属该检查边界，不构成 GPU 兼容结论。

候选补丁对 wheel 内 v2 文件原始 SHA-256 `18b6edfdb97ac30ee93f06318ea4c7e450d3820d1c282f7cd765d1c7ec1e89ab` 做 guard，在父类构造后删除旧 tower 并执行 `gc.collect()`；补丁后文件 SHA-256 `c76770d784eca03275fa36a0ffdb3026586b428d08c481ef11e1dceb9225d83e`。固定原文件的单次应用与重复应用拒绝已测试，显存释放效果仍未实测。

| 对象 | 摘要 |
| --- | --- |
| 服务镜像 config / image ID | `sha256:f46141ad398a875a4b2eceec43dc75bfc7577b7dfb5a08864dce7a3972f18abd` |
| 私仓服务镜像 manifest | `sha256:ef36987a1f7aacaaf0a5ea89db39aa869262a7741b2762561922160b1fc5524c` |
| 私仓 repository | `maas/unlimited-ocr-2`，固定 tag `p1-20261116-r1` |
| 上游源码 | `64e6f066023b305ab03860603a9cf358a8b70148` |

服务镜像压缩离线包跨受信路径传递并逐端校验 SHA-256；新 PC 使用本地 publisher 发布到独立 repository，临时认证文件已清除。worker 6 使用已有 CA 与 reader 实际按 digest pull，TLS、manifest、config ID、17 层及 RepositoryDigest 匹配；镜像已在该 worker 缓存，此项不是冷拉取测试。构建 RUN 实际 cgroup `memory.max=17179869184`、`cpu.max=400000 100000`，即 16 GiB/4 CPU。传输专用临时 SSH key 和 worker 限制授权已删除并核对。

Python 包按版本固定，wheel 和基础镜像按摘要固定；各分发包哈希及 apt 快照未全锁，不能宣称源码字节级重建。构建与发布未修改正式 OCR、未开放公网服务、未运行 GPU 推理或重启 VM/Docker。P2 须重新核对身份、端口和空闲状态，实际验证 CUDA、模型、输出和资源；共享 `/dev/shm` 4 GiB 是试点配置，须按实际页面采样验证。
