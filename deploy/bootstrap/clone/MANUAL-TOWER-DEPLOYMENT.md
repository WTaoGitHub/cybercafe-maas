# tower 母版手动部署操作手册

版本：2026-10-26；适用于当前 E5 试点、Ubuntu 24.04 x86_64。本文为管理员人工执行说明，并记录2026-10-26实际部署。用户本轮已授权部署到tower，并明确确认“两项旧业务服务由E5替代”；此前暂停部署的限制在本轮被解除。母版仍不启动worker，不自动清理系统身份或制作快照。实际结果见文末执行记录。

目标：在母版安装启动客户端、配置和镜像缓存；母版本身不注册worker。管理员保存新快照并创建独立A/B后，克隆开机加入001的`test1`集群。001继续承载GPUStack Server、配置服务和MaaS控制组件；002保持原状。禁止重启/重建/还原001、002。

本文提供“手动复制业务层文件到运行中的tower”的路径，不调用只支持离线root的`prepare_worker_image.py`；不要把`--root /`传给该工具。本文不自动清理系统身份，也不等于平台镜像封版工具。

## 1. 操作位置、材料及暂停条件

| 位置 | 操作 |
|---|---|
| 本地Mac | 核对交付包，SSH/SCP上传；从001安全中转共享码 |
| tower母版 | 手动安装、暂存拉取测试、缓存镜像；不运行apply或启动E5服务 |
| 管理员平台 | 母版准备检查通过后，保存新快照并创建/首次启动独立A/B |
| 新克隆A/B | 验证自动注册、GPU/模型及手动apply |

当前入口：tower `root@111.4.255.126:6025`；001 `root@111.4.255.126:2023`。本地SSH私钥为`/Users/4paradigm/.ssh/id_ed25519`，仅用于登录，不复制进母版。

需要材料：

- 本地包：`/Users/4paradigm/dev/cybercafe-maas/dist/e5/e5-worker-template-tower-e5-pilot-v1.tar.gz`。
- 本包源码commit：`f7de733f7f1d7c7b58380527fd601dc69c811212`；SHA256：`cb8dd386020c95db1b79d6bc5d2a8858c3d060ef51fa7accd0703b0d64ba9e71`。
- 001受限文件：`/root/wtaobj/maas-bootstrap/operator/shared-code`。它是共享注册码，不是SSH密钥或原生管理员token。
- 管理员确认旧CyberCafe启动逻辑的替换范围及人工快照方式；当前试点不要求供应商提供身份泛化或启动注入能力，见第16节。

**停止推进条件：** 已有E5安装/实例身份、未知Docker容器、依赖检查失败、旧启动逻辑尚未审查、包校验不一致、真实码或证书来源无法确认。先保存现状并处理原因，不覆盖、不擦除、不关闭TLS校验。

## 2. 上传并验证公开交付包

以下命令在 **Mac终端**执行。重新部署需确认自己的部署窗口；已有安装不能直接覆盖：

```sh
python3 - <<'PY'
from pathlib import Path
import hashlib
p=Path('/Users/4paradigm/dev/cybercafe-maas/dist/e5/e5-worker-template-tower-e5-pilot-v1.tar.gz')
print(hashlib.sha256(p.read_bytes()).hexdigest())
PY

ssh -p 6025 -i /Users/4paradigm/.ssh/id_ed25519 root@111.4.255.126 \
  'install -d -m 700 /root/e5-install-input'

scp -P 6025 -i /Users/4paradigm/.ssh/id_ed25519 \
  /Users/4paradigm/dev/cybercafe-maas/dist/e5/e5-worker-template-tower-e5-pilot-v1.tar.gz \
  root@111.4.255.126:/root/e5-install-input/

ssh -p 6025 -i /Users/4paradigm/.ssh/id_ed25519 root@111.4.255.126
```

接下来在 **tower的root终端**执行。每一步成功后再继续；不要把整份手册拼成无检查的大脚本。

```sh
umask 077
sha256sum /root/e5-install-input/e5-worker-template-tower-e5-pilot-v1.tar.gz
# 结果必须与第1节的可信摘要一致，然后才解压。目录须为新建空目录。
mkdir -m 700 /root/e5-install-input/bundle

tar -xzf /root/e5-install-input/e5-worker-template-tower-e5-pilot-v1.tar.gz \
  --no-same-owner -C /root/e5-install-input/bundle

cd /root/e5-install-input/bundle
python3 scripts/maas/verify_worker_bundle.py --root /root/e5-install-input/bundle
```

实测Mac的Perl版`shasum`因C.UTF-8 locale异常退出；上面改用Python标准库，不改全局locale。上传前本地校验、上传后tower校验均须通过；不要在前一步失败后继续解包。

预期：`ok=true`、`files_verified=15`、source_commit与第1节一致。SHA256用于核对从可信来源取得的包，不是第三方数字签名。不要使用来源不明、仅附自报哈希的压缩包。

## 3. 只读检查并记录变更前状态

仍在 **tower**：

```sh
python3 scripts/maas/inspect_worker_template.py
nvidia-smi
/usr/local/cuda-13.0/bin/nvcc --version
python3 -c 'import cryptography; print(cryptography.__version__)'
docker ps -a --format '{{.Names}} {{.Status}}'
```

2026-10-26实测基线：Ubuntu24.04、Python3.12.3、cryptography41.0.7、驱动580.95.05、CUDA13.0.88、Container Toolkit1.20.1、Docker28.2.2、RTX4070 12GB，无Docker容器。不要因此在本次重复升级驱动/CUDA或重启机器。`nvidia-smi`和runtime可见性不等于容器GPU执行已验证。

