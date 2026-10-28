# 当前拓扑维护与克隆网络边界验收

2026-10-28，真实常驻环境。**通过：当前两克隆网络边界、身份绑定的全局维护和14副本恢复。** 这不是E1逐worker持续服务，也不是本版本8小时耐久或规模验收。

## 当前对象

001为控制端，保留GPUStack Server v2.2.3、MaaS、数据库、存储、配置服务、Registry及下游入口；不承担worker。test1/cluster3，model18（maas-ocr-registry-r1）、route17，worker7/XZ-31-002/10.11.31.2和worker8/XZ-31-003/10.11.31.3，各7个实例，共14 running。未改变调度显存claim、应用并发或单执行器。

旧binding指向worker2、models1/9，已备份到Mac私有目录，改为schema-v2全局绑定。操作前检查实际VM boot ID、worker容器完整ID、镜像ID、原生worker UUID哈希及GPUStack ID/name/IP/labels/cluster；模型配置、期望副本、路由和维护generation漂移时拒绝覆盖。新维护SSH key仅存001宿主机私有目录及Mac备份；克隆authorized_keys仅追加限制001来源的forced-command入口，不允许任意shell/端口转发，不挂入API或执行器。

## 实际演练

会话`20261028T060538Z-9d63bec846`，约北京时间14:06开始，全局维护窗口**104.875秒**，31.435秒确认两节点真正停止。真实日志见[acceptance.json](acceptance.json)、[journal.json](journal.json)。

1. 常驻同步OCR和异步任务预检各返回46行。
2. 全局pause、排空，无UNKNOWN；两worker进入maintenance，模型18缩至0。
3. 原生实例消失后继续核对实际Docker容器和GPU计算进程，确认均清空，才停止两个原worker容器。两节点stop证据均为true，保持停止至少5秒。
4. 停止期间控制端health正常；同步返回503/MAINTENANCE_PAUSED；新异步任务QUEUED；旧结果仍可下载。
5. 启动原worker容器，恢复原maintenance及模型14副本，重新收敛为7/7；接单仍保持暂停。
6. 真实注册OCR探针46行通过才resume；排队任务SUCCEEDED、attempts=1，SQL核对该task成功计量事件恰好1条；恢复探针未重复计量。
7. 四个MaaS Compose容器ID不变；两克隆boot ID、原worker容器ID和原生身份不变；维护journal/config为600。控制端watchdog停止，两个节点会话disarmed。最终monitor ok、alerts=[]、UNKNOWN=0。

没有VM重启、关机/回收/还原、Docker daemon重启，没有删除命名卷或重新初始化任务库。执行的是显式授权的业务容器停止/恢复；safe_to_poweroff始终false。原生模型实例及推理容器随0→14部署重新创建是预期行为。

## 网络边界

两克隆安装独立MAAS-CLONE raw/INPUT/DOCKER-USER链，IPv4/IPv6均应用；只允许001从enp3s0访问受保护TCP/UDP端口。pre-DNAT拦截发布端口，Docker转发链及当前默认桥网段补充保护；保留loopback、SSH22和出站，不清空系统全局规则。

受保护端口：8820、10150–10151、10161、11435、18820、18830、40000–40063、41000–41999。当前GPUStack模型动态端口仍在批准范围，未来模型/端口/桥网络变化需要重新审查。

| 路径 | 实测结果 |
|---|---|
| 001→两个worker的10150、10151及实际模型端口 | TCP可达，模型/health 200 |
| worker7→worker8的10150、10151及实际模型端口 | 全部连接失败 |
| worker8→worker7的相同服务类别 | 全部连接失败 |
| Mac→两克隆现有SSH映射 | 仍可登录，规则未修改SSH |
| 全局恢复后的同步探针、异步及结果下载 | 成功 |

最终实际端口/实例见[final-topology.json](final-topology.json)；双向拒绝与规则状态见worker7/8-final-network.json。两节点均guarded-apply→验证→confirm，180秒网络回退timer已解除。脚本在003隔离network namespace中完成iptables/ip6tables真实内核加载和重复apply检查，无重复INPUT hook；没有改宿主Docker网络。

## 持久化与验证边界

当前两克隆已enable独立boundary unit，并在worker bootstrap前挂载依赖和ExecStartPre；重复应用已验证。**未做VM重启，未验证跨启动持久性。母版尚未更新，本轮规则不会自动进入旧快照。** 后续母版通用overlay及独立新克隆验收见[handoff](../../deploy/bootstrap/clone/network/README.md)；不复制当前维护身份/私钥到镜像。

IPv6已核对规则/内核可加载，未做真实IPv6模型调用。公网供应商映射/任意第三方LAN节点未全量扫描；本轮只以当前两克隆为非001来源实测，不声称完成全网安全审计。内部隔离依赖可信LAN来源，不是抵抗root或源地址伪造的身份认证替代。001现有回环HTTP/TLS和Docker下游入口保持；未新增公网入口。

本地MaaS及bootstrap测试结果另记validation.json；数据库专项因没有本地测试DB跳过，不冒充全部E2E通过。真实维护使用原正式任务DB及模型。无collector，无E1/E3/E8部署或新长时测试。下一顺序为上下游职责与服务认证契约，母版边界补充发布需管理员快照/独立克隆配合。

## 后续补测（2026-10-28）

用户授权复用001专用测试数据库后，原本56项数据库条件跳过已全部补测通过，包含真实测试DB中断/恢复；本地当时的跳过计数仍保留。见[补测报告](../maas-db-regression-20261028/REPORT.md)。补测后只读发现model18期望/实际已为4副本，本轮未改GPUStack配置；原14副本维护批准值不能直接复用，应确认后重新audit/bind。
