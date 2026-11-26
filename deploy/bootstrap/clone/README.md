# E5 克隆客户端与离线母版工具

签名发布可为任意受守卫的 GPU 模型声明宿主机见证路由。配置服务以读写方式使用 `MAAS_BOOTSTRAP_WITNESS_KEYS_DIR`，或使用运维预建的 `MAAS_BOOTSTRAP_EXECUTION_KEYS_DIR/host-witness` 私有子目录（0700）。控制端通过 `MAAS_RUNTIME_BOOT_KEYS_DIR` 只读访问同一目录。模型容器只挂载执行凭据、回执目录和本地见证 socket。开机时由宿主机生成本次启动的密钥，经过已批准的 worker 注册流程及受信 HTTPS 登记。母版必须清除实例状态及旧宿主见证配置。新 worker 上线可以恢复容量；旧任务需要原执行的有效回执或原宿主对原运行身份的签名终止证明。[模型接入和故障验收](../../maas/MODEL-ONBOARDING.md)

2026-10-23：代码和本地模拟测试已完成；真实新版母版快照、克隆启动与原生注册未验收。001配置API已在线，客户端真实HTTPS读取/验签已验证。新增worker/封版/发布工具本轮未部署到母版，不自动创建VM或操作快照。

## 启动和手工操作

镜像安装后的命令（仅用于另行创建的新克隆）：

```sh
sudo /opt/maas/bin/maas-worker-bootstrap prepare
sudo /opt/maas/bin/maas-worker-bootstrap apply --trigger manual
sudo /opt/maas/bin/maas-worker-bootstrap status
```

`prepare`只拉取、验签和私有暂存，不启动worker。`apply`是boot/manual统一入口：拿进程锁→共享码HTTPS取配置→验证签名、scope、image_id、架构、固定原生地址、镜像摘要→核对依赖→启动自身worker→以原生worker_token查询`/v2/workers?me=true`并核对cluster/name/UUID。`status`只输出脱敏本地状态，**不是实时健康接口**。GPUStack原生state单独输出；注册成功不等于模型已经部署或可推理。

普通命名策略下，新 worker 使用本机随机名称和独立空数据目录；`gpu-external-id` 无盘策略使用当前 GPU UUID 作为 name 和 external_id。CLI 拒绝在 `tower`、`tower-*` 母版上启动，当前工具不会更改 hostname 或网卡。2026-11-11 用户确认 XZ-31-001 已不再作为控制平面，允许其按普通 worker 流程注册；001、002 的主机名及旧地址不再作为注册禁用条件，Linux root、GPU、配置与身份校验继续生效。001、002 禁止整机重启的约束仍见根目录 AGENTS.md。

- APPLIED：已启动或恢复所属容器，原生注册确认通过。
- UNCHANGED：匹配的容器已经运行且此前相同配置已确认；不重复创建/启动。
- NEEDS_MAINTENANCE：现有容器与新配置指纹不同，包括已停止容器。保留原状，由管理员在维护窗口处理替换；没有自动删除/重建命令。
- REGISTRATION_UNCONFIRMED：本次未能确认，保留实际容器；再次执行只检查/恢复相同所属容器，不创建第二个worker。
- OTHER_WORKER_PRESENT / NATIVE_IDENTITY_WITHOUT_LOCAL_OWNER：发现其他worker或无本地所有权标记的旧身份，拒绝接管或清除。
- BOOTSTRAP_BUSY：已有本机引导在执行，等待后再手动检查。

普通及自动发证镜像由 `maas-worker-bootstrap.timer` 在开机满 30 秒时触发引导。服务排在网络、Docker 和供应商命名单元之后，检查主机名保护、命名结果、Docker、单张 GPU UUID 及 Bootstrap HTTPS 健康入口。条件满足即可进入网络边界检查与注册，即使供应商应用安装器仍在等待、`multi-user.target` 尚未就绪。暂时性就绪故障按原有 10 秒间隔重试，单轮预算为 600 秒。

