# OCR 真实图片基线评测

报告生成：2026-10-19T07:38:46.634658+00:00

固定模型 rapidocr-gpu；并发 1、无自动重试；40 张校准集 + 160 张冻结评估集。调用本机 SSH 隧道，经 GPUStack 统一入口路由到两台 worker。未调整 OCR 模型或预处理。

| 集合 | 来源 | 成功/请求 | 归一化分组 CER | 严格分组 CER | 客户端 P50/P95 ms | 服务端 P50/P95 ms |
|---|---|---:|---:|---:|---:|---:|
| calibration | doclaynet | 20/20 | 7.92% | 10.28% | 534.7/716.46 | 313.21/418.57 |
| calibration | rctw17 | 20/20 | 18.60% | 19.66% | 456.39/960.29 | 115.3/154.35 |
| evaluation | doclaynet | 80/80 | 14.74% | 17.52% | 426.51/656.05 | 236.27/394.9 |
| evaluation | rctw17 | 80/80 | 18.47% | 19.15% | 443.69/666.74 | 119.31/163.75 |

## 指标口径与限制

- 按原图坐标匹配区域：交集面积 / 两框较小面积 ≥ 0.5 时建立连接；连通的参考框与预测框合为一组，以容纳拆行与合行。每组按近似水平阅读顺序拼接。未匹配参考计删除，未匹配预测计插入。
- 优先匹配有效参考；其余预测若超过自身面积 50% 落在 ignore 区域则排除。零面积参考框单独计数并排除空间评分。
- 严格 CER 保留区域文本空白、大小写及标点；归一化 CER 使用 Unicode NFKC、casefold、删除所有空白，保留标点。逐组编辑距离求和后除以参考字符总数，不对逐图 CER 取平均；插入多时 CER 可以超过 100%。
- 这是自定义工程诊断指标，不是 RCTW 官方检测/识别比赛分数，也不是纯识别准确率。阅读顺序、多方向文字、参考框粒度仍会影响结果。未匹配区域数不是官方漏检率。
- DocLayNet 参考来自 PDF 文本，存在符号映射及不可见文本问题；中文街景来自公开训练部分。没有对全部标注人工校正，不能据此承诺客户准确率。
- 客户端耗时含网络、图片上传、网关和推理；服务端 elapsed_ms 主要包含 OCR 调用与锁等待，不含完整预处理/网络。此次顺序低并发调用不能代表双节点最大吞吐，也没有测量功耗。
- calibration 用于检查评测流程；evaluation 使用相同冻结规则。模型配置未依据 evaluation 结果调整。

## 标注及路由审计

- calibration/doclaynet: `{"invalid_reference_regions": 16, "reference_regions": 2402, "missing_regions": 199, "unmatched_predictions": 10, "ignored_predictions": 0}`；节点请求数 `{"GPU-4a64ff0c-324c-60ed-5a48-df099dde9dd6": 14, "GPU-cd838bc3-2b0e-9aaf-e04b-cd3ab24d837c": 6}`。
- calibration/rctw17: `{"invalid_reference_regions": 0, "reference_regions": 117, "missing_regions": 23, "unmatched_predictions": 6, "ignored_predictions": 6}`；节点请求数 `{"GPU-4a64ff0c-324c-60ed-5a48-df099dde9dd6": 10, "GPU-cd838bc3-2b0e-9aaf-e04b-cd3ab24d837c": 10}`。
- evaluation/doclaynet: `{"invalid_reference_regions": 10, "reference_regions": 8086, "missing_regions": 990, "unmatched_predictions": 143, "ignored_predictions": 0}`；节点请求数 `{"GPU-cd838bc3-2b0e-9aaf-e04b-cd3ab24d837c": 41, "GPU-4a64ff0c-324c-60ed-5a48-df099dde9dd6": 39}`。
- evaluation/rctw17: `{"invalid_reference_regions": 0, "reference_regions": 376, "missing_regions": 65, "unmatched_predictions": 13, "ignored_predictions": 17}`；节点请求数 `{"GPU-cd838bc3-2b0e-9aaf-e04b-cd3ab24d837c": 44, "GPU-4a64ff0c-324c-60ed-5a48-df099dde9dd6": 36}`。

