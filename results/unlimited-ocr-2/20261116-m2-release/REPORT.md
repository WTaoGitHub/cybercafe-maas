# M2 受控文档异步服务交付

2026-11-16 · [Issue #11](https://github.com/WTaoGitHub/cybercafe-maas/issues/11) · [执行计划](../../../docs/framework/UNLIMITED-OCR-2-PLAN.md)

**M2受控试点交付完成。** 用户明确要求“忽略 中文场景的质量未达标问题。继续推进。”后，本轮保留[原失败及停止记录](../20261116-m2/REPORT.md)，将该质量门槛作为用户接受的已知限制，完成P5容量验证、P6备份恢复/回退及受控开放。没有将中文质量重新判为通过，也没有进行第三次质量修复。

`document.unlimited-ocr-2` 已在独立文档入口开放，复用现有 artifacts/tasks/cancel/download/usage API，原 `forDownStream` Key 可沿用。试点只授权既有试点project，独立DB、artifact根目录、bindings、API及执行器；旧OCR入口继续使用原配置。实际URL和凭据保存在受控连接文件，不进入Git。

## 实际支持范围

| 项目 | 发布配置 |
| --- | --- |
| GPUStack服务 | 模型11 `unlimited-ocr-2`，实例190，worker6，1/1；Higress路由与M1一致 |
| MaaS逻辑模型/driver | `document.unlimited-ocr-2` / `gpustack.document_ocr` |
| 输入 | 一个同project PDF或PNG/JPEG artifact；PDF≤10MiB、1–10页，默认150DPI、最高300DPI，每页≤10MP/总≤50MP；图片沿用10MiB/20MP |
| 执行预算 | 同时执行1份文档，全局/项目相同；等待队列8/8；执行600秒、任务总期限900秒，max_attempts=1 |
| 传输与结果 | JSON-only、固定可信处理器；有版本的JSON结果、顺序页码、版面块、原始标记及Markdown；首版无裁剪文件 |
| 计量 | 唯一 `successful_pages` 最终收据；token窗口汇总语义未验证，明确unavailable |
| CPU服务 | API/执行器各2GiB RAM、2CPU、128 PID、有界256MiB tmpfs和只读root；入口128MiB/0.25CPU |
| 停止/恢复 | 执行器StopTimeout=630秒；先关闭提交并暂停/排空，再停止进程。持久恢复探针通过后恢复派发 |

限额是资源拒绝边界，不是全范围质量承诺。当前只验证固定单执行预算，未启用动态并发#60或自动副本调节，不将HTTP提交并发当成GPU执行并发。

## P5 容量及实际下游验证

按[续行验收范围](acceptance.json)，使用同一组冻结输入，在HTTP并发1/2/4间交错完成三轮；每档先用单页预热。三轮输入分别为合成单页、evaluation两页混合、合成两页。包含3个预热任务共24任务/38页，全部成功并完成结果、页序、最终收据、单位拒绝及原键幂等复用核对。

| HTTP提交并发 | 正式任务/页 | 客户端观察P50 | 客户端观察P95 | DB最长排队 |
| --- | --- | --- | --- | --- |
| 1 | 3 / 5 | 4.77秒 | 15.11秒 | 0.45秒 |
| 2 | 6 / 10 | 7.03秒 | 29.66秒 | 14.86秒 |
| 4 | 12 / 20 | 18.50秒 | 59.51秒 | 43.65秒 |

P50为样本中位数，P95使用nearest-rank；含预热24任务的中位数10.54秒、P95为59.06秒，低于冻结600秒门槛。客户端耗时包含排队及轮询观察，属于有限突发试验，不是持续到达率或生产SLA。1800次数据库采样中，实际执行/探针占用最多1、等待最多4，UNKNOWN及并发预算违规均为0。两页自然文档实际执行最长约14.53秒；更多HTTP并发主要增加等待。

最终180秒开放观察包括：既有P2表格/公式PNG的SHA核对、Paper→12/24及Books→5/50行列关联、`F = m x a` 内容；十页混合PDF；空闲后单页请求。三项全部成功，十页任务约69.41秒，能力查询持续200。未将简单公式/固定表格通过外推为复杂数学或真实跨页表格质量认证。

另对已保存的22份多页结果、68页做[离线页关联复核](page-association-evidence.json)：合成页的独立ALPHA编号、自然样本来源锚点和末尾空白页均对应预期顺序，未发送新GPU请求。检查器初版错误过滤了全部5字符参考文本，修正短锚点回退后通过；这是页身份校验，不修改原中文质量指标或豁免。

所有HTTP均由实际 `kc-e2e-aliyun-slave3-server` 容器中的curl发起，新PC负责编排校验。没有在下游安装Python/jq。最后两页验收任务 `task_d2d63b768d6945aea90bdf3fcc7e5087`，SUCCEEDED、`successful_pages=2`，客户端观察4.69秒；结果 `art_15dfc88a93df4a4cae0d26e0a006f4fd`。

历史与本轮合计45个成功任务、45条任务用量、45条最终页收据、91页；每任务一次MaaS尝试，重复用量源0条，内部探针用量0条。一次MaaS尝试和唯一收据不表示GPU恰好执行一次；Higress沿用M1的内部重试策略，应用不叠加重放，未确认活动仍隔离。

## 16GB、镜像及运行证据

[资源证据](resource-evidence.json)含821次约2秒采样、约1652秒暖态观察。16376MiB显存的RTX4080SUPER观测峰值10922MiB；推理容器memory.current观测峰值10383716352字节，CPU观测峰值约2.11核，共享内存峰值约148.6MiB，宿主最低可用约19.68GiB，OOM事件前后均为0。最后3次、间隔3秒读取引擎running/waiting/tokens/pending均0；采样器已退出。

16GB支持本次固定版本、固定单份执行、暖态样本范围；没有本轮初始化峰值、全50MP/300DPI边界、多份GPU同时执行或长期稳定性结论。M1的GPU运行环境启动守卫及无人值守恢复限制继续有效。

文档API/执行器镜像已保存在新PC既有registry：

- 仓库 `maas/document-ocr`，tag `m2-20261116-31eaaa2`。
- OCI index digest `sha256:fa05b3688f1c9b5e73d308807e6210b8f16a3a1e349628ab4b5fd91de70a96cb`。
- linux/amd64 config digest `sha256:f26a78c87c867314cd5b8a83e9cb26abd4f0e3075f2d0b36b5ff88aa9a74c59d`，16层。

通过既有受信loopback管理入口发布，未新增监听或改Docker daemon；worker使用现有专用reader及CA，经TLS读取并实际docker pull，匿名401/reader200，manifest、config及rootfs摘要匹配。该CPU镜像此前不在worker镜像索引中，不代表全部基础层均为空缓存；reader配置未变，GPU模型容器持续运行。模型运行镜像仍为M1的固定digest，未替换。

## P6 回退、恢复与开放

1. 实际捕获一份两页任务在后端执行、另一份排队后，关闭新上传/提交（503）并暂停独立派发。在途任务完成，排队任务保持QUEUED、attempt=0，历史查询/结果/用量仍200，无UNKNOWN。
2. 在暂停快照上备份DB、artifact和配置，恢复到新建的独立测试数据库及独立文件目录。用同一不可变镜像的新容器读取恢复内容：历史十页及新完成任务、文件哈希保护、页收据均通过；排队任务及其0次尝试保留，恢复快照40条收据。随后删除该测试数据库；没有将执行器接到恢复库触发推理。
3. 真实模型探针通过后恢复原服务，原排队任务只执行一次，取得唯一收据。原队列、任务ID、revision及归属未改写，没有自动清除UNKNOWN。
4. 最终排空后仅更换文档CPU执行器，镜像相同、StopTimeout设为630秒；原GPUStack模型/worker及旧OCR容器未停止。恢复探针持久保存在只读secret挂载内，验证恢复后再开放入口。
5. 文档明确暂停期间，旧OCR再次3次真实回归成功，输出哈希与此前一致；另有回退前后3次成功。原MaaS API、执行器和PostgreSQL容器ID/镜像/StartedAt均未变化，GPUStack旧OCR仍2/2，新模型仍1/1，实例集合未变。
6. 最终45条收据快照在artifact生命周期文件锁下私有备份，包含持久探针、当前开放配置及完整文件；另有不可变镜像离线归档。完整恢复演练基于前述40条收据快照，最后快照没有重复做恢复演练。未做VM/OS/Docker daemon重启、空机灾备或异机RPO验证。

发布仅覆盖当前受控试点project和私网入口；当前新提交开放、派发未暂停、无排队/在途/UNKNOWN。完整脱敏机器汇总见[release-evidence.json](release-evidence.json)，部署/回退操作见[接入说明](../../../deploy/maas/DOCUMENT-OCR.md)。

## 容器内curl

在实际下游容器执行以下命令，查询本次已完成两页任务的页收据，无需Python/jq：

```sh
curl --config /tmp/maas-uocr2-m2-20261116/m2-release-verify.curl
```

预期 `{"successful_pages":2}`。该命令核验历史成功任务；若要新建一次模型请求，使用已准备的同一两页PDF引用请求文件，生成并保存新的幂等键：

```sh
IDEMPOTENCY_KEY=$(cat /proc/sys/kernel/random/uuid)
curl --config /tmp/maas-uocr2-m2-20261116/m2-release-submit.curl \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY"
```

新任务预期202，须按响应task ID查询至SUCCEEDED，再下载结果及查询页收据；不能把202当成推理完成。同次重试保留原键、原请求，结果不明不换键重做。认证和真实URL只存在容器内600权限的受控curl配置；未写入Git或命令行。示例输入artifact按24小时TTL保留，过期后按[通用纯curl步骤](../../../examples/verify_document_task.curl.md)重新上传本地PDF。现有收据查询独立于artifact TTL。

中文场景质量仍低于原门槛，按用户明确指示接受该限制；业务留出集、真实跨页表格专项、长期容量及自动GPU恢复未认证。首版不支持SSE、token结算、裁剪图或多输入公共批次接口。