已观察到失败单元`start_appinstaller.service`和`swappdev.service`，需管理员确认是否影响平台快照/启动/设备分配；本手册不自动修复或忽略其影响。

先确认以下路径没有旧部署；若存在，停止，按升级/维护流程处理：

```sh
test ! -e /etc/maas
test ! -e /opt/maas
test ! -e /etc/systemd/system/maas-worker-bootstrap.service
test ! -e /etc/systemd/system/multi-user.target.wants/maas-worker-bootstrap.service
test ! -e /var/lib/maas-bootstrap
```

标准原生路径（`/var/lib/gpustack`、`/root/.gpustack`、`/etc/gpustack`）若含实例状态也应停止；不要把已有UUID/token带进新快照。

建立root私有备份目录，保存旧服务启用/运行状态及单元定义。审查仅在管理员本地终端进行，完整unit可能包含环境配置，不要原样贴聊天或报告。

```sh
E5_BACKUP_DIR="/root/e5-before-install-$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$E5_BACKUP_DIR"
for E5_UNIT in cybercafe-agent.service cybercafe-provision.service; do
  (systemctl is-enabled "$E5_UNIT" || true) > "$E5_BACKUP_DIR/$E5_UNIT.enabled"
  (systemctl is-active "$E5_UNIT" || true) > "$E5_BACKUP_DIR/$E5_UNIT.active"
  systemctl cat "$E5_UNIT" > "$E5_BACKUP_DIR/$E5_UNIT.unit"
done
```

记录该目录，回退时使用实际备份路径，不依赖跨SSH会话保留变量。本次实际备份：`/root/e5-before-install-20261026-105709`，除单元状态外，还私有备份旧unit、agent/provision/install脚本、config.env及.provisioned，共7个文件；均只留root可访问，不将秘密复制入报告或Git。

## 4. 审查并处理旧启动职责

已知：`cybercafe-provision.service`执行`/opt/cybercafe/provision.sh`，先于agent；`cybercafe-agent.service`执行`/opt/cybercafe/cybercafe-agent.py`。当前两者均启用。供应商`swcfghostname.service`位于network-pre之前，不是E5替换对象。

管理员需审查旧脚本及unit的ExecStart/ExecStop，明确是否承担平台接入、网络、命名、Docker配置或业务调度，是否停止时会触发其他操作。

**只有确认两项旧业务服务由E5替代，且停止无平台/网络副作用后，才执行：**

```sh
systemctl stop cybercafe-agent.service
systemctl disable cybercafe-agent.service
systemctl stop cybercafe-provision.service
systemctl disable cybercafe-provision.service
```

本轮已确认两项旧服务替代范围，实际ExecStop/ExecStopPost为空，agent未发现显式signal/atexit处理注册；停止后两项均disabled/inactive，Docker PID未变。

保留旧脚本和备份便于当前安装回退；旧设备凭据/config.env及含秘密备份迁出/清理已列为低优先级Nice to have（第17节），不阻塞当前试验快照；保留它们会让克隆继承旧凭据，记录这一限制即可，不把保留状态写成已经清理。不禁用`swcfghostname`、`swnetboot_success`、SSH或供应商网络/磁盘服务。若provision含仍需保留的工作，先拆分职责，不直接照抄上述停用命令。停用旧服务后再次检查Docker容器，未知容器不要自动删除。

## 5. 安装客户端与公开材料（不启动）

在 **tower**的包根目录 `/root/e5-install-input/bundle`执行：

```sh
install -d -m 755 /opt/maas/lib/maas_bootstrap /opt/maas/bin
install -d -m 700 /etc/maas

for E5_FILE in __init__.py client.py worker.py cli.py; do
  install -o root -g root -m 644 "maas_bootstrap/$E5_FILE" "/opt/maas/lib/maas_bootstrap/$E5_FILE"
done

install -o root -g root -m 755 deploy/bootstrap/clone/maas-worker-bootstrap \
  /opt/maas/bin/maas-worker-bootstrap
install -o root -g root -m 644 deploy/bootstrap/clone/maas-worker-bootstrap.service \
  /etc/systemd/system/maas-worker-bootstrap.service
install -o root -g root -m 644 results/maas-e5-preparation/bootstrap-server.crt \
  /etc/maas/server.crt
install -o root -g root -m 644 results/maas-e5-preparation/bootstrap-release-signing.pub \
  /etc/maas/release-signing.pub
```

没有复制配置服务、发布工具、服务端私钥或数据库到母版；没有创建worker实例。

## 6. 安全转交共享注册码

先在 **Mac另一个终端**执行。不要使用`cat`显示码，不把实际码放进命令参数或JSON示例。

```sh
umask 077
E5_TRANSFER_DIR="$(mktemp -d /tmp/maas-e5-transfer.XXXXXX)"

scp -P 2023 -i /Users/4paradigm/.ssh/id_ed25519 \
  root@111.4.255.126:/root/wtaobj/maas-bootstrap/operator/shared-code \
  "$E5_TRANSFER_DIR/shared-code"
chmod 600 "$E5_TRANSFER_DIR/shared-code"

scp -P 6025 -i /Users/4paradigm/.ssh/id_ed25519 \
  "$E5_TRANSFER_DIR/shared-code" \
  root@111.4.255.126:/root/e5-install-input/shared-code
```

