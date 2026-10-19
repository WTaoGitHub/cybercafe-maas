# OCR Mixed v1：普通文字提取测试集

200 张完整图片：100 张 DocLayNet 英文文档 + 100 张 RCTW-17 中文/中英混合场景图片。
固定随机种子 20261016；40 张 calibration（每来源20）用于调参，160 张 evaluation（每来源80）用于冻结后评估。
这不是两个上游数据集的官方联合测试集，也不是模型训练集。RCTW 来自带公开标注的训练部分；DocLayNet 从官方 PNG 池筛选。
预训练模型可能已见过这些公开数据，结果用于工程比较，不能证明对新客户数据的泛化。

## 文件

- images/：完整、未缩放、未重新编码的原图。
- annotations/：统一区域标注，保留多边形、文字、ignore 标记及原始文档元信息。
- ground_truth/：为人工查看生成的逐行文本，按区域 y/x 粗略排序。
- manifest.jsonl / manifest.csv：逐图路径、语言、尺寸、大小、SHA256、有效文字区域数、字符数、来源与许可状态。
- splits.json：固定调参/评估 ID 列表。
- summary.json、validation.json：数量及完整性检查。
- preview.jpg：16 张样本联系表。
- ../sources/：每张入选图片及上游格式标注的保留副本。

## 来源与参考文字

### DocLayNet

官方 https://github.com/DS4SD/DocLayNet
从官方 v1.0.0 core.zip 和 extra.zip 通过 HTTP Range 提取对应 PNG 和 JSON，逐成员检查 CRC32。
先按种子打乱 PNG 文件名抽 180 页候选；筛选英文（langdetect 自动检测）、至少100字符，并确保每个原始 PDF 至多一页，取100页。
英文语言标签是自动判断，未逐页人工核定。
extra JSON 的文字来自数字 PDF，不是逐字人工校对 OCR 答案；可能存在字体映射和不可见文字等偏差。
保留 cells 原始文字和坐标。读取顺序不是 gold，不能直接用全文字符串 CER 当作唯一准确率结论。
许可 CDLA-Permissive 1.0，完整文本在 ../sources/doclaynet/LICENSE。

### RCTW-17

官方 https://rctw.vlrlab.net/dataset
官网高校下载域名当前无法解析。
文字、多边形、difficult 标记来自 OpenDataLab 在魔搭发布的 DSDL 转换包：
https://modelscope.cn/datasets/OpenDataLab/RCTW-17
原图由 NVIDIA NVEagle/LocateAnything-Data 的 RCTW 原图镜像获取，经 hf-mirror.com 传输。
使用镜像的 source_relative_path 与原始 image_*.jpg 对应；选中图片的 content_sha256 和 source_sha256 相同，实际下载字节也逐张通过该 SHA256 校验。
使用 DSDL 中完整标注，而不是 NVIDIA 任务重编排后的 query 标注。
筛选含可评估中文、像素数不超过2000万的图片，按种子打乱取100张，不裁剪文字行。
许可仍未确认，镜像发布和公开下载不能自动赋予商业使用权限；此测试集不能标为整体商业许可已清。

## 评估建议

1. 先固定模型和预处理，在 calibration 上调参数，再使用 evaluation 报告成绩。
2. 按来源分别报告：文档页和场景照片的文字密度不同，不用一个平均张/秒代表业务产能。
3. RCTW 的 difficult/### 区域设 ignore，计算检测/端到端指标时应排除对应区域，不能把 ### 当正文。
4. 优先通过区域匹配计算识别错误与漏检；按全文 y/x 排序的文本仅用于查看，复杂版面和多方向文字可能排序不正确。
5. 如报告 CER，同时明确 Unicode、空白、大小写和标点归一化规则，保留严格/归一化两个版本。
6. 所有输入均符合当前服务 10MB/20MP 限制；这只是输入兼容性，不是准确率达标证明。
7. 保留了真实难例和小字；没有人工校对所有文本；2026-10-19已完成200张基线与配置对比评测，报告见 ../../results/real-ocr-v1/REPORT.md 及 ../../results/ocr-quality-v2/REPORT.md。

## 复现

在 wtaobj 下安装 requirements-dataset.txt 中依赖，然后：

```sh
.venv/bin/python download_datasets.py docsample
.venv/bin/python download_datasets.py rctwsample
.venv/bin/python build_dataset.py
.venv/bin/python validate_dataset.py
```

下载过程依赖 raw 中保存的上游 metadata/DSDL 包；具体 URL 见 sources/*/source.json 和 download_datasets.py。
本地只保留抽样和对应原图标注，没有保留两套全量原包。
