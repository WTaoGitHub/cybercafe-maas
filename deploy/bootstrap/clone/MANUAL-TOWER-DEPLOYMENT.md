# tower 母版手动部署操作手册

版本：2026-10-26；适用于当前 E5 试点、Ubuntu 24.04 x86_64。本文是后续人工执行说明，**编写手册不表示已授权或已经部署到 tower**。此前“先准备、不要部署到tower”的要求继续有效。

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
- 管理员确认旧CyberCafe启动逻辑的替换范围、快照方式、克隆首次machine-id/SSH主机密钥初始化方式。

**停止推进条件：** 已有E5安装/实例身份、未知Docker容器、依赖检查失败、旧启动逻辑尚未审查、包校验不一致、真实码或证书来源无法确认。先保存现状并处理原因，不覆盖、不擦除、不关闭TLS校验。

## 2. 上传并验证公开交付包

以下命令在 **Mac终端**执行，待实际部署窗口获准后再操作：

```sh
shasum -a 256 /Users/4paradigm/dev/cybercafe-maas/dist/e5/e5-worker-template-tower-e5-pilot-v1.tar.gz

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

记录该目录，回退时使用实际备份路径，不依赖跨SSH会话保留变量。

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

保留旧脚本和备份便于回退。不禁用`swcfghostname`、`swnetboot_success`、SSH或供应商网络/磁盘服务。若provision含仍需保留的工作，先拆分职责，不直接照抄上述停用命令。停用旧服务后再次检查Docker容器，未知容器不要自动删除。

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

预期enabled、当前inactive。**不要使用`enable --now`、`start`、`restart`或直接`apply`。** 当前代码会拒绝在tower/001/002上启动worker，这个保护不应删除或绕过。启用只设置以后开机入口，母版当前不启动；母版若后来重启，该保护仍会拒绝其注册。

unit成功后不持续监护worker容器；容器使用restart=no，后续退出不会由该oneshot单元自动恢复。启动阶段失败有界重试，10分钟最多5次；不要把此功能理解为全天候自动修复。

## 11. 封版前检查与身份初始化交接

以下全部满足后，才通知管理员制作快照：

- 客户端、配置、共享码和公开信任材料到位；`/etc/maas`为root:root/700，shared-code与bootstrap.json为root:root/600；固定镜像摘要匹配。
- 临时prepare通过，临时私有返回数据已清理；母版无worker容器、无`/var/lib/maas-bootstrap`实例目录，无标准原生路径UUID/token。
- 旧业务启动服务已按审查结论处理；供应商网络/命名及SSH入口保留；E5单元enabled但当前inactive。
- `hostname`仍是tower，克隆首次启动会获得不同hostname/IP；E5不自行更改它们。克隆若继承tower名称或001/002地址，会被本机保护拒绝。
- 管理员已确定machine-id和SSH主机密钥的离线清理/首启重建方案，并有确保SSH可登录的执行顺序；保留管理员authorized_keys。
- 管理员确认平台失败单元不会破坏快照/克隆；记录当前依赖、镜像摘要、E5源码版本及处理结果。

**本文不提供在运行中tower删除machine-id或SSH私钥的命令。** 当前E5单元也不负责生成这些系统身份；仅删除密钥而没有首启重建流程可能使新克隆SSH不可用。若平台只支持在线热快照且不提供初始化，应先补齐并验证专用首启方案，不能把“E5文件已安装”写成“可正式封版”。

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