两次SCP均成功后清理Mac临时副本：

```sh
rm -f "$E5_TRANSFER_DIR/shared-code"
rmdir "$E5_TRANSFER_DIR"
```

在 **tower**安装并清理上传暂存副本：

```sh
install -o root -g root -m 600 /root/e5-install-input/shared-code /etc/maas/shared-code
rm -f /root/e5-install-input/shared-code
```

这是当前接受的“母版预置共享码”试点方案：拿到母版/克隆root权限的人可能取得共享码和通过它读取的原生注册token。不复制001的原生管理员凭据，不把共享码传给`docker run -e`或写进公开包。

## 7. 写入本机配置

在 **tower**运行以下Python，只创建目标文件，不覆盖旧文件；示例中的路径已经是安装后路径：

```sh
python3 - <<'PY'
import json,os
cfg={
  'schema_version':1,
  'config_service_url':'https://10.11.31.1:18840',
  'shared_code_file':'/etc/maas/shared-code',
  'tls_certificate_file':'/etc/maas/server.crt',
  'release_public_key_file':'/etc/maas/release-signing.pub',
  'release_key_id':'e5-pilot-signing-v1',
  'state_dir':'/var/lib/maas-bootstrap',
  'worker_data_dir':'/var/lib/maas-bootstrap/gpustack-worker',
  'site_id':'cybercafe-lab','batch_id':'e5-pilot',
  'cluster_id':3,'image_id':'tower-e5-pilot-v1','architecture':'x86_64',
  'allowed_server_urls':['http://10.11.31.1:18080'],
  'cloud_backup':{'url':'http://configuration.cybercafe.phanlink.cn','enabled':False}
}
fd=os.open('/etc/maas/bootstrap.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump(cfg,f,indent=2);f.write('\n')
PY
```

此处不写原生token，客户端将经验证的HTTPS取得。证书只信任本试点001地址；正式部署到独立控制机时，需更新地址、证书和范围配置，不直接照搬测试身份。

## 8. 缓存固定worker镜像

在 **tower**，Docker运行且旧业务职责已处理后执行；只拉镜像，不运行容器：

```sh
E5_WORKER_IMAGE='quay.io/gpustack/gpustack@sha256:55ecc762950a32641b6a4d96195c0662ef72998eaeeda31bba0e8e218d41afef'
docker pull --platform linux/amd64 "$E5_WORKER_IMAGE"
docker image inspect "$E5_WORKER_IMAGE" \
  --format '{{.Os}}/{{.Architecture}} {{json .RepoDigests}}'
```

必须显示linux/amd64，RepoDigests包含上述完整引用。不要改用latest、只核对tag、或跳过摘要检查。若母版不能访问镜像源，先解决受控下载/镜像分发方案；经`docker save/load`转交后也必须重新核对RepoDigests，不能假定一定保留，不能用重新打tag冒充摘要验证。拉取失败时暂停封版，不启动替代版本。

## 9. 母版只做临时prepare验证，不做apply

**不要在母版执行普通`prepare`写入最终实例目录。** 以下测试把返回的私有配置放在临时`/run`目录，结束自动删除；只输出脱敏状态，不创建worker。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/maas/lib python3 - <<'PY'
import json,os,tempfile
from pathlib import Path
from maas_bootstrap.client import LocalSettings,prepare
with tempfile.TemporaryDirectory(prefix='maas-e5-check-',dir='/run') as tmp:
    cfg=json.loads(Path('/etc/maas/bootstrap.json').read_text())
    cfg['state_dir']=str(Path(tmp)/'state')
    path=Path(tmp)/'settings.json'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(cfg,f)
    settings=LocalSettings(path)
    a=prepare(settings);b=prepare(settings)
    assert a['code']=='PREPARED' and a['fingerprint']==b['fingerprint']
    print(json.dumps({'https_and_signature_ok':True,'revision':a['revision'],
                      'cluster_id':a['cluster_id'],'repeat_same':True,'worker_started':False}))
PY
```

成功表示当前共享码、HTTPS信任、签名和范围匹配；不代表GPU或原生注册成功。失败时保留实际错误码在本地检查，禁止使用`curl -k`绕过证书；不要打印完整release响应。临时目录一般退出时自动清理；强杀/断电可能留下`/run/maas-e5-check-*`，封版前检查并只清理本次测试目录。

母版到原生18080不通目前不作为阻塞，但**克隆A/B到18080及原生反向网络必须验收**。不要因母版不通直接放宽001防火墙。

## 10. 安装检查与启用开机入口

在 **tower**：

```sh
sh -n /opt/maas/bin/maas-worker-bootstrap
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/maas/lib python3 -c \
  'import maas_bootstrap.client, maas_bootstrap.worker, maas_bootstrap.cli; print("imports OK")'
