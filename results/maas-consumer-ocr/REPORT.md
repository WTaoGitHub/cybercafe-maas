# 下游OCR协议适配验收 · 2026-10-27

结论：消费者文档的同步OCR门面已实现并在001私有入口发布，POST /ocr和/v1/ocr等价；原Chat和异步接口保留。当前可对齐OCR协议，但文生图/音视频、E4同步幂等、远程消费者网络开放和消费者自身网关实机联调不在本轮完成范围。

## 接口

model=ocr.default，image_base64/image_url恰好一个；裸base64和PNG/JPEG data URL可用，URL仅公开HTTP(S)80/443且不跟随重定向。语言auto/zh/en为提示，不提供检测或回显；return_boxes默认true，false省略polygon；request_id仅日志关联，不去重。

输出顶层lines，score→confidence、box→polygon；空白图lines=[]成功。输入最多10MiB、2000万像素，JSON预算20MiB；下载/解码10秒预算、推理采用模型25秒预算。共享现有准入、维护、UNKNOWN和成功计量，转换校验在写成功事件前完成。

## 实测

| 用例 | 结果 |
|---|---|
| TLS /ocr + 裸base64 | 200，46行，confidence/polygon通过校验 |
| TLS /v1/ocr + data URL + return_boxes=false | 200，46行，polygon省略 |
| 公开HTTPS image_url | 200，Python官网公开logo识别1行；真实下载开启TLS校验 |
| 空白PNG | 200，lines=[] |
| 成功计量 | 四张成功图片对应四条sync_usage_events，失败请求不增加成功事件 |
| 无效base64/私有元数据URL/双图片来源/不支持语言 | 422，错误结构合规 |
| 无Bearer | 401 |
| 两个新增路径处于维护窗口 | 均503/MAINTENANCE_PAUSED |
| 原Chat + 异步 + 下载/计量 | 200/46行，异步SUCCEEDED/attempts1，SQL成功1/usage事件1 |
| 本地与隔离DB | 本地186通过/56跳过；隔离DB及相关单元89通过/1历史harness跳过；包含坏输出不计费、DNS固定/原TLS Host保留、共享配额及UNKNOWN不重放 |
| 服务与监听 | MaaS接单恢复、UNKNOWN=0、monitor ok；仅API重建与Nginx reload，所有客户监听仍回环 |

部署及证据：[deployment.json](deployment.json)、[暂停门禁](paused-gates.json)、[新契约](acceptance.json)、[旧API回归](legacy-acceptance.json)、[日志关联](log-correlation.json)、[worker保持](worker-unchanged.json)、[最终状态](final.json)。

源码功能提交9468518。回退与私有材料说明见[发布手册](../../deploy/maas/CONSUMER-OCR.md)，对接参数见[接口文档](../../docs/api/CONSUMER-OCR.md)。本次回退未实演，没有重新创建母版/快照或修改GPUStack、worker、执行器/observer和数据库。注册给真实下游的独立key/project仍需按既有账号开通流程办理，不使用管理员凭据。
