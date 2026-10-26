# model-storage-v1 部署与回退记录

适用本轮独立新克隆：公网端口16482、boot 2026-10-26 16:49:33、原生worker5/test1。相同XZ-31-002名称不代表同一个VM。严禁001/002的OS/VM重启；本流程只有已授权的容器维护。

## 文件与契约

maas_bootstrap/storage.py创建并检查独立模型发布目录、缓存及公开marker；client.py增加受控路径设置；worker.py校验新挂载契约和实际布局。公开配置字段model_root=/srv/maas/models、model_cache_dir=/srv/maas/model-cache。签名配置的model_mounts仍不开放任意远程挂载；本次是可信本机启动契约升级，中央配置revision仍为e5-pilot-r1。

本机布局model-storage-v1、MaaS模型revision ocr-e5-r2、管理员的快照版本是不同概念，不得混同。

## 已执行顺序

1. Mac .maas/backups/model-storage-v1-20261026保存旧client/worker/config、旧容器私有inspect、原生token/UUID/name/JWT、旧catalog和维护窗口。私有文件600，不入Git。
2. 暂存新client.py、worker.py、storage.py到节点/root/<name>.storage-v1。install_storage_client.py核对新旧SHA、原子安装、保存节点私有回退文件并准备公开模型目录；不运行apply，不启动worker。新克隆及母版安装均已完成。
3. 新克隆旧布局apply返回NEEDS_MAINTENANCE；这个拒绝是正确结果，不要删身份或强行改标签绕过。
4. 在001执行storage_controller_window.py prepare：核对worker5/model12/cluster3，暂停并排空，启用原生维护、旧副本降0；不能使用旧binding。窗口私有状态/root/wtaobj/maas-e5-storage/window.private.json记录generation23。
5. 新克隆replace_storage_worker.py：核对boot及只剩worker运行；保存私有journal，停止并重命名旧容器；root-only state_dir/maintenance-backups.json只列入旧容器完整ID；调用新bootstrap创建容器并验证身份文件不变、重复应用UNCHANGED。receipt不是自动恢复指令，也不能进入母版。
6. storage_controller_window.py release确认原生state=maintenance且可达，再解除维护，等待ready；MaaS仍暂停。若调用超时，先读取窗口phase与实际worker状态，不直接重新release。
7. e5_ocr_pilot.py create --worker-id 5 --name maas-ocr-e5-r2 --revision ocr-e5-r2 --work-dir /root/wtaobj/maas-e5-storage/model --model-path /srv/maas/models/image-contained/model。
8. 新模型running后，probe_storage_mounts.py验证实际两个共享卷、RO拒绝、缓存读写、身份文件/socket不可见。然后同target脚本activate带--paused-generation 23；保留API key原scope，增加新route许可；原位切换catalog并用真实OCR探针恢复。
9. verify_e5_ocr.py --out /root/wtaobj/maas-e5-storage/acceptance.json --revision ocr-e5-r2完成同步/异步/结果/SQL计量。本轮已通过，禁止为重跑而覆盖既有证据。

当前worker5 ready、model16副本1、旧model12副本0，MaaS未暂停、UNKNOWN=0；旧容器保持停止，供回退。未改Docker引擎、没有自建collector，也没有做VM重启。

## 母版恢复后的待执行步骤

用户确认6025暂不可达，等待通知，不持续重试、不推断母版已更新。恢复后先只读确认hostname属于tower、源码/身份现状及缓存镜像，不能沿用失效主机身份判断。

新包路径dist/e5/e5-worker-template-model-storage-v1-e202112.tar.gz，SHA256 b351d0c68f38c325307dd6686a1f8301c58f137ce110e1da45d188bf1fd2b453；包含storage.py等16项公开载荷。先核对母版旧源文件SHA并备份Mac，再安装三模块及公开路径配置，准备目录，确认母版guard拒绝worker、无native实例身份/维护receipt/运行容器。不要在母版执行apply。源码漂移应停下复核，不强制覆盖。

之后由管理员保存新快照并创建独立克隆，观察首启自动注册和布局，部署模型时不再docker cp补目录。新快照/真实克隆首启尚未执行，不能把本轮容器级迁移写成首启已验证。

## 人工回退方案（未实演）

保持身份数据卷不变，不删除数据库/结果/模型缓存，不执行VM或Docker引擎重启。

1. 先暂停MaaS并确认无活动/UNKNOWN，记录新的pause generation；启用worker维护，model16降0并确认原生推理实例完全停止。
2. 停止新worker容器。按replacement.json的新容器ID核对后备份其私有配置，移除这个已停止的新容器；不要用全局docker rm，也不要删除原生身份数据目录。
3. 将停止的旧容器改回maas-gpustack-worker。若恢复旧引导程序，使用Mac或节点/root/wtaobj/e5-storage-v1-install中的旧client/worker/config，核对SHA和600配置权限；旧bootstrap不支持新布局/备份许可，不能保留另一个冲突worker后盲目apply。手动启动原旧容器并核对原生worker5及身份。
4. 恢复旧model12副本、等待运行及旧route14健康，再从model/switch-backup.json恢复原catalog内容（原位写，保留bind inode）及原key模型许可/scope。不可把旧route不可用当成可恢复流量。
5. 以真实探针恢复MaaS，核对新维护generation、UNKNOWN和计量；任何不确定身份/请求先保留暂停，禁止自动覆盖凭据、重放推理或清除UNKNOWN。旧容器和备份的最终清理另行安排。

此回退只是一份具备具体材料的操作方案，未声称已演练成功。

## 母版实际安装：19:18更新

用户通知恢复后，6025对应tower-icDC5YcU3；旧摘要匹配，Mac私有备份位于.maas/backups/model-storage-v1-20261026/tower-icDC5YcU3。使用上述安装脚本--role template完成三模块和公开目录配置，随后只读核对源码、保护、无实例身份/容器和启动单元enabled。未运行apply或重启服务。此前待恢复段落为历史执行计划，现已完成；下一步管理员保存新快照，创建独立克隆验证首启。