systemd-analyze verify /etc/systemd/system/maas-worker-bootstrap.service
systemctl daemon-reload
systemctl enable maas-worker-bootstrap.service
systemctl is-enabled maas-worker-bootstrap.service
systemctl is-active maas-worker-bootstrap.service
```

实测`systemd-analyze verify`退出码0，但附带供应商`/usr/lib/systemd/system/start_appinstaller.service`带可执行位的警告（Proceeding anyway）；它不是E5 unit失败，本次未修改供应商权限。

预期enabled、当前inactive。**不要使用`enable --now`、`start`、`restart`或直接`apply`。** 当前代码会拒绝在tower/001/002上启动worker，这个保护不应删除或绕过。启用只设置以后开机入口，母版当前不启动；母版若后来重启，该保护仍会拒绝其注册。

unit成功后不持续监护worker容器；容器使用restart=no，后续退出不会由该oneshot单元自动恢复。启动阶段失败有界重试，10分钟最多5次；不要把此功能理解为全天候自动修复。

## 11. 封版前检查与身份初始化交接

本节以第16节的最新试点边界为准：软件/配置和实例状态清洁仍需满足；独立OS身份不再作为试验快照的前置。正式生产身份泛化仍需另行评估。

- 客户端、配置、共享码和公开信任材料到位；`/etc/maas`为root:root/700，shared-code与bootstrap.json为root:root/600；固定镜像摘要匹配。
- 临时prepare通过，临时私有返回数据已清理；母版无worker容器、无`/var/lib/maas-bootstrap`实例目录，无标准原生路径UUID/token。
- 旧业务启动服务已按审查结论处理；供应商网络/命名及SSH入口保留；E5单元enabled但当前inactive。
- `hostname`仍是tower，克隆首次启动会获得不同hostname/IP；E5不自行更改它们。克隆若继承tower名称或001/002地址，会被本机保护拒绝。
- 当前试点保留供应商现有machine-id、SSH主机密钥和管理员authorized_keys，不删除、不重建、不要求供应商提供首启钩子。记录共用系统身份/密钥的限制，真实A/B验证SSH和原生worker独立。
- 记录平台失败单元、当前依赖、镜像摘要、E5源码版本；是否影响网络/GPU/注册，以新克隆实测判断，不等待供应商给出技术结论。

**本文不提供在运行中tower删除machine-id或SSH私钥的命令。** 当前E5单元也不负责生成这些系统身份；仅删除密钥而没有首启重建流程可能使新克隆SSH不可用。若平台仅支持现有在线快照且不提供初始化，按第16节保留现状做受控试验；这不等于生产身份隔离已实现。

`inspect_worker_template.py`是清单，不是自动验收器：它的snapshot_ready固定为false，如果运行过普通prepare/apply，新增实例状态也会被列为需审查项。不要为让报告变绿而删除正常安装文件，需人工按本节记录结论。

可在本机用以下命令核对权限与实例状态（不显示秘密）：

```sh
stat -c '%a %U:%G %n' /etc/maas /etc/maas/bootstrap.json /etc/maas/shared-code
test ! -e /var/lib/maas-bootstrap
docker ps -a --format '{{.Names}} {{.Status}}'
```

可以保留公开安装包作版本追溯；清理暂存目录中的共享码/返回payload，服务端私钥、原生管理员token和本地SSH私钥不得存在于母版。

## 12. 管理员制作快照与创建A/B

由管理员在平台上记录并交付：新版快照ID/版本、来源母版、A/B名称及IP、SSH入口、每台GPU分配信息。只创建独立新VM，不改造或重启001002。必要的平台停机/封版动作由管理员另行安排，本手册没有自动重启命令。

A/B首启应先完成供应商命名/网络和系统身份初始化，再由E5拉配置注册。当前E5单元依赖network-online和Docker；若供应商初始化发生得更晚，需先适配并验证systemd顺序，不通过无限重试掩盖时序错误。

## 13. 新克隆验收（以下命令只在A/B执行）

先记录自动启动结果，再考虑手动恢复；否则不能证明开机自动注册通过。

```sh
systemctl status maas-worker-bootstrap.service --no-pager
/opt/maas/bin/maas-worker-bootstrap status
docker ps --filter name=maas-gpustack-worker --format '{{.Names}} {{.Status}}'
```

在001 GPUStack UI确认test1中出现A/B两个独立worker、名称/UUID不同、GPU可见且状态正常；同时核对不同machine-id/SSH公钥指纹。不要公开worker_token或docker inspect的完整Env。

自动启动证据保存后，再在A/B运行：

```sh
/opt/maas/bin/maas-worker-bootstrap apply --trigger manual
```

正常应返回UNCHANGED（相同配置已确认且容器仍在运行），worker不重复注册/创建。若先前自动失败，手动成功只能证明手动流程恢复，自动首启仍需单独复验。status是本地历史状态，不是实时健康证明。

错误码处理：

| 错误 | 处理 |
|---|---|
| PROTECTED_HOST_WORKER_FORBIDDEN | 核对是否仍为母版或继承受保护hostname/IP，不绕过保护 |
| PINNED_WORKER_IMAGE_UNAVAILABLE / WORKER_IMAGE_DIGEST_MISMATCH | 核对缓存摘要，回到母版制备修复 |
| NATIVE_SERVER_UNREACHABLE | 检查克隆到001:18080路由/防火墙，不改配置服务TLS策略 |
| REGISTRATION_UNCONFIRMED | 保留实际容器，核对原生记录和网络；不要盲删容器、清UUID或重建worker |
| NEEDS_MAINTENANCE | 配置改变，进入维护流程；本工具不自动替换旧容器 |
| OTHER_WORKER_PRESENT | 排查旧启动逻辑或实例残留，不随意删除未知容器 |

之后在隔离测试模型中做真实GPU容器/推理验证；注册成功不等于模型可用。封版失败在母版构建流程修正，克隆业务故障按原生状态处理，不重启001002。

## 14. 母版阶段回退

仅适用于母版尚未运行E5 worker的安装回退：

1. `systemctl disable maas-worker-bootstrap.service`，保留新增文件供诊断，不自动删除配置或改Docker。
2. 根据第3节备份的实际enabled/active状态恢复旧业务服务；不要无条件enable/start全部单元。先确认没有新旧两套逻辑并行。
3. 不回滚NVIDIA驱动、CUDA、Docker，也不重启VM；不恢复001002镜像。
4. 共享码若发生泄漏，在001执行停码/换码，并评估已外发原生token；仅删除母版文件不能撤销凭据。

如果新克隆已运行worker，此回退段不适用；应先按维护流程处理原生实例和GPU占用，再决定替换配置/组件。


## 15. 2026-10-26实际部署记录（已执行）

本节覆盖此前“仅准备、未部署”的历史状态。用户授权在tower实际部署，并确认旧agent/provision由E5替代。

| 阶段 | 实际结果 |
|---|---|
| 变更前备份 | /root/e5-before-install-20261026-105709，7个文件及before.json；root私有，未复制秘密入Git |
| 旧业务退出 | 两项均disabled/inactive；ExecStop/ExecStopPost为空；Docker未重启 |
| 包及安装 | 源码f7de733，包SHA256一致，15项载荷校验通过；8个已安装公开文件逐项匹配包摘要 |
| 凭据/权限 | shared-code经临时私有目录转交；本地及tower中转码均清理；/etc/maas为700，配置与码600，owner均root |
| 配置链路 | 当前001真实HTTPS和Ed25519验证通过；e5-pilot-r1、cluster3；两次prepare指纹相同，/run临时配置已删除 |
| 镜像缓存 | quay.io固定摘要拉取成功，linux/amd64，RepoDigest精确匹配；本地image size 4,941,415,793 bytes（约4.60 GiB，非下载流量/独占磁盘占用） |
| 开机入口 | shell语法、Python导入和systemd unit验证通过；单元enabled/inactive，未使用start或enable --now |
| 母版状态 | 无Docker容器，无/var/lib/maas-bootstrap，未注册worker；machine-id和SSH主机密钥摘要与变更前一致 |
| 平台操作 | 无VM/Docker引擎重启；未制作快照或创建克隆；001002未修改 |

**当前母版E5业务层已部署，真实克隆未验收。** 按第16节的新试点边界，不再等待供应商身份初始化或失败单元解释；检查E5/原生实例状态清洁后，可由管理员制作受控试验快照（旧业务秘密清理不作本轮前置）、创建独立A/B，再实测自动注册和GPU/模型。

线上脱敏结果保存为`/root/e5-install-input/deployment-result.json`；本地证据：[tower-deployment-20261026.json](../../../results/maas-e5-preparation/tower-deployment-20261026.json)。没有把原生token、共享码、配置响应或私钥写入证据。


## 16. 供应商无技术支持/API时的试点操作边界（当前基线）

用户已说明基本无法取得供应商技术支持或网管API。我们适配既有环境，不再要求供应商新增能力，也不将泛化系统身份设为小规模E5试验的阻塞条件。

1. 保留现有machine-id、SSH主机密钥、authorized_keys和供应商网络/hostname机制；不增加OS首启身份模块，不在tower执行重建身份操作。共用密钥风险和其他组件的身份依赖保留记录，不声称生产安全隔离完成。
2. 保持E5实例状态清洁：母版不执行apply，/var/lib/maas-bootstrap和原生worker身份不得固化；旧agent/provision保持disabled/inactive。旧设备凭据/秘密备份清理已下调为第17节的后续增强，不阻塞当前试验快照，不要求修改供应商文件。
3. 管理员仅需人工保存试验快照、创建并启动A/B、分配GPU并提供SSH入口。不需要网管API、可信启动参数、座位资产号、离线编辑或技术支持承诺。
4. 我们以实际访问结果验证网络和命名；若克隆hostname仍为tower导致保护拒绝，先在独立新克隆核查，再通过我们可控的worker适配解决，不删除母版保护或要求改造001002。
5. 验收以独立worker原生名称/UUID、配置拉取、GPU和推理为准；machine-id或SSH主机公钥相同只记录现状，不直接判E5失败。保留SSH主机校验，不通过关闭验证掩盖连接问题。
6. 原检查脚本固定的snapshot_ready=false和离线工具generalization拒绝，仅描述各自检查范围，不作为此受控在线试点的硬门槛；不改写历史取证文件或伪造通过结果。

当前仍没有创建快照或新VM。网络/系统原生行为只有在A/B真正启动后才能验收；失败时由我们在新克隆上取证和适配，而不是承诺供应商会修复。


## 17. 旧凭据与残留清理：低优先级后续任务（当前决策）

用户2026-10-26明确列为Nice to have，详见[E5.F4](../../../docs/framework/ENHANCEMENTS.md#e5-f4-legacy-cleanup)。覆盖前文将旧业务秘密清理列为快照/封版前置的要求：本轮暂缓清理，不阻塞受控试验快照、A/B注册和推理验证；旧服务仍需保持disabled/inactive。

备份优先放Mac。目前本地归档`/Users/4paradigm/dev/cybercafe-maas/.maas/backups/tower-legacy-20261026-113634/tower-legacy-private.tar.gz`及verification.json均存在，核对记录显示归档和15个源文件完整性验证通过。备份含秘密，不入Git、不作为公开交付物，不在聊天中显示内容。此前工具调用被中断不等于传输失败；不得重复宣称本地备份未完成。

母版旧配置、标记、含设备凭据的agent脚本、原回退目录及`/root/e5-legacy-export-20261026-113634`导出副本均未删除。下一次实施先核对现状和Mac备份，再按明确清单处理；当前不要执行删除。原第14节回退所需材料仍在母版，今后实际清理后需改为从Mac恢复。

不清理旧凭据的主要限制是克隆继承旧秘密和误启动旧程序的可能性；不据此认定当前推理/接入一定失败。E5/原生已注册身份不能随母版复制仍是必要要求，不因本项降级而放宽。供应商machine-id、SSH主机密钥、管理员authorized_keys、网络配置和有效E5共享码继续保留。


## 18. 新VM与旧002的集群边界

管理员回收旧002后，用新快照创建的新VM应加入test1（ID3）；002原属cybercafe-poc（ID1）。“替代002提供模型服务”是业务接替，不是把新worker注册进旧集群。先验证test1自动注册，再显式部署模型、核对MaaS路由并验证客户入口；模型和原接口不会随worker跨集群自动迁移。

当前保护在注册前匹配XZ-31-002和10.11.31.2，与cluster无关。若新VM复用这些名称/地址，会被PROTECTED_HOST_WORKER_FORBIDDEN拒绝；应在快照前确认或调整本系统的保护判定，再验证真实首启。此处只是登记问题，代码尚未变更，不要求供应商提供API。旧凭据清理仍暂缓。


## 19. 后续增强：角色驱动的启动保护

[E5.F5](../../../docs/framework/ENHANCEMENTS.md#e5-f5-role-based-guard)已列入后续计划：角色/启动许可取代固定名称和IP，仍保护controller与template，同时允许合法worker复用地址。角色字段不能简单固化为template后复制给所有克隆，生命周期需另行设计；不要求供应商API或系统身份泛化。此项尚未实施，不能据此忽略第18节当前002复用误拦截。它仅防误操作，不负责身份认证或VM重启控制；本次没有修改代码或服务器。


## 20. 旧002名称/IP复用修正（本地已完成，tower待部署）

用户授权后源码596516e已完成最小兼容修正，38项bootstrap测试通过：允许新克隆使用XZ-31-002或10.11.31.2，仍拒绝001/tower名称及001地址。E5.F5通用角色改造仍为后续计划，本次没有实现角色系统。

本轮6025公网SSH和经001访问原10.11.31.3:22均超时，未向tower写入补丁。当前暂停保存最终试验快照；等待管理员确认母版在线及有效入口。原f7de733包未重建，保留旧限制，不能将源码已修改理解为母版已更新。

恢复后只替换/opt/maas/lib/maas_bootstrap/worker.py，先核对远端版本和Mac备份，再原子安装并确认新摘要、语法/导入、单元enabled/inactive和无实例身份。不要启动服务或apply，不清理旧凭据；本地补丁目录dist/e5/002-guard-patch-596516e，after SHA256为0d02619f9eff5624edf68aaba1f18b5b63254a94dde54dc755f971eba8a3ac9d。记录本轮实际未部署，不覆盖此前首次安装证据。


## 21. 2026-10-26：管理员确认新母版，002兼容补丁已部署

当前6025入口对应的新母版为tower-8CbKAhaaf，管理员已确认其角色。源码fbd3d3e在允许新克隆复用XZ-31-002名称/10.11.31.2地址的同时，将新母版名称纳入保护；001名称及10.11.31.1保护保留。该补丁已原子替换母版的/opt/maas/lib/maas_bootstrap/worker.py，旧文件优先备份到Mac。此前SSH不可达、补丁未部署的记录为历史状态。

38项本地bootstrap测试通过；远端摘要、导入、真实母版拒绝、模拟002复用允许和控制端IP拒绝均已核验。cluster3配置保持不变，无容器、无E5/native注册身份。单元enabled/failed为此前开机尝试留下的状态，本轮未启动、重启或重置单元，未运行apply，未修改驱动、旧凭据或001/002。真实克隆启动注册尚未验证，模拟保护测试不等于注册验收。

GPU仍不可用：当前PCI 10de:2f04要求NVIDIA open kernel modules，实际加载闭源模块，nvidia-smi显示No devices found。母版到001:18080此前仍超时；按既定边界，原生端口重点在新克隆实测。下一步先单独处理GPU模块兼容，再由管理员安排受控快照/新克隆，验证配置拉取、test1自动注册、GPU和模型服务。不得把本次补丁成功写成GPU环境或真实首启验收通过。

原f7de733归档包仍是历史版本，重装会丢失本次修正；需使用当前源码重新构建，或显式叠加fbd3d3e模块并核对摘要。证据见[部署核验](../../../results/maas-e5-preparation/002-guard-patch-deployment-20261026.json)，GPU只读取证见[new-tower-comparison-20261026.json](../../../results/maas-e5-preparation/new-tower-comparison-20261026.json)。E5.F4清理、E5.F5通用角色保护继续暂缓。


## 22. 2026-10-26：TOWER母版名称保护规则更新（已部署）

管理员确认“TOWER就是改镜像的”。当前试点据此采用命名约定：hostname忽略大小写后，等于tower或以tower-开头即拒绝启动worker；不再逐台登记随机后缀。XZ-31-001名称保护也忽略大小写，10.11.31.1地址保护保留；002名称/IP继续允许复用。towerish-worker、worker-tower不匹配母版规则。

保护在配置拉取、环境检查和容器创建前执行，因此母版的开机入口及手动apply均不会进入接入流程。此规则只用于防误启动，不是身份认证；管理员说明并非完整命名规范，首次普通克隆仍需实测hostname。若普通克隆继承tower名称会被拒绝，应先取证再适配，不直接移除母版保护。

本地40项bootstrap测试通过，母版模块已备份到Mac后原子替换；远端真实名称及模拟名称保护、002复用均验证通过，installed SHA256为7db3e0709fec7728e8c310798879b5dc14c589b3845823f953f66fb59b465ae5。无容器、无注册身份，原enabled/failed历史状态保留。本次未运行apply、未启动worker、未重启VM或改驱动。GPU兼容问题及真实克隆注册仍未验收；若只分配4070/4080，可先用新克隆验证现有驱动，不将母版5070修复作为这项试验的强制前置。

[本次部署证据](../../../results/maas-e5-preparation/tower-prefix-guard-deployment-20261026.json)。原f7de733归档包未更新，重新部署须使用当前源码构建或叠加当前模块并验证摘要。通用角色识别E5.F5仍为后续增强。


## 23. 当前执行顺序：先验证第一版快照，再应用新改动（2026-10-26用户明确）

管理员当前可用于创建VM的最新已保存镜像仍是第一版，包含E5自动启动/原生注册逻辑，但不包含后续“放行002名称/IP”和“保护tower及tower-*”修正。当前tower文件已安装新补丁，不等于已保存镜像包含补丁；不推断平台会自动持久化母版改动。此前部署证据仅证明当时母版文件的状态。

1. 管理员使用第一版已保存镜像创建独立普通VM，提供SSH入口、预期镜像版本及GPU型号；本系统不执行平台操作，不回收或重启001/002。本轮先不要求保存新快照，也不在新VM上提前打补丁或手动apply。
2. 新VM上线后先只读取证：核对hostname/IP、worker.py摘要、启动时间、E5错误码与日志、GPU/容器运行时、到001:18840及18080连通性、独立身份/容器和test1原生worker记录。保留首次开机证据，不把人工补救算成自动注册成功，不输出凭据。
3. 第一版worker.py参考SHA256为4b6a48c469aad46d261cee29b937c4dd279ad5556bd91481aefe28e53f87dc9f，以现场文件核对为准。复用XZ-31-002或10.11.31.2会被旧保护拒绝，这是本轮已知预期限制，不算新回归。第一版只保护原tower具体名称，不含通用tower-*保护；管理员应确认新VM是普通克隆。其他合法名称/IP才可继续观察原有自动注册流程。
4. 完成第一版结果记录后，再应用新补丁并作对照验证。若首轮已创建worker/注册身份，先检查运行状态及变更影响，再安排配置应用，不删除身份或强行重复注册。补丁后人工apply仅证明手动路径；新版真实首启仍需管理员保存包含补丁的后续快照，并另建独立克隆验证。
5. 新版参考摘要为7db3e0709fec7728e8c310798879b5dc14c589b3845823f953f66fb59b465ae5，源码d81f02b。现有母版补丁不回滚；此次只更新执行计划，不修改服务器或镜像。旧凭据清理、角色系统等后续增强继续暂缓。

阶段验收分别记录“第一版自动启动结果”“新补丁部署/手动验证结果”“新版快照真实首启结果”，不能互相替代。若分配4070/4080可先实测现有驱动；GPU实际识别、容器访问和注册结果仍以新VM取证为准。


## 24. 2026-10-26：004网络修复与手动接入验收

001持久化策略新增native_server_cidrs=["10.11.31.0/24"]，支持后续多个IPv4 CIDR。仅在001局域网接口放行到10.11.31.1:18080/tcp：raw开机过滤、HOST和DOCKER链均受控；Docker按conntrack原始目的地址/端口匹配，未扩大所有端口或容器直连许可。原配置18840规则保留；其他业务端口、IPv6和SSH范围不变。重用原boot/refresh服务实现持久化，未重启系统或Docker。策略源码及配置已备份至Mac和001私有目录。

004实测18840/18080连通，原生/v2/workers未认证返回401，18834/8820/11435仍超时；修复前18080超时，修复后连通，完成实际网络对照。refresh再次执行成功，边界rules_ok/inventory_matches和IPv4/IPv6早期hook验证通过；实际整机重启持久性未测试且不得在001002执行。

首次人工apply实际注册了worker ID4至test1(cluster3)，原生状态ready/unreachable=false，但E5误报REGISTRATION_UNCONFIRMED。查v2.2.3 WorkerPublic定义，worker_uuid明确exclude=True，不会在列表响应中出现。确认逻辑已修复为使用本机原生worker token访问/v2/workers?me=true，严格要求单条记录、me=true、集群及本机名称匹配；本机UUID/token/名称文件仍须存在且有效。不是忽略身份或改用管理员token，也不重放注册接口。

修复仅替换004与当前tower模块中的确认条件，分别保持各自名称保护版本；004仍未安装“放行002/保护tower-*”改动。Mac保存修改前模块。之后apply返回APPLIED/ready，再次apply返回UNCHANGED，容器ID/启动时间未变，未重复创建worker。容器内nvidia-smi正常识别RTX4080 SUPER/580.95.05。001反向10150和实际metrics_port=10151可达；之前探测10161拒绝仅说明该端口不是当前实际指标端口。bootstrap40项、边界相关7项测试通过。

第一版首启失败证据保留，本次只判“网络修复后的手动注册及重复应用成功”；未做新快照首启、双克隆独立性或模型推理验收。原systemd failed为首启历史状态，手动CLI成功不自动清空。当前tower文件含修正，管理员保存的第一版镜像仍不含它。下一步按用户计划评估安装剩余保护补丁，再由管理员保存新版快照并新建独立克隆验证自动接入；不要重启或重建001002。

[网络修复证据](../../../results/maas-e5-preparation/native-network-fix-20261026.json) · [手动接入与确认修复证据](../../../results/maas-e5-preparation/clone-004-manual-registration-20261026.json)。


## 25. 第二版受控试验快照前核对（2026-10-26）

只读核对当前tower文件：002名称/IP复用、大小写无关tower/tower-*保护及GPUStack223 me确认修复均已安装，模块SHA256为bf34d3d93ae362f3de985a31a81eea5495dcd5739fa6243be251391bfcc6f994。启动入口enabled；无容器、E5 identity/applied或原生token/UUID/name，固定worker镜像已缓存，配置cluster3及信任文件/共享码齐备，旧agent/provision disabled/inactive。到001:18840/18080均可连。仅有apply.lock和旧last-result.json，systemd failed属历史启动结果，不作为已注册实例或当前worker运行状态，不需为试验快照重启/人工apply。

结论：若下一台独立普通克隆分配RTX4070/4080系列，本轮无额外必须写入母版的改动，可请管理员保存第二版受控试验快照并启动新克隆，先只读观察自动启动，再核对test1 ready、独立身份及GPU。001的18080网段修复属于控制端配置，无需写进母版。当前母版5070仍不可初始化，不宣称第二版支持5070；若新克隆分配5070须另处理驱动模块。旧凭据清理继续Nice to have。

不要用已注册004保存母版，避免复制实例身份；应保存当前tower。记录管理员侧快照版本/时间及新VM SSH入口，保留004作为手动接入对照。本轮未保存快照、修改服务器或重启VM；[封版前只读核对](../../../results/maas-e5-preparation/template-snapshot-readiness-20261026.json)。新版无人值守首启、双克隆及模型推理仍待实测。


## 26. 第二版真实克隆首启自动接入通过（2026-10-26）

用户确认端口16482现为新建VM，虽然复用XZ-31-002名称及10.11.31.2地址，不能视为旧002或先前004。只读检查启动时间16:49:33；worker模块SHA256 bf34d3d93ae362f3de985a31a81eea5495dcd5739fa6243be251391bfcc6f994匹配封版前母版，证明保护及注册确认修正已随镜像带入。

E5于16:49:46自动开始、16:50:00完成，trigger=boot、code=APPLIED、NRestarts=0，单元enabled/active(exited)，符合oneshot成功状态。新worker ID5、随机名称e5-9c5be5b92f10f84b714a5dbe自动加入test1(cluster3)，本机原生身份文件存在。首启记录native_state=not_ready是注册时的瞬时快照；随后本机个体token的me=true查询及001管理查询均显示ready/unreachable=false，不将旧快照误读为当前不可用。

宿主机和worker容器均识别RTX4080 SUPER/580.95.05，18840/18080可达，001反向10150/10151均可连。002名称/IP复用未被误拦截。整个检查未运行prepare/apply、未改配置/补丁或重启任何VM，只有只读命令及容器内GPU查询。因此第二版独立克隆的无人值守配置获取、worker启动和原生纳管链路通过本次实测。

边界：未部署模型或执行端到端推理；未验证5070、所有GPU型号、大规模或多克隆同时在线。新ID5与先前ID4名称不同，可证明观察到独立原生记录；004目前not_ready/unreachable，不算双节点同时可用验证。旧worker记录未自动删除，后续清理需区分集群/ID。母版启动保护采用名称约定，仍非平台可信角色认证。

下一步在test1为新worker部署测试模型并通过MaaS客户入口完成请求；另安排独立克隆并行身份验证。不得通过重启001/002开展测试。[第二版首启证据](../../../results/maas-e5-preparation/second-snapshot-clone-002-20261026.json)。


## 27. 独立模型存储版本：母版尚待部署

model-storage-v1已在独立新克隆完成容器维护、身份保持、只读模型/可写缓存、身份卷排除及MaaS OCR验收；母版6025当前不通，用户会通知恢复。不要据此通知管理员直接保存含本轮改动的新快照：当前母版文件还未安装本版本。

新安装文件清单必须包含maas_bootstrap/storage.py；三模块client.py/storage.py/worker.py更新及模型目录配置须一起核对。公开交付包、SHA、具体部署/回退步骤见[MODEL-STORAGE-V1.md](../../maas/MODEL-STORAGE-V1.md)。该文档为本轮最新操作入口，覆盖前文仅放行002/保护tower-*的封版状态；旧历史原包不含本轮能力。

恢复连接后优先备份Mac，准备公开目录，不在母版启动worker，不复制新克隆的身份、applied.json或maintenance-backups.json。快照与独立克隆创建仍由管理员执行，不能重启001/002替代验证。

## 28. model-storage-v1母版实际安装完成

2026-10-26 19:18，北京时间：恢复入口6025对应tower-icDC5YcU3。Mac备份后已安装client.py/storage.py/worker.py及独立目录配置。摘要、母版保护、无实例身份/容器和enabled启动单元核对通过；未运行apply、未重启。第27节“尚待部署”为此前历史状态。现在请管理员保存新快照、创建独立克隆，提供新SSH入口后再验收新版首启及OCR。不得重启001/002代替。证据见[母版核对](../../../results/maas-e5-storage/tower-verification.json)。
