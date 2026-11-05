# MaaS 模块化端到端测试

基线：2026-10-27，API consumer-ocr-r1、执行器/Observer e6-r1、GPUStack v2.2.3。

这是通过真实客户HTTP入口执行的黑盒回归框架，不导入maas_platform、不创建模拟模型，也不修改线上配置。运行器仅需Python 3.10+标准库；本地框架自测另使用项目开发依赖。现有pytest单元/集成测试仍保留，各有不同证据边界。

## 1. 能力与执行边界

- 65个独立用例，按case_id、module、labels组织。详见[完整用例目录](CATALOG.md)。
- 支持指定ID集合、模块集合、标签集合、排除标签、全量选择；列举与筛选不需要密钥或连接服务器。
- 同类筛选值之间为OR；不同筛选维度之间为AND。保持目录顺序；未知ID/模块/标签或空结果报错，不静默扩大范围。
- 每个用例自行准备所需artifact/task，没有依赖另一个case先运行。独立选择任务用例也能执行。
- 真实PNG/JPEG、兼容同步/原Chat、异步任务、文件、幂等、取消、跨项目隔离、计量和状态检查。
- 默认拒绝写操作和推理；需显式启用对应effect。没有任何VM、OS、Docker引擎、worker或业务容器重启操作，没有镜像还原、自动清理线上文件、故障注入和维护状态切换。
- 正常请求不依赖测试框架。可选运维取证仅在选中的case执行一次，不是collector、后台轮询服务或新的推理门禁。
- 下游用户体系/商业计费由下游负责的讨论尚未改变当前实现；测试仍验证当前客户key、本地project绑定和GPUStack授权，不把未实施的服务级凭据方案当成现有功能。

## 2. 本地准备

在仓库根目录执行：

```bash
cd /Users/4paradigm/dev/cybercafe-maas
mkdir -p .maas
cp e2e/config.example.json .maas/e2e-config.json
chmod 600 .maas/e2e-config.json
```

编辑配置文件。默认key路径为`~/.maas-credentials/customer-a-key`，文件权限600；key内容只放在文件里。证书路径指向此前已下载、并与001核对的公共证书，不能用配置服务/registry证书替代MaaS证书。

配置中所有相对文件路径相对于**配置文件所在目录**解析，不随执行工作目录变化。output则是命令行指定的本地结果目录。每次须使用新目录，已有报告不会被覆盖。

在另一个终端建立TLS转发并保持运行：

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -p "$MAAS_SSH_PORT" -i "$MAAS_SSH_KEY" \
  -L 127.0.0.1:18832:127.0.0.1:18832 "${MAAS_SSH_USER}@${MAAS_SSH_HOST}"
```

默认`base_url=https://127.0.0.1:18832`。不要添加`/v1`或API路径。TLS始终验证证书，不读HTTP代理环境，不跟随重定向。HTTP仅允许127.0.0.1/::1；若用授权HTTP转发，将base_url改成`http://127.0.0.1:18831`，该模式不读取cert_file。

## 3. 列举和部分执行

```bash
# 完整目录；也可追加--json供其他工具读取
python3 -m e2e list
python3 -m e2e list --json

# 按ID集合运行（逗号、空格、重复参数均支持）
python3 -m e2e run --config .maas/e2e-config.json \
  --case-id AUTH-001 AUTH-003 CAP-001 --require-all

# 按模块运行
python3 -m e2e run --config .maas/e2e-config.json \
  --module auth capabilities edge

# 按label运行；GPU用例需启用推理
python3 -m e2e run --config .maas/e2e-config.json \
  --label gpu --allow-inference --allow-writes

# 模块和label交集，再排除需要特殊窗口/外站的用例
python3 -m e2e run --config .maas/e2e-config.json \
  --module ocr ocr_validation --label positive negative \
  --exclude-label external --allow-inference --allow-writes

# 全目录；未具备运行条件的条目会显示SKIP
python3 -m e2e run --all --config .maas/e2e-config.json \
  --allow-writes --allow-inference

# 常规CI：排除特殊条件/大请求，要求选中项无跳过
python3 -m e2e run --all --config .maas/e2e-config.json \
  --exclude-label optional large --allow-writes --allow-inference --require-all
```

