# P0：多组织、凭据与任务对象权限验收

2026-10-20 · [真实验收记录](../../results/maas-p0/tenant-acceptance.json) · [回归测试](../../results/maas-p0/p0-auth-tests.log)。

## 验收范围

两个临时客户组织、各一个非管理员owner、各一个组织绑定的inference-only key，消费平台拥有的两个临时私有模型路由。这是本项目的“平台提供模型、客户组织消费”模式，不声称所有GPUStack管理/组织共享组合都已测试。

- 组织A/B分别访问获授权路由：200；跨组织访问：403。
- 伪造X-Organization-Id访问另一个组织路由：403；模型目录分别只显示各自私有路由。
- 撤销组织route grant后立即拒绝，恢复grant后允许；缩小key模型白名单后拒绝。
- 2秒过期key：过期前200，3秒后使用新鉴权请求401。
- 用这两个真实key绑定两个task project，实际任务API+PostgreSQL验证：A读自己的任务200，B读/取消A任务、下载A输入、伪造组织头均404。

## 必须保留的安全边界

GPUStack `/token-auth`的已签发网关缓存票据在key过期后仍可能返回200（本次已复现），因为票据有自己的有效期。因此任务层不接收、转发或持久化客户提供的`x-gpustack-auth-cache`，不直接把它当通用SSO token；每次用原key进行新的认证/授权请求。

现有AuthConnector每次只构造自身Bearer和必要模型头，不转发Cookie/组织头/缓存票据。新增回归用例确认该行为；这些限制不等于修改GPUStack网关缓存。客户直连原网关的缓存传播边界仍须在服务说明中保留。

## 登记规则

- 只有可信管理员可登记key→project/组织的映射，归属来自签发结果owner_principal_id，不接受客户自报。
- 活跃登记中的credential ID及完整key必须唯一，同一key不能绑定两个project。
- 缺失/格式错误/重复的登记、非私有secret文件一律503失败关闭；key未登记/禁用拒绝。
- 不克隆用户密码或要求客户management key；执行前模型权限复核是P2真实模型调用的必需步骤。
- P0只批准受限、非public的任务模型目标，避免把public route的匿名语义误当key模型白名单校验。

本feature新增登记歧义检测，5个无数据库鉴权用例与原15项PostgreSQL测试共20项通过。真实多组织探针与两project测试分别留下证据，不把MockTransport用例冒充真实组织验收。

所有临时组织、用户、key和route已删除；生产模型/路由未修改。权限“立即生效”仅表示本次新鉴权请求的观测，不承诺跨网关缓存零延迟。
