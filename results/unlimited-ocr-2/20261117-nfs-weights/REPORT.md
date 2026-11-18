# Unlimited-OCR-2 权重复制到新PC NFS模型库

2026-11-17执行，2026-11-18补记 · [Issue #6](https://github.com/WTaoGitHub/cybercafe-maas/issues/6) · [模型服务 Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11)

**复制及文件访问验收通过。** 固定模型revision为`0480966938291445591f69f08a13b041c073a96a`，从原模型worker复制完整19个文件到新PC；总计6,699,209,288 bytes（约6.70 GB）。包括两片safetensors权重、索引、配置、tokenizer及模型代码，文件清单与[既有固定版本manifest](../20261116-p1-build/model-manifest.json)一致。

| 位置 | 路径与状态 |
| --- | --- |
| 原模型worker来源 | `/srv/maas/models/unlimited-ocr-2-0480966`；复制前19个文件的大小与SHA-256全部匹配 |
| 新PC NFS共享目录 | `/root/maas/data/modelsWeights/unlimited-ocr-2-0480966`；复制后19个文件的大小与SHA-256全部匹配 |
| 用户指定的新worker宿主入口 | `/srv/maas/models/unlimited-ocr-2-0480966`；经既有只读NFSv4挂载完整读取，19个文件的大小与SHA-256全部匹配 |

先检查目标版本不存在及可用空间，再传输到共享目录外、同一文件系统的独立暂存目录。完整校验、同步文件及设置新模型目录0755/文件0644后，以不覆盖目标的目录重命名发布。未覆盖已有版本或调整共享根目录及其他模型的权限；未改变NFS export、访问名单、启动或母版配置。

客户端校验前后核对`/srv/maas/models`的实际NFSv4来源一致且挂载为`ro`，再对全部19个文件执行完整SHA-256校验。该结果证明指定worker宿主可读取新发布版本；不是仅检查服务端目录或文件名。结构化结果见[VERIFICATION.json](VERIFICATION.json)。真实连接信息、认证材料和原始操作日志留在私有目录，不纳入本报告。

后续在用户指定的新worker部署时，应引用上述宿主模型路径，并另外验证推理容器内的真实NFS来源、只读映射、启动参数、资源限额及实际推理。原服务的本地权重和部署未修改；本次没有在新worker创建模型实例、加载模型或发送推理请求。宿主文件校验不能替代容器及服务验收，也不作为冷启动性能测试。

本轮未执行VM重启、还原或其他等效操作；传输使用的临时SSH agent已停止，服务端临时信任文件已移除。