不带正向筛选参数的run会拒绝执行，须选择`--all`或至少一种正向条件。`--all`不能和case-id/module/label一起使用，但可以排除标签。普通list默认显示全部目录。

## 4. effect与前置条件

| effect/参数 | 含义 |
|---|---|
| 无effect | 只读HTTP/可选只读SSH观测 |
| write / --allow-writes | 上传、提交、取消自建任务、负向POST校验；可能写入输入/任务/同步活动记录 |
| inference / --allow-inference | 允许真实GPU推理及成功用量；异步任务用例也声明此项 |
| load / --allow-load | 大请求或已准备满额条件的检查；不会自动制造满额 |

`SKIP`不算通过；报告列明是缺少effect还是缺少配置。`--require-all`让任一SKIP产生非零退出码，适合为某个已准备环境定义严格验收子集。正常服务、维护和满额条件互相矛盾，不应要求所有65个case在同一状态下同时通过，应分别跑对应窗口的子集。

示例配置指向仓库内[中英文PNG/JPEG小样本](data/README.md)，附原文、预期片段和SHA256；无客户数据，无需下载外部数据集。若不配置image_file，运行器退回生成的白色64×64 PNG；OCR-003始终使用空白图。空白图只验证协议和执行链，**不能证明识别准确率**。

| 可选配置 | 用途/前置条件 |
|---|---|
| image_file | PNG/JPEG测试图片；示例配置使用仓库双语图片，不配置则退回空白PNG。用于多数正向OCR和异步用例 |
| jpeg_file | JPEG正向样本，与image_file分开配置 |
| expected_text_contains | 非空字符串数组；配合image_file验证已知中英文片段；不是CER/WER基准 |
| public_image_url | 可公开直接下载、无需认证、无重定向的PNG/JPEG；受外站/DNS可用性影响 |
| expected_revision | 断言当前capabilities revision；切换正式模型版本时更新 |
| secondary_key_file + distinct_projects=true | 已开通、属于不同project的第二key；不会自动创建用户/凭据 |
| restricted_key_file | GPUStack可认证但不得访问当前OCR的专用测试key；不自动创建或撤销key |
| expired_artifact_id | 主key项目中的已过期/已清理artifact元数据ID，验证410；不能用不存在的ID代替 |
| project + exclusive_project=true | 项目名与主key绑定一致，且测试期间没有其他请求，才能做同步计量差值断言 |
| operator | 可选001只读SSH证据，见下节；不需要交给普通下游客户 |
| expect_maintenance=true | 运维已独立进入批准的维护窗口，只选择maintenance模块；框架不暂停/恢复服务 |
| expect_capacity_full=true | 容量已由独立受控环境占满，选择QUOTA-001；框架不制造流量洪峰 |
| expect_queue_full=true | 等待队列已由独立环境占满，选择QUOTA-002；框架不灌满线上队列 |

配置中的`max_input_bytes`仅用于边界用例生成超限输入，需与被测服务一致；不会修改服务器限额。当前默认10MiB。

队列取消存在合法竞态：TASK-009接受已完成/运行中取消等合法结果，不伪称一定测到了QUEUED或RUNNING状态。MAINT-002在已暂停窗口单独验证确定的排队取消。不自动取消用户原有任务。

## 5. 可选运维与计量证据

在本机私有配置文件中设置：

```json
{
  "operator": {
    "host": "192.0.2.10",
    "port": 22,
    "user": "root",
    "key_file": "~/.ssh/maas_operator_key"
  }
}
```

此片段合并到配置，不要覆盖整份配置。SSH使用BatchMode和StrictHostKeyChecking，使用本机已验证的known_hosts，不自动接受新主机密钥。运行器不打印SSH私钥、API key或数据库连接串。

固定只读操作包括：docker ps、读取业务monitor状态、systemctl show已退役collector状态，以及在MaaS容器中以READ ONLY事务读取任务/同步成功事件计数。不会访问GPUStack数据库、修改SQL、检查每个worker或轮询实际镜像。

METER-001按**本次任务ID**检查一条成功事件，不依赖全局安静。METER-002/003是指定project计数差值，必须有独占测试项目。不要把某次通过的同步差值测试当成客户收到账单/恰好一次网络执行的证明。

