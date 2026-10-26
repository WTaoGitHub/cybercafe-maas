# 新克隆上的OCR端到端试点

控制端001运行GPUStack v2.2.3和现有MaaS；本轮独立新克隆使用端口16482、hostname XZ-31-002、worker ID5、test1(cluster3)。不能据hostname把它当成旧VM。001/002均不执行整机重启；本流程也不重启Docker引擎，不恢复自建collector。

## 镜像与部署边界

- 复用已验证的quay.io/cybercafe-maas-ocr:p2-r1，完整本地image ID为sha256:8eed873554282ec02ba4d8d3a78f8230178cef2ee4f6f94ca6dfb858336e89e5；权重在镜像内。
- 在001导出docker save压缩包，只向新克隆传输非秘密镜像；临时HTTP服务绑定10.11.31.1:18841，仅允许10.11.31.2访问一个固定文件，无目录列表。传输后停止该临时服务，不作为正式registry。
- 新克隆运行fetch_e5_image.py校验压缩包SHA256、docker load、完整image ID；成功后删除克隆上的传输归档，保留镜像和transfer.json。不在母版加入模型或实例身份。
- OCR程序和权重均在镜像内。执行stage_e5_ocr_metadata.py，将公开deployment.json放在宿主机及现有worker容器的/opt/maas-models/ocr-e5-r1/model，让local_path检查通过。更正：实际官方worker默认mirrored=true，OCR容器继承原生数据卷，而非只挂载此公开目录父目录；此前对挂载范围的推断不成立。既有固定worker镜像及身份不改。
- 当前bootstrap没有独立模型目录挂载。此步向现有worker文件系统复制公开元数据，不是持久化模型卷；worker容器重建或另一新克隆需要重新准备。必须保留此试点边界，后续实现规范模型目录/分发方案。

## 分阶段执行

1. 核对新worker ID/cluster/ready和镜像ID。仅完成静态编译检查不能替代部署验收。
   先在独立新克隆执行stage_e5_ocr_metadata.py，再创建模型。首次使用旧/root/wtaobj/gpu-ocr路径曾因worker容器看不到目录而失败；修正路径后仅移除失败实例93，由GPUStack创建实例94，未重建worker或重放推理。
2. 在001执行python3 /root/wtaobj/maas-framework/scripts/maas/runtime/e5_ocr_pilot.py create --worker-id 5。独立创建maas-ocr-e5-r1模型，复用已有custom backend，单副本并按worker-name标签限定到当前worker；不迁移或删除旧cluster1模型。创建控制响应不确定时先核对target.json和GPUStack记录，不自动重发POST。
3. 等GPUStack instance=running、model ready_replicas=1，核对节点、端口和GPU providers。尚未ready不得切换MaaS。
4. 执行同脚本activate：登记新静态pin/revision ocr-e5-r1，备份旧catalog及推理key允许的模型名，确认无排队/活动/UNKNOWN后暂停、排空；保留旧key许可并增加新route；在暂停时原位写catalog，保证现有文件bind mount可见；用真实OCR探针恢复。
5. 不重启业务容器或DB，不改变旧route的含义；客户模型名仍为ocr.default，上游切到独立maas-ocr-e5-r1，revision改为ocr-e5-r1。
6. 执行python3 /root/wtaobj/maas-framework/scripts/maas/runtime/verify_e5_ocr.py：私有TLS验证、单次同步OCR、上传输入、单次异步提交、只读轮询、结果下载、revision/节点一致性、任务成功与一次usage事件SQL核对。推理发送后不自动重放，失败保留阶段证据。

## 证据与回退

001的/root/wtaobj/maas-e5-ocr保存target.json、pin.json、switch-backup.json、acceptance.json；秘密不得打印或复制入Git。本机.maas/backups/e5-ocr-activation-20261026保存旧catalog，后续复制私有回退材料也优先放Mac。公开结果只记录资源ID、镜像摘要、状态、行数和节点，不记录API key或图片base64。

激活失败保持暂停，不自动恢复流量或解除UNKNOWN。先查活动请求/任务及新route状态，再决定修正或回退。回退应暂停并确认排空、从switch-backup.json恢复原catalog内容（保持原文件inode）及原key模型许可；旧route当前无ready target时不能因配置恢复就宣称服务恢复，不强制resume。新模型/route、输入和结果保留用于取证，删除须单独按资源ID操作。

这次是单克隆端到端试点；不代表双克隆并发、大规模容量、容灾或生产上线验收。正式模型分发/共享存储/registry仍需后续设计，不能把本次临时传输写成自动分发已完成。

旧常驻维护binding仍指向worker ID2、model IDs[1,9]；不能直接用于新ID5/model12。先更新维护目标及SSH端口16482并作校验，再安排维护演练。不要仅按复用的XZ-31-002名称或10.11.31.2地址复用旧维护操作。

仓库/模型目录的正式方案以[GPUStack v2.2.3原生能力核对](../../docs/framework/GPUSTACK-223-DISTRIBUTION-REVIEW.md)为准：复用原生registry pull、mirrored mounts及模型下载；补独立模型挂载并排除原生身份目录，不再以docker cp/临时HTTP作为正式分发方案。当前环境尚未实施这项整改。
