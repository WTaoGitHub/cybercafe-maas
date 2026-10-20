# P2 常驻采集与操作手册

2026-10-20。[本轮报告](../../results/maas-p2-r2/REPORT.html)。worker离线实验按用户要求暂缓；不要把P2标记为完整验收通过。

## 已部署组件

- 控制端：XZ-31-001，同时继续提供原GPUStack worker能力。
- systemd：`maas-collector.service`，已enabled；代码 `maas_platform.collector`。
- 私密运行目录：`/root/wtaobj/maas-runtime`，配置、pin、预检、SSH身份均不写入Git。
- 专用试点模型：`maas-ocr-p2-r2`，revision `ocr-p2-r2`；复用两节点完全相同的专用镜像 `quay.io/cybercafe-maas-ocr:p2-r1`。原 `rapidocr-gpu` 与原0.3标签保留。
- 002清单端点：`scripts/maas/runtime/inventory_agent.py`，只接受 `inventory` 动词，作用域由002的root私密配置固定。

系统尚未常驻发布任务API和执行器；常驻collector与模型不能等同于完整对外MaaS发布。

## 信任与新鲜度

001生成专用ed25519私钥且不外传。002公钥登记使用 `from="10.11.31.1",restrict,command=...`；禁用PTY/转发/agent并执行固定脚本。主机公钥来自已有可信SSH连接，固定写入known_hosts；不自动接受新主机key。清单只导出worker身份、image ID、运行内容ID、暂停状态和观察时间，不导出容器环境或挂载。

collector每轮校验版本、路由唯一目标、模型/backend配置、同cluster节点集合/ready状态、运行副本、镜像及真实Higress策略。pin仅在显式enroll时以排他创建方式写入；周期采集绝不重新登记或覆盖pin。成功后原子写preflight；失败时立即撤销preflight并写脱敏health。控制面观察预算20秒、快照最大30秒、镜像观察最大60秒。原操作helper的HTTP调用有5秒socket超时，采集器不会无限等待连接；这不是所有网络操作统一的20秒硬中断。

这是观测新鲜度保证，仍有检查到调用之间的竞态。计划修改部署前应先暂停派发，不承诺GPUStack提供原子不可变部署。

## 检查与恢复

在001上：

```sh
systemctl is-enabled maas-collector.service
systemctl is-active maas-collector.service
cat /root/wtaobj/maas-runtime/health.json
journalctl -u maas-collector.service -n 20 --no-pager
```

`health.ok=false` 或snapshot不存在时，先核对节点、SSH清单通道、专用实例和真实配置；不要直接删除pin重建来掩盖漂移。恢复原已登记配置后collector自动刷新。collector停止时旧snapshot最多在有效期内继续使用，之后新推理拒绝；观察到失败时立即撤销。

新发布任务容器必须只读挂载预检目录，并在目录配置中使用：

```json
{
  "ocr.default": {
    "driver": "gpustack.ocr",
    "revision": "ocr-p2-r2",
    "upstream_model": "maas-ocr-p2-r2",
    "max_runtime_s": 25,
    "pin_file": "/trusted/pin.json",
    "preflight_file": "/trusted/preflight.json"
  }
}
```

管理key、collector SSH私钥不挂载进任务API/执行器。实际发布为非root UID时还需按最小只读权限设置预检文件属主/权限；本轮临时任务容器使用root，不能把600权限的host文件直接当作已适配非root生产容器。

## worker与宿主机的限制

**每个Docker引擎只允许一个GPUStack worker控制者。** 多个任务executor消费同一PostgreSQL队列不受此规则限制；GPUStack server、一个GPUStack worker和任务executor也可以同机部署。

v2.2.3 WorkloadCleaner会把引擎中不属于当前worker的实例当作孤儿清理，cluster/worker名称不是引擎隔离。仅新增daemon、数据目录或不同引擎ID也不保证网络/containerd隔离。本轮两种实验已造成容器和网桥影响并恢复，记录见报告。

`isolation_guard.py` 是只读检查，会拒绝001/002及检测到现有业务容器的环境；它不会启动worker或daemon，也不能代替对独立VM的确认。没有独立测试VM时，worker离线用例保持暂缓，不能使用容器重建的通过记录代替。

## 已验证用例与后续

`fault_matrix.py` 实际验证排队后模型ACL撤销、绑定禁用、真实key删除、模型env漂移、collector停止后快照过期、专用实例容器重建和历史结果下载。`--smoke-only` 只做真实上传/任务/结果链路，不做故障注入。脚本创建独立临时数据库/身份/容器，运行后清除临时身份；测试数据库和artifact清理由报告收尾步骤完成，不是生产发布工具。

剩余：独立VM上的worker离线/恢复；P3全局维护与同步旁路边界；P4发布、备份和长时验证。任何人工解除UNKNOWN活动必须先取得实际后端停止证据，不能把key撤销或worker不在线当作停止证明。
