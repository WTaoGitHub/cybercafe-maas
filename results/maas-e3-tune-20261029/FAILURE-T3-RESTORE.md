# T3恢复阶段中断

64/64测量点已完成其60秒/200次成功、供给、结果及计量核对。随后将原生模型从8副本恢复到4副本时，GPUStack保留了3+1分布；4个实例都在running。实验编排错误地假定直接缩容会保留2+2均衡布局，触发`RuntimeError: Unbalanced worker placement`。这不是一次OCR请求失败，也不能据此称GPUStack声明可用却不能推理。

原始T3状态保留`INTERRUPTED_REQUIRES_INSPECTION`，后续串行阶段已停止。只读现场：MaaS paused=true、generation121，active/unknown/backend/sync活动均0；模型固定字段与原值一致，4个running实例；应用尚为E8/G8/P2，executor pending=COMPLETE。尚未恢复整体原R4/E4/G4/P4，因此T3整体不标通过。

第一次受控处置方案：在当前已排空且拥有的维护窗口内，对本模型做0→4的容器重建并核验2+2，真实恢复探针通过后，再通过既有维护工具将E/G/P恢复4/4/4。此法沿用T1的均衡重建流程，改进调优控制器的缩容路径；不改变GPUStack产品的正常缩容语义。失败原记录不覆盖，不重放任何测量请求，不自动解除UNKNOWN，不操作VM、worker或Docker引擎。

本地11项相关恢复/所有权回归通过，实际恢复结果另存独立证据。若1～2次有界调试仍无法继续，按用户要求中断后续测试。

## 第一次处置结果

受控恢复成功：0→4重建后2+2，随后恢复E/G/P=4/4/4，固定应用环境匹配原值，两次真实探针通过，generation124、UNKNOWN=0。未重启VM/worker/Docker引擎、未重放任何测量请求。原失败状态不变，独立恢复记录见[t3-restoration-recovery.json](t3-restoration-recovery.json)。全部64窗口已重新作只读核对，后续根据显式的测量＋恢复组合证明继续条件扩展；不会将原始run的INTERRUPTED改写为COMPLETE。
