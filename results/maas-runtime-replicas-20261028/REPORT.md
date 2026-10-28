# 维护时动态读取副本数

2026-10-28。用户指出replicas为runtime配置，可能随时调整；固定长期approved_replicas会制造无必要的维护重绑定。

## 新行为

- 绑定仍限制cluster、worker实际身份、model/route及部署镜像/命令等配置；不再固定replicas。旧binding/operator中的approved_replicas被忽略，新的binder不再生成它。
- new-session只分配窗口；每次begin重新读取GPUStack期望replicas，校验实例已健康收敛后写入该窗口600 journal，再暂停和维护。不是读取ready_replicas或实际运行数量。
- recover使用本窗口journal快照，避免缩至0以后误把当前0作为恢复目标。绑定时14、开始时4，则4→0→4；下一窗口改6，则6→0→6。
- 恢复时数量、running和worker范围必须正确；布局由GPUStack重新调度，记录restored_placement，不固定要求原7/7或3/1分布。仍保留真实恢复探针和UNKNOWN保护。
- 维护期间检测到副本/部署配置漂移拒绝覆盖；在脚本尚未开始缩容时外部改0，也会阻止继续执行。GPUStack PUT未提供本实现使用的原子CAS，不能识别第三方同值写入或消除读写竞态；窗口内仍需避免管理员/自动扩缩容并行修改。不是动态追随维护中的新目标。

## 发布及验证

Mac私有目录备份001原宿主脚本和binding后，持维护锁确认旧会话RESUMED且未暂停，原子替换四个宿主脚本，仅移除未来binding的固定replicas字段，保留历史会话。未重建任何业务容器、未改当前模型副本、未开始维护、未重启VM/Docker。

新增只读plan，可直接使用binding.json，预览不冻结副本；begin仍会重新读取。001实际执行plan成功，返回model18 replicas=4、worker7/8身份通过，见[runtime-plan.json](runtime-plan.json)与[deployment.json](deployment.json)。现有服务继续运行。

19项针对性测试全部通过，包括1/4/6/14动态快照恢复、旧binding兼容、快照后修改绑定不能改变本窗口目标、维护中改0/改6拒绝覆盖、非副本配置漂移拒绝、原生调度改变分布仍能安全恢复、UNKNOWN及探针失败保护。MaaS本地回归217通过，56数据库依赖用例本轮未重复执行；这些未修改的数据库专项此前已[单独56项全部通过](../maas-db-regression-20261028/REPORT.md)。

本轮没有再次执行真实停止/恢复，不将模拟测试写成真实4副本维护演练；不改E3并发、不部署E1、不改变当前计划优先级。
