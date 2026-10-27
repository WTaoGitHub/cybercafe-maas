# 消费者OCR兼容门面发布记录

2026-10-27，源码feature9468518。API镜像cybercafe-maas-tasks:consumer-ocr-r1，实际image ID见results/maas-consumer-ocr/deployment.json。仅001 API容器重建；HTTP/TLS两个Nginx原位更新路径白名单并reload，无VM/Docker引擎/worker/执行器/observer/数据库重启。

## 发布与验证

1. 在核对过的e6-r1镜像82c4588a…上离线增加Pillow13.0.0及当前maas_platform，HTTPX/httpcore版本保持0.28.1/1.0.9。标准requirements与Dockerfile同时支持全新构建，Dockerfile.consumer-ocr用于本次增量离线构建。
2. 本地186通过、56因无测试DB跳过；独立PostgreSQL maas_test中原有集成与新消费者契约合计89通过、1跳过（历史DB中断harness）。不使用正式任务库或GPUStack库做破坏性测试。
3. ROOT=/root/wtaobj/maas-consumer-ocr-r1，私有rollout.private.json、backup及旧API inspect优先备份到Mac .maas/backups/consumer-ocr-r1-20261027。无DB schema变更，新旧migration字节核对一致。
4. deploy_consumer_ocr.py prepare确认无排队/在途/UNKNOWN，pause generation29；只设置MAAS_API_IMAGE覆盖API，MAAS_TASK_IMAGE仍e6-r1。compose up仅指定api、no-deps/no-build/pull never，不重建其他服务。
5. 实际HTTP配置/root/wtaobj/maas-edge/nginx.conf及TLS配置/root/wtaobj/maas-private-tls/nginx.conf都在保持inode的前提下增加/ocr和/v1/ocr白名单，nginx -t后reload；仅修改仓库模板不足以更新运行中的Nginx。
6. 两个新增TLS路径在暂停时均503/MAINTENANCE_PAUSED，证明路径已放行且受统一门禁。原Chat恢复探针46行通过，generation30恢复接单。
7. verify_consumer_ocr.py通过实际TLS发送裸base64、data URL alias、公开HTTPS图片URL、空白PNG，成功事件增量4对应4张；再验证输入错误/私有URL/鉴权拒绝。verify_e5_ocr.py另回归原Chat、异步任务、结果下载和单次计量。

MAAS_API_IMAGE只选控制端API镜像，不是推理引擎镜像，也不改变GPUStack/母版/克隆镜像。常规compose启动会保留这个API覆盖；不要忽略app.env中的覆盖字段而误认为所有组件都运行同一个应用镜像。

## 当前入口及范围

POST /ocr与/v1/ocr；其余现有API保留。入口仍回环HTTPS18832/HTTP18831，API18830和DB18835仍回环，未新增LAN或公网客户监听，TLS证书未改变。worker7 boot和容器ID核对保持，新克隆ACL待办未在本次处理。

调用方使用现有登记key规则，真正新消费者还需开通自己的credential/project/逻辑模型权限；本次复用pilot key做验收，没有替消费者创建正式账号或发送密钥。协议参数、公开URL限制、language提示及E4幂等边界见docs/api/CONSUMER-OCR.md。

本次没有文生图/音视频服务、同步幂等、消费者自有网关源码联调或新的8小时耐久验收。外部消费者生产部署仍需安排可达通道及证书信任，不能将回环地址直接填在远程机器上。

## 回退方案（材料已保留，未实际回退）

先pause并确认无在途/UNKNOWN、记录自己的generation，不能盲目重放未知请求。按rollout.private.json files映射恢复旧app.env、compose和已修改旧源码；恢复旧HTTP/TLS配置须原位写入并nginx -t/reload，保留旧有效证书。用旧compose/env只重建API到原e6镜像82c4588a…，其他组件和业务卷不变。新增加但不再被旧API导入的公开模块可暂留，不做全局清理。

旧Chat/异步真实探针通过后才resume。回退会移除新增兼容接口，不能在已有消费者依赖时无告知切回。数据库schema没有变化，不应回滚数据库或删除任务/artifact/计量；本轮产生的结果和事件继续保留。

脚本按阶段执行；异常/超时先查private phase和实际容器状态，再决定人工恢复，不能重跑prepare覆盖旧材料、重复发送验证请求或自动解除UNKNOWN。