就绪后先应用网络边界，再执行 E5 `apply --trigger boot`，验签、申请许可并核对原生注册。边界和 apply 每次分别限时 45 秒和 150 秒，受本轮剩余时间约束。超时后向子进程组发送 SIGKILL，再等最多 2 秒。仍在退出的直接子进程持有同一把启动锁，后续引导遇到占锁会延后。

含 [BOOTSTRAP-INC-001 修复](https://github.com/WTaoGitHub/cybercafe-maas/issues/78) 的镜像在同一次开机内支持多轮恢复。600 秒内仍遇到 Docker、GPU、本地只读运行时探测、配置服务或原生注册确认等暂时性故障时，boot 返回 75。systemd 在 2 分钟后重新执行整轮检查。systemd 自身的 620 秒启动超时也触发延迟重试。NFS 未决操作、身份冲突、验签失败等需要处理的状态保留原有保护。成功后服务保持 `active (exited)`。可用 `systemctl show maas-worker-bootstrap.service -p Result -p NRestarts -p ExecMainStatus`、journal 和 `/run/maas-worker-startup/last-result.json` 核对原因与次数。

交付时同步更新引导模块和 service。已失败且正在运行的旧版节点需要在获准维护时重新加载 unit 并启动引导一次。验收新版本时，先核对同一 GPU UUID、worker ID、原生 `Ready` 及新心跳，再通过目标模型入口完成一笔推理。真实部署及推理验收进度见上述 Issue。

非自动发证镜像由 `.path` 等待许可文件并触发一次性 timer。后续重试由准备服务的退出结果决定。容器保持 `restart=no`，成功后的服务不常驻轮询，容器随后退出仍需人工恢复。已有镜像升级时一并更新 Python 模块、service、timer、path 和网络 drop-in，移除旧 bootstrap/boundary 的 `multi-user.target.wants` 链接。在母版只 enable timer，不使用 `--now`。

## 离线镜像安装

工具只向一个**离线挂载/解包的Ubuntu文件系统目录**写入固定文件，不修改当前宿主、不安装包、不拉取镜像、不输出qcow2/供应商快照。磁盘格式导出/快照保存仍由供应商平台或镜像构建流程完成。

在具备构建材料的受控机器上，从本仓库运行，路径替换为实际离线目录：

```sh
install -m 600 deploy/bootstrap/clone/image-settings.example.json /secure/e5-image-settings.json
# 在受控编辑器中核对该文件里的源码证书/公钥/共享码文件路径，禁止把秘密写入命令参数。
python3 scripts/maas/prepare_worker_image.py \
  --root /mnt/offline-ubuntu \
  --settings /secure/e5-image-settings.json
```

settings示例包含试点公开地址及秘密文件路径，没有实际码。工具把克隆中的引用重写为`/etc/maas/`，安装客户端到`/opt/maas/lib/maas_bootstrap`、固定入口到`/opt/maas/bin/maas-worker-bootstrap`，创建systemd启用链接。共享码和本机JSON为600，配置目录700；以root构建实际交付镜像，确保所有安装文件归root所有。macOS临时目录测试产物不能直接认作完成Ubuntu镜像。

封版前必须另行满足：

1. x86_64 Ubuntu、Python3和`cryptography==41.0.7`（优先复用Ubuntu24.04已核对的包）；Docker、NVIDIA驱动和Container Toolkit已配置可用。
2. 正式manifest里的GPUStack v2.2.3固定摘要镜像已预缓存；客户端不会在线自动pull，也不会自动修改驱动、Docker或Toolkit。`docker image inspect`必须能匹配RepoDigest。
   该固定镜像创建的 worker 在启动前安装 [OCR 容器限额补丁](../../../maas_bootstrap/worker_limits.py)，核对 Docker 运行时代码摘要。补丁按固定镜像摘要和启动守卫匹配 OvisOCR3、Unlimited-OCR-2 的主推理容器，并设置 CPU、内存和 swap 限额。升级镜像前须复验容器创建路径。 [OvisOCR3 配置](../../ovisocr3/README.md#build-and-configure) · [Unlimited-OCR-2 配置](../../unlimited-ocr-2/README.md)
3. 原生状态目录、E5实例状态目录和Docker容器为空；缓存镜像允许保留。不得封入已注册token/UUID、固定worker名称、运行过的E5 identity.json。
4. 供应商完成模板generalize及首启身份生成契约：空/`uninitialized` machine-id，克隆首次生成不同machine-id、SSH主机密钥，保留管理员公钥授权；已有SSH主机密钥会阻止本工具封版。工具不删除这些材料，也不提供批量清理命令。
5. 已有CyberCafe/gpustack开机服务与新引导的责任边界经核对，避免两个worker自动启动。工具只拒绝已知冲突单元，**不能发现所有第三方启动钩子**，需检查供应商机制。
6. 新克隆能访问001的18840及原生18080，原生worker所需反向端口/容器网络符合GPUStack要求。母版到18080不通不作验收；不得因此放宽无关端口。

污染或文件已存在时拒绝写入；它不是重入安装器，也不自动清理部分构建。写盘/权限失败后丢弃该离线构建副本，从干净base重新构建；不要恢复001/002镜像。输出`/opt/maas/image-manifest.json`只有公开元数据、源码/公钥摘要和待满足条件，**不是启动验收合格证明**。

## 配置发布与回退

### 推理资源档位与执行回执

新母版安装 `execution.py`、`worker_limits.py` 和当前启动程序。新模型、固定镜像版本、限额和回执服务公钥证书由签名 manifest 的 `worker_runtime` 发布。使用新版母版的 `image_id` 独立发布，旧客户端继续读取其兼容版本。下面的 JSON 是 manifest 的一个字段，示例摘要和入口需替换为部署实际值，证书需填完整 PEM。

```json
{
  "profiles": {
    "ocr-standard": {"cpu_millis": 4000, "memory_mib": 16384}
  },
  "images": [
    {
      "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "entrypoint": ["python3", "/opt/model/entrypoint.py"],
      "profile": "ocr-standard",
      "execution_env_prefix": "MAAS_EXAMPLE_OCR_EXECUTION_"
    }
  ],
  "execution": {
    "origin": "https://receipts.example.invalid",
    "ca_pem": "<回执入口公钥证书或 CA 的 PEM>"
  }
}
```

档位同时设置内存上限和等量的 Docker memory+swap 上限，对应禁用 swap。每条规则可声明档位、执行回执或者两者；只配置限额时省略 `execution_env_prefix` 和 `execution`。镜像须使用完整摘要引用，启动入口匹配 Docker 实际 entrypoint。受保护的模型沿用现有 `<prefix>ROUTE`，创建时自动设置 `ORIGIN`、`KEY_FILE`、`CA_FILE`，仅给匹配的推理主容器挂载只读文件。

要启用本机结束回执，在对应的 `images[]` 项中增加整数 `receipt_uid`，填写实际写回执的容器进程映射到宿主机的 UID。该模型镜像须已支持 `<prefix>RECEIPT_DIR`。bootstrap 创建权限为 `0700` 的本机目录并单独读写挂入推理容器，自动设置 `<prefix>RECEIPT_DIR=/run/maas-execution-receipts`。密钥及 CA 继续以只读文件挂载。使用新版无盘母版时，宿主机的 `maas-worker-receipts.timer` 自动启用，每轮扫描结束约 10 秒后复查。可用 `systemctl list-timers maas-worker-receipts.timer` 和 `journalctl -u maas-worker-receipts.service` 检查运行情况。新配置按下述 revision 发布和存量 worker 维护流程生效。

在启用发证的配置服务容器中设置 `MAAS_BOOTSTRAP_EXECUTION_KEYS_DIR`，指向与 MaaS API 共享的私有目录。目录权限为 `0700`，由配置服务和 MaaS API 的共同运行 UID 持有；配置服务挂读写，MaaS API 挂只读。若已有现场凭据，在启用新发布前将原映射完整迁入该目录的 `worker-keys.json`（`0600`），再将 MaaS API 的 `MAAS_PP_OCR_WORKER_KEYS_FILE` 指向容器内此文件。例如把共享目录放在现有 `MAAS_SECRET_DIR` 的 `e5-execution` 子目录，API 使用 `/run/secrets/e5-execution/worker-keys.json`。两端挂载目录，服务端才能原子替换映射并让 API 读到新文件。

获批的开机会按 GPU 取回一项执行凭据。客户端写入 bootstrap 私有状态目录，推理容器通过只读 bind 读取，公布的 manifest 只携带证书和策略。现有批准流程仍负责 GPU 和开机会话接续；自动模式沿用该网络与批次认证范围。轮换既有执行凭据及硬件身份验证另行管理。

发布顺序为先发布兼容新版母版的配置和凭据存储，随后验收新克隆。后续改模型规则或证书使用原配置发布命令提交新 revision。新克隆自动读取；存量 worker 仍按现有维护流程替换后应用新 revision。

`maas_bootstrap.publisher`只在配置服务器受控操作环境执行，不对外提供写API。需要当前客户端范围settings、公开manifest JSON、600权限的registration JSON（cluster_id和token）、只留operator的Ed25519私钥。先把本仓库该模块安装到受控Python路径；本轮未将新发布CLI部署到001。

```sh
python3 -m maas_bootstrap.publisher \
  --root /root/wtaobj/maas-bootstrap/private \
  --settings /secure/e5-image-settings.json \
  --signing-key /root/wtaobj/maas-bootstrap/operator/release-signing.key \
  publish --manifest /secure/manifest-r2.json --registration-file /secure/registration.json
```

工具验签回读、核对客户端scope和policy、互斥发布、不可变revision、原子切换active摘要；写入文件继承private目录owner，避免root操作后API UID10001不可读。签名只覆盖公开manifest，原生token由HTTPS保护；均不写到输出或shell参数。失败不切换active，可能留下完整未激活版本，保留供检查。

镜像设置和 `policy.json` 中的授权项省略 `cluster_id` 时默认使用 `1`。发布工具以镜像设置补齐省略的 manifest 集群 ID，再将该值写入省略集群 ID 的 registration。写出的 manifest 会带着明确的集群 ID 签名。指定其他集群时，镜像设置、策略授权、发布和服务端接入配置须指向同一个集群。

回退以旧内容发布**新revision**，不覆盖历史、不操作任何worker：

```sh
python3 -m maas_bootstrap.publisher \
  --root /root/wtaobj/maas-bootstrap/private \
  --settings /secure/e5-image-settings.json \
  --signing-key /root/wtaobj/maas-bootstrap/operator/release-signing.key \
  rollback --source e5-pilot-r1 --new-revision e5-pilot-r3
```

回退可能重新外发旧原生token，操作前确认它仍有效且没有因泄漏撤销；停用共享码不撤销已经外发的原生token。运行中的worker读取新revision后仍可能需要维护，不把配置回退当作推理进程回退。

## 下一批实际验收

先完成母版generalize/依赖和旧启动钩子核对，再安装E5文件；准备完成后交由管理员保存新快照，并由管理员创建、启动独立克隆A/B。管理员交付访问入口和快照版本后，我们再执行真实验收。验证同码同配置、独立名称/UUID、真实GPU容器、原生注册和模型请求、SSH手动重复apply、错误配置不破坏原worker。不能重启001/002，不能在母版上启动实验worker。详见[E5部署矩阵](../../../docs/framework/E5-TEST-DEPLOYMENT.md)。


## 2026-10-26部署状态更新

客户端已手动部署到tower，固定镜像已缓存；旧agent/provision disabled/inactive，E5开机单元enabled/inactive，母版不注册worker。发布CLI仍未在001线上演练，封版/真实新克隆验收待完成。后续以[实际部署手册](MANUAL-TOWER-DEPLOYMENT.md)为准；其第15节记录本次证据，不把本文早期未部署状态作为现状。
# 独立模型存储启动契约 model-storage-v1

[宿主 NFS 按需挂载](NFS.md)支持在新母版上设置 `model_nfs_from_release: true`，由签名 `model_nfs` 下发远端地址、导出路径和校验信息。旧 NFS 快照继续对比本机与签名发布的 `model_nfs`。两种 NFS 模式都会在启动时准备本地 automount，首次目录访问才连接远端。下述本地 marker 适用于原本使用本地目录的节点。

客户端默认创建/srv/maas/models只读发布目录和/srv/maas/model-cache可写缓存，通过明确bind传给原生worker；配置可用model_root/model_cache_dir指定独立规范路径。二者不得覆盖/包含原生身份目录、bootstrap秘密、对方目录或宽泛系统根目录，不跟随symlink。image-contained/model内只预置公开marker，实际OCR权重仍在推理镜像中。

worker设置GPUSTACK_CACHE_DIR到独立缓存，显式启用原生mirrored并指定自身容器名；保留固定镜像内既有ignore列表，并加入/var/lib/gpustack，防止原生身份卷被传给模型容器。只读发布目录保持只读，可写缓存会保持可写；这不构成privileged容器的强隔离，也不等于HF/ModelScope下载已验收。

新布局具有独立版本/摘要标签。每次开机或手动apply核对真实Mounts、读写属性及关键环境配置，旧布局/额外挂载/摘要不符返回NEEDS_MAINTENANCE，不会自动停止或删除容器；改变本机路径也必须维护。签名manifest中的model_mounts仍不接受任意远程挂载，本项为受控本地启动契约。

存量节点维护若保留停止的旧容器，必须写root-only state_dir/maintenance-backups.json，schema_version=1、container_ids为获准的完整容器ID。只有显式列入、受本工具标签管理、固定镜像/同一原生身份bind、restart=no且停止的容器可被忽略；运行的旧容器仍拒绝启动第二worker。此文件是节点维护记录，不能进入母版或共享模型目录。

已有节点须暂停并排空MaaS、停止模型副本，再由运维重建worker容器，保留身份数据与可回退旧容器；本工具apply本身没有stop/rm/reboot功能。001/002禁止VM重启的约束始终生效。母版仅安装程序/准备公开目录，不执行apply，不保存原生身份；快照由管理员另行操作。

## 可选registry-access-v1启动契约

新客户端支持签名manifest.registry_access与受保护registration.registry_access。manifest只含endpoint、credential_id、payload_sha256及schema_version，签名绑定证书/用户名/密码的完整载荷摘要；秘密仅经验证的配置HTTPS传输并root-only保存。本地allowed_registry_endpoints必须显式允许目标，例如10.11.31.1:18443；未启用时保持model-storage-v1。

启动worker前准备精确Docker certs.d证书及state_dir/registry-auth/<payload_sha256>/config.json，目录700/文件600；专用目录只读挂入worker的/run/maas-registry-auth，DOCKER_CONFIG指向它。只配置单个endpoint认证，无全局registry用户名密码env、无publisher凭据。新挂载加入mirrored ignore列表，不传入推理容器。

已有worker fingerprint/布局不匹配返回NEEDS_MAINTENANCE，不自动重建或覆盖证书；证书/凭据轮换仍须显式维护，不承诺热轮换。配置拉取失败不伪称应用成功。发布新版配置前先部署客户端，新快照首启须单独验收，当前不表示已向母版安装。

## 无盘 GPU 接续试点

试点使用签名的 `worker_name_policy=gpu-external-id`。配置服务和原生 GPUStack 入口都需要使用客户端信任的 HTTPS 证书，原生入口还须在本机 `allowed_server_urls` 内。此模式的 `registration` 保留 `cluster_id` 和可选仓库配置，启动许可通过独立接口下发。客户端设置 `state_dir=/run/maas-bootstrap`、`worker_data_dir=/run/maas-bootstrap/gpustack-worker` 和 `worker_start_grant_file=/run/maas-start/boot-grant`。镜像只带授权文件路径，运行时身份目录位于 tmpfs。

试点应使用单独的配置服务实例和发布目录。每个发布目录只有一个活动版本。`test1` 可作为共同的原生目标集群，现有 `native-independent` 客户端继续读取原配置服务。

存量节点转入试点时，运维可保留已停止、`restart=no` 的旧 E5 容器及其原生数据。把旧容器改名后，在试点 `state_dir/maintenance-backups.json` 写入权限 0600 的记录，包含 `schema_version=2`、旧容器的完整 `container_id` 和实际原生 bind 来源 `legacy_worker_data_dir`。新 worker 仍使用独立 tmpfs 目录。客户端核对旧容器的完整 ID、E5 标签、镜像、状态和 bind 后才允许启动。

在目标 GPUStack Server 安装 worker enrollment 0.3.0，并启用 `MAAS_WORKER_HANDOFF_MODE=required`。配置服务使用三个独立环境变量启用 `POST /bootstrap/v1/worker-start`。

- `MAAS_BOOTSTRAP_WORKER_START_STATE` 指向仅服务账号可读写的授权目录，权限 0700。
- `MAAS_BOOTSTRAP_NATIVE_ENROLLMENT_URL` 指向同一 GPUStack Server 的内部 HTTPS 或回环 HTTP 入口。
- `MAAS_BOOTSTRAP_NATIVE_ENROLLMENT_KEY_FILE` 指向服务端插件专用密钥文件，权限 0600。

操作员核对设备归属和旧 VPC 退出或隔离后，以服务账号运行授权命令。接续时额外传入 `--worker-id`、`--previous-session-id` 和 `--fence-receipt`，其中 receipt 填写经核验的退出记录编号。

```sh
python3 -m maas_bootstrap.enrollment \
  --root "$APPROVAL_STATE" --output "$PRIVATE_BOOT_GRANT" \
  --site-id "$SITE" --batch-id "$BATCH" --cluster-id "$CLUSTER_ID" \
  --gpu "$GPU_UUID" --image-id "$IMAGE_ID" --config-revision "$CONFIG_REVISION"
```

通过受控渠道把 0600 的授权文件交给目标 VPC，放到 `/run/maas-start/boot-grant`，再运行 `apply`。共享配置码只用于读取签名发布。MaaS 绑定授权到发布版本、集群和 GPU，E5 启动前核对绑定，原生注册后再核对 `me=true`、worker ID、设备和 `ready`。重复调用可取回同一份有效许可。Docker 已创建、尚未执行的容器可重试启动。已退出的容器需要新的受控启动会话。

## 无盘克隆的开机交付

使用本版离线安装工具准备无盘克隆。镜像会启用 `maas-worker-bootstrap.path`，监视 `/run/maas-start/boot-grant`。许可到达后，systemd 启动现有的 `maas-worker-bootstrap.service`。目标克隆通过已配置的 HTTPS 信任材料拉取并核验签名发布。

运维先确认旧实例退出或隔离及同卡分配，记录退出凭证，并从已核验的目标 SSH 会话读取 `/proc/sys/kernel/random/boot_id` 和完整 GPU UUID。用能访问授权目录的服务账号运行下面的命令。该账号的 SSH 配置须为目标别名指定已核验的主机密钥和能运行 E5 的 root 登录。每次交接使用独立的私有操作目录。

```sh
python3 -m maas_bootstrap.delivery \
  --root "$APPROVAL_STATE" --operation-dir "$PRIVATE_HANDOFF_DIR" \
  --ssh-host "$TARGET_ALIAS" --boot-id "$TARGET_BOOT_ID" --gpu "$GPU_UUID" \
  --cluster-id "$CLUSTER_ID" --config-revision "$CONFIG_REVISION" \
  --worker-id "$WORKER_ID" --previous-session-id "$PREVIOUS_SESSION_ID" \
  --fence-receipt "$VERIFIED_EXIT_RECEIPT"
```

命令在同一条 SSH 连接上校验目标的启动 ID、GPU、集群和签名发布，再原子交付许可。SSH 中断后用同一操作目录和原参数重试，复用已有授权。若出现 `DELIVERY_NEEDS_RECONCILIATION`，先核对本地许可文件和服务端会话记录。许可逾期或新一轮开机时，按新的退出证据准备下一次交接。保留运行时许可文件供同一轮交接重试，文件随 tmpfs 清空。

`GRANT_DELIVERED` 表示许可已交付。用 `systemctl status maas-worker-bootstrap.service` 和 GPUStack 核对原 worker ID 恢复 `Ready`，再测试旧凭据和试点 OCR。开机时缺少许可的克隆应停在等待状态。

`WORKER_START_NEEDS_RECONCILIATION` 表示 MaaS 发出原生许可请求后缺少确定结果。运维通过授权目录中的 `issue.json` 核对 worker ID，并查询 GPUStack 侧该 worker 的接续记录。若原生许可已发出，后续按已存在的会话处理。只有确认原请求未发出许可时，才可重新授权。真实退出和存量隧道、模型恢复属于 P4 现场验收。 [Issue #16](https://github.com/WTaoGitHub/cybercafe-maas/issues/16)

### 首次注册与同卡接续自动发证

自动模式用于信任已认证 worker 从本次运行环境上报实际单卡 GPU UUID 的试点。在对应批次的 `policy.json` 授权项中设置 `"auto_enroll_gpus": true`，MaaS 校验当前签名发布的集群、镜像与批次范围后，自动调用既有预登记接口创建或匹配 `name=external_id=GPU UUID` 的记录，再发放启动许可。首次新 GPU 无需运维预建 worker 或逐卡填写批准映射。既有绑定的唯一性、并发与 `not_ready` 接续条件仍由原生插件在事务内检查。

`auto_enroll_gpus` 缺省为 false，保留仅批准名单中的 GPU 可自动启动的受限模式。`auto_start_gpus` 仍可指定完整 GPU UUID 到数字 `worker_id` 的明确绑定；有此绑定时继续核对原 ID，不自动替换冲突记录。无映射且启用自动预登记时，由服务端预登记结果确定 ID，运行态保存在授权目录，MaaS 不改写只读策略文件。开关及绑定配置类型非法时拒绝发证。

构建试点镜像时，在无盘镜像设置中加上 `"worker_start_automatic": true`，启用独立 timer，由 E5 读取实际 GPU UUID 和 `boot_id` 并认证申请。已交付的人工许可文件优先使用。未获许可时保持无 worker，准备任务按现有总预算重试。对已经支持自动申请的客户端，此次自动预登记修复只需更新 MaaS 服务端并启用批次开关，无需更新母版快照。

同一启动复用同一份有效许可。MaaS 在授权目录保留该集群与 GPU 的启动绑定和签发记录，不同启动竞争时，已保留或正在注册的会话会阻止另一启动获批。已有活动会话时，enrollment 0.4.0 在数据库锁内确认 GPU 绑定唯一且旧 worker 为 `not_ready`，再撤销旧凭据并签发接续许可。许可过期或签发结果不明确时，由运维对账。


## 可选 worker 关机事件（2026-11-18）

母版已安装通知客户端并设置开机启用。新平台快照与独立克隆首启待验收。[现场部署记录](../../../results/maas-worker-shutdown-events-20261118/DEPLOYMENT.md)

客户端在正常 Ubuntu poweroff/halt 的停止事务中尽力发送一次通知，整体预算 1 秒，失败照常关机，不重试、不等待排空。每次新生成 UUID；该功能只记录事件，不更改 worker/模型状态。服务端保存 48 小时。[方案与实施状态](../../../docs/framework/WORKER-SHUTDOWN-EVENTS.md)。

1. 服务端先配置专用 HMAC 密钥并开放批准节点可达的精确 HTTPS 接收路径；Bootstrap 服务配置 `MAAS_BOOTSTRAP_SHUTDOWN_EVENT_KEY_FILE`，见 [服务端变量示例](../shutdown-events.env.example)。两端分别保存同一密钥内容的 0600 文件，按各自服务 UID 配置，worker 和母版不持有该密钥。
2. 将 [客户端配置片段](shutdown-events.example.json) 的 `shutdown_events` 合并进受控本地 Bootstrap JSON，URL 为实际受信的 MaaS HTTPS 接收地址，CA 文件须能验证该地址。新镜像使用 GPU UUID/启动会话模式；原启动流程附加下发节点范围凭据，注册成功后原子写入 `state_dir/shutdown-event.json`，0600；身份/状态查询不输出凭据。通知准备失败只在 apply 结果的 `shutdown_event` 字段记录代码，不阻塞 worker 注册。
3. 离线镜像构建工具会复制发送模块与独立单元，校验并复制公开 CA，只有配置了本功能才创建通知单元 enable 链接；不打包节点凭据。存量安装须同步更新客户端依赖文件及 `maas-worker-shutdown-notify.service`，不能只复制单个 Python 文件。

存量运行节点补装不需要重新注册或重启：只读核对现有 `identity.json` 和本次 `boot_id`，生成仅含 `cluster_id/worker_id/gpu_uuid/boot_id/session_id` 的私有绑定文件；在 Bootstrap 受控端使用同一签发 helper：

```sh
python3 -m maas_bootstrap.shutdown_auth \
  --key-file <private-event-key-file> --binding-file <verified-private-binding.json> \
  --output <new-private-credential-file>
```

命令以 0600 新建文件，拒绝覆盖，不打印凭据。将该文件私有交付到对应 worker，再执行：

```sh
/opt/maas/bin/maas-worker-bootstrap --config /etc/maas/bootstrap.json \
  shutdown-prepare --credential-file <private-credential-file>
```

只显示 `SHUTDOWN_EVENT_PREPARED` 等代码，不操作原生注册/worker 容器。此后检查安装的单元和配置，才启用 armed 状态：

```sh
systemd-analyze verify /etc/systemd/system/maas-worker-shutdown-notify.service
systemctl daemon-reload
systemctl enable --now maas-worker-shutdown-notify.service
systemctl is-active maas-worker-shutdown-notify.service
```

armed 单元通过单个同步 ExecStop 发送，systemd 设置 `TimeoutStopSec=1s`、超时 kill 整个进程组。发送器检查本次 poweroff/halt 作业，因此普通 service stop/restart、容器停止、重启系统的作业不发关机预告；不替换系统 shutdown 命令。测试/实际时序仍需独立 Ubuntu 验证，不能把静态单元检查当作真实关机验收。

禁用使用 `systemctl disable --now maas-worker-shutdown-notify.service`，正常运行中不应发事件。移除节点凭据和可选配置前，仍保留服务端旧事件的 48 小时清理。母版保护继续生效；XZ-31-001 可按普通 worker 使用，不因旧控制端角色被拒绝。XZ-31-001/002 均不执行整机关机、重启或等效测试；用户已确认实际关机验收暂缓。
