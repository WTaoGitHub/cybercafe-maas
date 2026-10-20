# P0 验收报告：GPUStack v2.2.3 接入与协议基线

日期：2026-10-20 · **状态：P0通过**（限本项目已明确的受控试点范围）。

## 完成清单

| P0任务 | 结果 | 证据 |
|---|---|---|
| 运行版本与实际入口 | v2.2.3 / 1cfcbbf，实际经Higress；原OCR0.3两副本 | maas-gateway-policy.json、final-verification.json |
| POST重试/fallback边界 | 持续503触发3次后端调用；真实网络任务不自动叠加模糊POST重放 | gateway-acceptance.json、P0-GATEWAY |
| OCR/generic用量覆盖 | OCR JSON/SSE各记1；generic原生HTTP无同等记录；503记1请求但后端执行3次 | gateway-acceptance.json |
| 原生generic proxy | 临时route路径改写和鉴权通过；生产OCR保持未启用 | gateway-acceptance.json |
| 组织、白名单、过期与权限变更 | 两真实客户组织隔离、目录隔离、grant撤销恢复、白名单变更、key到期均验证 | tenant-acceptance.json |
| 任务对象隔离 | 两真实组织key经任务API+PostgreSQL，跨project查询/下载/取消及伪造头被拒绝 | tenant-acceptance.json |
| 身份桥接约束 | 不转发客户缓存票据或共享HTTP Cookie；重复key/project绑定失败关闭 | p0-stateless-auth.log、p0-all-tests.log |
| 版本固定方式 | 专用版本目标+配置指纹+统一内容image ID+新鲜worker清单；实际配置漂移拒绝 | version-acceptance.json |
| API/schema/状态机 | TaskCreate/TaskView/错误/mock schema及幂等语义已导出 | protocol-schemas.json |
| 参数和验收线 | 有界队列/并发、输入输出、租约/心跳、TTL、2GiB空间余量及初始性能目标已定 | acceptance-profile.json、P0-ACCEPTANCE |

P0是接入策略与工程契约验收，不表示真实OCR异步调用、生产部署或商业SLA已经交付。

## 关键发现与决策

1. **一次请求不等于一次推理。** 当前Higress对持续503的POST做3次实际调用；业务重试不能不加区分地再乘3。真实connector必须区分未派发和未知后端执行。
2. **基础usage不等于客户成功账本。** OCR有请求记录，generic存在覆盖缺口；错误请求也有计数。按任务唯一成功事件计业务用量，不伪造token，不拿api_requests直接收费。
3. **网关缓存票据不适合任务层认证。** 已签发票据在key过期后仍可能有效；任务层仅以原key做新认证/权限验证，不接收客户缓存票据或会话Cookie。
4. **同镜像tag不代表相同内容。** 版本登记使用完整内容ID和部署指纹；P2为OCR准备新专用目标与统一镜像，不原地改写生产版本。
5. **generic代理可用但非首要依赖。** 已隔离验证，P2可先走已验证的兼容接口，不必给原OCR开启新路径。

## 测试与范围

- 累计42项测试通过：原15项PostgreSQL持久闭环，新增鉴权、版本漂移、参数配置及协议回归。
- 网关、generic、真实OCR少量正常请求、多组织和版本预检均在现网v2.2.3完成隔离实测。
- 两组织采用“平台拥有模型路由，客户组织消费”的业务模式，未声称穷尽上游所有组织管理/共享组合。
- 版本预检已实现并用真实临时控制记录验证；P2需将其接入真实执行器，并控制发布时序。它不是上游提供的原子不可变部署。
- 性能P95、回收时延等数字是后续验收目标；本次不冒充已完成吞吐/8小时SLA测试。
- 测试日志有1条上游TestClient的弃用提示，不影响通过结果。

## 参数基线

输入10MiB、输出20MiB、提交JSON64KiB；全局/项目排队1000/100，执行槽2/1；租约30秒/心跳5秒；输入/结果各24小时；最小磁盘余量2GiB。已支持MAAS_*环境变量和启动校验。

受理P95初始目标为内网4客户端下不超过1秒，mock租约失效后5秒内可重领，后续按试点负载实测；不是对外承诺。控制端本次可用空间约391GiB，不应据此认为24小时结果无限可存。

## 清理与生产服务复查

临时model/route/backend、用户/组织/key均已删除，私有测试凭据bundle已移除。P0专用PostgreSQL测试库已删除，原独立测试数据库容器停止；探针代码、报告及probe镜像保留供复现。

原rapidocr-gpu仍为backend0.3、两个ready副本，原route generic_proxy=false；复查请求200、46行。未升级GPUStack，未修改生产模型配置，也未给生产OCR开启generic接口。

## 当前下一步

先完成P1剩余的适配器抽象、数据库中断和结果提交崩溃窗口验证；再进入P2，部署独立OCR版本目标、接入预检与真实推理、落实未知执行隔离。P3排空、P4公网/备份/8小时验收仍保持原阶段边界。

## 材料

- [入口/重试/用量](../../docs/framework/P0-GATEWAY.md)
- [组织与身份](../../docs/framework/P0-IDENTITY.md)
- [版本固定](../../docs/framework/P0-VERSIONING.md)
- [协议与参数](../../docs/framework/P0-ACCEPTANCE.md)
- [机器可读参数](../../deploy/maas/acceptance-profile.json)
- [完整测试日志](p0-all-tests.log)
- [生产复查与清理](final-verification.json)