E5-001仅证明配置/仓库服务进程在运行，不证明克隆首启、驱动兼容、冷缓存拉取或跨启动身份恢复。真实克隆首启仍需要管理员创建另行批准的独立克隆；框架不会启动、回收、重启001/002或母版。

## 6. 报告、退出码和恢复

默认结果写到`.maas/e2e/<UTC时间-随机值>/`，也可用`--output <新的目录>`指定。

| 文件 | 内容 |
|---|---|
| report.json | 选中用例、模块/labels/effects、逐请求状态与X-Request-ID、结果、跳过原因、资源ID、未决任务 |
| junit.xml | CI可读取的PASS/FAIL/ERROR/SKIP与耗时 |
| REPORT.md | 人工查看的概要表 |

目录700、文件600。报告不包含key、完整配置、原图base64、OCR全文或下载的结果内容。不要手动将本地私有配置或凭据提交Git。JSON在每次发送前记录SENDING，记录本次生成的artifact/task/幂等键，逐请求和逐case原子更新；测试进程异常退出时仍能查看最后阶段。

退出码：0=选中用例无失败（可能有明确SKIP）；1=断言失败/框架执行异常；2=配置/选择错误、未决执行熔断、或require-all遇到SKIP；130=中断。

没有自动重试HTTP请求；只轮询**已知task ID**。TASK-002在确认202后显式测试同键/同体复用，不是网络失败重放。若发生上传/提交/同步请求通信异常、UNKNOWN或不能确认的推理5xx，停止后续写入/推理，保留报告；只读检查可继续。确认的MAINTENANCE_PAUSED不误报为UNKNOWN。

已经创建且已知ID的任务会有界GET等待至终态，以免失败断言后留下意外的后台执行。若等待超时或中断，不自动取消/重提/重启；报告pending_tasks和resources供运维核查。运行器不支持自动续跑上次未决请求；只有确认上次执行状态后才另建新run。

输入/结果/任务和计量留在服务端；输入/结果按现有TTL清理，没有公开删除API，框架不直接删库/删文件。反复运行会消耗测试项目额度与存储，请使用专用测试凭据和无敏感内容的图片。

## 7. 覆盖边界及后续扩展

目前未自动执行：VM重启、驱动安装、镜像制作、克隆创建、维护切换、副本停止/恢复、故障注入、数据库断连、磁盘损坏、备份回退、真实24小时TTL等待、8小时耐久及几百/千节点容量。这些不是一次正常接口回归能安全替代的场景。

相关能力已有历史专项工具或pytest测试，仍按独立环境/运维窗口执行；本框架不调用旧collector、旧动态preflight测试脚本，也不把本地mock/进程恢复当作VM重启验证。全量选择通过或部分跳过，都不能宣传为上述专项验收完成。

新增用例：在e2e/cases中新增模块，用@case登记稳定ID、模块、标签、effects和requires；在cases/__init__.py导入。函数只使用Context.request/上传/任务辅助方法，不能加入任意shell脚本或绕过effect门禁。用例内部用check而非assert，避免python -O关闭断言。

本地框架自测：

```bash
.venv-maas/bin/python -m pytest tests/e2e -q
```

本次真实验收结果见[端到端报告](../results/maas-e2e/REPORT.md)。

### 零 Worker 冷启动

只在独立可销毁环境中逐个选择 `COLD-001`、`COLD-002` 或 `COLD-003`。
三项均要求 MaaS API、数据库、认证与模型配置可用，维护未暂停且初始没有 UNKNOWN。

- `COLD-001` 和 `COLD-002` 使用 `expect_cold_gateway_503=true`。GPUStack 控制面在线、没有 GPU Worker 和可用副本，指定模型的上游推理响应为 503。异步用例另需 MaaS 执行器在线。两项各用一套干净环境，预期产生 UNKNOWN 隔离。
- `COLD-003` 使用 `expect_no_executors=true`。测试期间没有 MaaS 异步执行器，验证任务排队、尝试次数为零及可取消。GPU Worker 和 MaaS 执行器是两个独立条件。

环境由测试外部准备。用例通过客户 API 观察响应。
