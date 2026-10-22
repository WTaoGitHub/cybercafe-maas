# 云网吧两节点环境只读检查

2026-10-22。现场提供的两台环境已直接检查。此次只读，不重启、不写远端配置、不改变身份、地址或模型，不影响今晚20:00的P4复测安排。仅是E5准备性环境取证，不代表完成E5实现或跨重启验收。

## 核心发现

| 项目 | XZ-31-001 | XZ-31-002 | 对设计的意义 |
|---|---|---|---|
| 系统 | Ubuntu 24.04.3 LTS / 6.14.0-27-generic | 相同 | 当前系统镜像/软件基线接近 |
| CPU/内存 | i7-13790F、24逻辑CPU、约31.2GiB内存 | 相同 | 不是此前仅凭描述假设的硬件清单 |
| GPU | RTX 4080 SUPER、16376MiB、驱动535.274.02 | 相同 | 当前实际型号为4080 SUPER；GPU UUID两机不同 |
| DMI主机型号 | Intel PC Farm B760M，AMI BIOS | 相同 | 有PC主板信息，但DMI本身不证明物理/虚拟部署方式 |
| 虚拟化探测 | systemd-detect-virt输出none | 相同 | 未识别常见虚拟化；不能据此断言一定裸机，仍按供应商提供环境看待 |
| 根文件系统 | /dev/swblock_sysroot，ext4；相关系统盘500G | 相同 | 非普通可见NVMe/virtio根盘路径，存在厂商块设备抽象 |
| 镜像/启动模块 | sw_readimage、sw_writebackimage、sw_admin | 相同 | 结合swblock支持镜像读取/写回机制的推断；未读取映射表或厂商私有配置 |
| 启动服务 | swnetboot_success、swcfghostname等 | 相同 | hostname服务描述为config swnetboot hostname，明确存在网络启动/命名相关组件；当前unit为inactive/dead，不代表每次启动一定执行 |
| machine-id | 与002完全相同 | 与001完全相同 | 不能用machine-id作为本批设备唯一ID；符合共享/克隆镜像特征，但不能仅此证明克隆来源 |
| DMI UUID/serial | 两机不同 | 两机不同 | 可作设备匹配辅助；跨重启、还原或重新分配稳定性仍未验证，不能代替认证 |
| boot ID | 两机不同 | 两机不同 | 可作为本次启动代次线索，不作为长期设备ID |
| 当前内网地址 | 10.11.31.1 / enp3s0 | 10.11.31.2 / enp3s0 | 当前地址不同；没有通过重启证明动态分配规则 |
| cloud-init | 已安装，status disabled，DataSourceNone | 相同 | 不能预设用cloud-init下发每机身份；厂商可能有其他启动机制 |

## 对无盘启动的判断

**具有很强的厂商网络启动、镜像盘和写回层特征，但此次检查不能确认“纯无盘”或“每次重启都会还原”。** 依据是swblock系统根盘、sw_readimage/sw_writebackimage模块，以及swnetboot和hostname相关组件。磁盘容量500G只表示呈现给Linux的逻辑容量，不能证明独占本地物理盘或承诺持久性。

根目录为ext4，不是直接显示的NFS根目录；这也不能排除厂商块设备驱动在下层进行网络取盘、缓存或写回。没有使用dmsetup table、读取厂商私有配置/进程参数或进行网络流量抓取，不能判断下层服务器地址、写回策略或实现厂商。

## GPUStack身份数据在哪里

两台worker均使用显式--worker-name，分别为XZ-31-001和XZ-31-002。宿主机/root/wtaobj/gpustack-worker绑定到容器/var/lib/gpustack，存在worker_name、worker_uuid、worker_token；两机worker UUID不同。token只记录文件存在性/权限/大小，未读取或保存内容。

001的GPUStack server数据位于/root/wtaobj/gpustack-server，绑定容器/var/lib/gpustack。这些路径以及/root/wtaobj都落在同一个/dev/swblock_sysroot根文件系统，没有看到对应路径独立的持久数据分区挂载。

因此目前能确认：**容器重建后可以复用宿主目录；不能确认云网吧还原、重启或重新分配后该目录仍存在。** 如果厂商回滚整个根镜像，worker身份凭据与001控制端数据库都有丢失/回滚的风险；这是假设条件下的影响，不是已经观察到的数据丢失。

## 对E5方案的直接约束

1. 不采用machine-id作为唯一资产身份。GPU UUID、DMI UUID、网卡标识只作辅助指纹，身份仍需可信登记/独立凭据；本次标识以SHA256摘要保存，原始UUID/序列号未导出。
2. 优先确认厂商是否提供稳定设备/座位资产ID、每机持久目录、系统镜像启动钩子。若有，复用其能力，不先创建额外节点采集平台。
3. GPUStack已有独立worker名与注册文件，可继续复用；不要将已注册的worker目录和token打入共享镜像。
4. 跨重启持久性未证实前，不能把001现有根盘当成稳定控制端存储。需为控制端明确独立稳定宿主/持久卷或供应商持久性承诺。
5. 当前服务地址不作为设备身份。源码行为和后续实验需核对地址变化后的重新注册、旧记录/实例及路由收敛；不在今晚测试前更改现有配置。

## 下一步需要确认及验证

供应商确认：系统盘/写回层在普通重启、强制断电、系统还原、释放重租四种操作下分别保留什么；是否有持久盘/目录；hostname/IP如何生成；DMI/GPU是否随实例重新分配；有无可信资产ID或启动脚本接口。

2026-10-22用户补充长期约束：001和002现在及以后均不得重启，因此取消在这两台环境进行重启/还原/重新分配验证的设想。后续使用供应商说明与只读证据；只有另行获准的独立环境才能开展启动实验并比对文件、machine-id、DMI/GPU/worker UUID及网络变化。当前两台的跨启动持久性继续标记未验证。

证据：[001环境](node-001.json)、[002环境](node-002.json)、[001启动细节](node-001-followup.json)、[002启动细节](node-002-followup.json)。这些是单时点证据，不证明长期唯一性/稳定性。检查脚本为scripts/maas/inspect_vm_environment.py及inspect_vm_boot_details.py，仅手动按需运行，不是常驻collector。