## 高错误样本（自动排序，需人工复核）

- [doclaynet_f883ede7c6b73f5f905f8ba1df4a0cc57a5055e2d6e00ee1c1149618aeede719](../../datasets/ocr-mixed-v1/images/doclaynet_f883ede7c6b73f5f905f8ba1df4a0cc57a5055e2d6e00ee1c1149618aeede719.png)：分组 CER 82.2%；未匹配参考区域 4。
- [doclaynet_36e67fbd9eed08b904efa0c4884024a4815f513f4c126e4a47a118854046f52e](../../datasets/ocr-mixed-v1/images/doclaynet_36e67fbd9eed08b904efa0c4884024a4815f513f4c126e4a47a118854046f52e.png)：分组 CER 74.7%；未匹配参考区域 34。
- [doclaynet_d0bf118449b6415e5030fe6ef73584d1334f710eb2a70a8f78716dd954a847da](../../datasets/ocr-mixed-v1/images/doclaynet_d0bf118449b6415e5030fe6ef73584d1334f710eb2a70a8f78716dd954a847da.png)：分组 CER 62.1%；未匹配参考区域 5。
- [doclaynet_67bae167225018874a56de88e8ed4e7f5a147b64e7706c942f14068b0ef62a9b](../../datasets/ocr-mixed-v1/images/doclaynet_67bae167225018874a56de88e8ed4e7f5a147b64e7706c942f14068b0ef62a9b.png)：分组 CER 61.7%；未匹配参考区域 17。
- [doclaynet_481a5738351ec20ae055fcc68e28978b774b1eb8e6be8e926e785cc057c433a3](../../datasets/ocr-mixed-v1/images/doclaynet_481a5738351ec20ae055fcc68e28978b774b1eb8e6be8e926e785cc057c433a3.png)：分组 CER 59.1%；未匹配参考区域 19。
- [rctw17_image_3988](../../datasets/ocr-mixed-v1/images/rctw17_image_3988.jpg)：分组 CER 350.0%；未匹配参考区域 0。
- [rctw17_image_4725](../../datasets/ocr-mixed-v1/images/rctw17_image_4725.jpg)：分组 CER 100.0%；未匹配参考区域 3。
- [rctw17_image_7475](../../datasets/ocr-mixed-v1/images/rctw17_image_7475.jpg)：分组 CER 100.0%；未匹配参考区域 0。
- [rctw17_image_2757](../../datasets/ocr-mixed-v1/images/rctw17_image_2757.jpg)：分组 CER 71.3%；未匹配参考区域 12。
- [rctw17_image_6946](../../datasets/ocr-mixed-v1/images/rctw17_image_6946.jpg)：分组 CER 66.7%；未匹配参考区域 1。

## 人工抽查发现

- 校准集英文财务页 a5c884…：原图中若干水平正文行没有出现在响应中，存在真实整行漏检/过滤问题；不能只看请求成功率。
- 评估集英文 f883ed…：主要是旋转 90 度的小字；水平阅读顺序假设与 PDF 分块会额外抬高 CER，需要方向感知匹配或人工复核。
- 评估集 RCTW image_3988：四个大字“用宫品前”正确识别；衣物上额外输出的拉丁字符串没有对应有效参考框，导致单图 CER 350%。这些未匹配输出可能包含背景误识别或未标注的真实文字，不能直接据此断言正文识别差。
- 因此上述 CER 适合固定规则下比较版本，不能换算成“准确率 = 1 − CER”对外宣称。


## 后续动作

1. 人工核对错误样本页面，区分真实漏字、错字与参考/匹配问题。
2. 优先在 40 张校准集上比较检测输入分辨率、低置信度过滤及分块处理对小字的影响；参数冻结后重新跑独立评估。
3. 识别质量满足目标业务后，再做单节点/双节点并发 1、2、4、8 压测；随后开展节点排空和退出恢复实验。

文件：`per-image.csv` 逐图指标；`failure-examples.html` 原图与标注/预测框；各 split 下原始响应 JSON、`scored.jsonl`、`summary.json`。API key 不写入结果。
