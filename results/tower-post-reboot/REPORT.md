# tower 重启后检查与 E5 环境证据

2026-10-23，北京时间约10:41–10:49采集。用户补充：tower确实在昨晚由管理员重启。该时间按用户确认记录；系统墙钟/日志/uptime不一致，不据机器时间另行推断管理员重启时刻。本轮没有重启任何机器或服务，没有安装软件、写远端配置、执行供应商启动脚本或接入GPUStack。只查询状态，并执行此前已经编译好的微型CUDA探针（分配4字节、单线程加一，结果42，两次成功）。证据保存在本地。

## 已确认的安装与运行结果

| 检查 | 结果 | 证据边界 |
|---|---|---|
| 加载驱动 | 580.95.05，nvidia-smi正常 | 已消除此前535内核/580 NVML不匹配 |
| CUDA Toolkit | /usr/local/cuda→cuda-13.0，nvcc13.0.88，元包13.0.3-1 | 不以nvidia-smi的CUDA字段代替Toolkit检查 |
| 真实CUDA计算 | 现有cuda13_probe返回PASS，1个设备，kernel result=42 | 已验证微型CUDA内核，不代表OCR或长负载 |
| NVIDIA Container Toolkit | 四个包1.20.1-1，nvidia-container-cli info成功 | 驱动与容器工具可识别GPU |
| Docker运行时 | 已加载nvidia，默认runc | 配置已生效；无本地镜像/容器，未拉镜像或执行GPU容器，端到端容器验收仍待做 |
| GPU | RTX4070，12282MiB，PCI 01:00.0，UUID见evidence.json | tower并非001/002的4080SUPER |
| 包完整性 | dpkg --audit无输出 | 本次未执行升级/修复 |

探针源码SHA256与本地scripts/tower/cuda13_probe.cu一致。对比重启前记录，boot ID摘要变化；安装的驱动、Toolkit、Container Toolkit和探针仍存在。这证明本次当前环境能读取这些安装结果，不证明供应商任意快照/所有克隆的持久性。

## 问题一：PCI直通还是vGPU？UUID稳定与并行性？

当前观测：PCI显示真实型号AD104 [10de:2786]，nvidia驱动绑定；NVML Virtualization Mode=None，Host VGPU Mode=N/A；无CPU hypervisor标志，systemd-detect-virt输出none；根盘为swblock_sysroot，存在sw_readimage/sw_writebackimage等供应商组件。

**结论：没有观测到标准NVIDIA vGPU模式，但无法从这些guest数据证明PCI直通，更不能排除整机网络启动或隐藏虚拟化。** 产品中的“VM”称呼不足以决定底层架构。NVIDIA将NONE、PASSTHROUGH与VGPU区分为不同枚举，None不能等同PCI直通。[官方枚举](https://nvidia.github.io/cuda-python/cuda-bindings/13.3.2/module/generated/cuda.bindings.nvml.GpuVirtualizationMode.html)

当前完整UUID为GPU-690b4fd1-7481-72ed-b4aa-bb3c32cadba1，其SHA256前12位为e9de91cfd30f。前一boot的cybercafe-provision日志记录gpu来源指纹a56a6995e61d，当前boot为e9de91cfd30f；当前日志还记录marker从前者变成后者。两轮均声称gpu来源，不能简单归因为降级MAC。但旧原始GPU UUID、旧脚本版本及同物理设备的独立证明不足，日志也可能随镜像继承，**不能断言同一物理GPU的UUID发生变化**。这恰好说明不能把相同hostname、镜像或入口端口当作同一GPU/VM。

跨VM重绑定稳定性、同UUID能否并行出现均未验证。一次GPU查询成功不能证明独占。E5保持完整GPU UUID资源关联+临时VM会话；碰到并发同UUID拒绝覆盖，不通过缩短租约自动抢占。若将来证实UUID会重建，则新增供应商可信物理GPU资产映射；若无可靠资产映射，只能登记“本次资源观察”，不承诺跨实例稳定GPU身份。

## 问题二：启动注入和钩子能否自己确认？

**已确认可用的本地执行机制：** cybercafe-provision.service enabled且本boot成功退出，Agent enabled且运行；swcfghostname.service与swnetboot_success.service本boot也成功执行。systemd启动引导已经有运行证据，不必等待供应商提供cloud-init才能实现本地引导。但本轮未验证我们新的E5单元，更未验证可信凭据注入。

**新发现的供应商通道：** /proc/cmdline包含swbootid、swsnapid、swsoluid、swdiskid、sw_image_version、sw_rootdiskmode；两个boot日志中swbootid摘要不同，snapshot/solution/disk/image-version摘要相同。/run/swboot有CLIENT_INFO_ACK、CLIENT_DESC_ACK、CLIENT_DISCOVER_ACK、APPDEV_DISCOVER_ACK及bootsrv_ip。swcfghostname脚本引用/proc/cmdline、/run/swboot/CLIENT_INFO_ACK和/etc/hostname。

这证明供应商已有启动参数及运行期配置传递渠道；**不证明字段含义、唯一性、保密性、真实性或可注入任意用户配置。** 原始参数值不入报告，ACK只看元数据、不读取凭据；ACK为0600、162字节，当前未解析成通用文本字段。不把swbootid直接当token或GPU资产号，可作为启动线索待验证。root可访问的guest自报字段不能单独提供可信认证。

cloud-init仍disabled，DataSourceNone，seed为空；未发现qemu_fw_cfg或virtio-ports目录。不能默认user-data生效，也不能据未发现这些目录断言供应商不具备注入能力。cloud-init禁用marker会阻止其运行，参见[官方状态说明](https://docs.cloud-init.io/en/24.1/howto/status.html)。

网络：tower为10.11.31.3，网关10.11.31.254。到001和002的22端口TCP成功；到001:18080及18832超时。18832原本是回环私有TLS入口，失败不意外；18080不可达则当前不能以该LAN URL自动加入Server。超时原因可能为既有防火墙/监听/路由，未专项归因，不能为接入而自动放宽全网段。Docker Hub域名能返回解析结果，但443连接失败；该结果不证明所有出网不可用，镜像分发需明确受控内网镜像源或离线包。

## 问题三：谁初始化身份，什么时候验证驱动和容器？

当前machine-id摘要与母版旧记录/001/002相同，三种SSH主机公钥指纹也与原记录一致。可确认这次没有观察到这些身份重新生成；不能假设每次克隆会自动清理。供应商命名脚本只发现hostname处理线索，不能将其视为完整身份初始化责任方。

现有cybercafe引导/Agent在此前检查时未出现，现在存在并开机执行，说明当前母版已包含另一套控制程序；本轮不停止、删除或更新它。E5封版前需列出并确认所有启动所有者，防止两套程序同时改Docker/安装模型。machine-id及SSH host key初始化必须独立定义责任和顺序，并保留管理员授权入口；不能现在删除母版密钥。

**驱动与宿主CUDA已可在当前母版验证，并已成功。** 当前没有容器镜像，故未拉取镜像或创建容器；下一项可用固定摘要的CUDA测试镜像做一次短暂容器探针，独立验证GPU设备、驱动库注入与容器内CUDA。不能将nvidia-container-cli成功当成容器推理成功。真实克隆/同卡重新分配验证仍需独立获准克隆环境，001/002永久禁止重启。

## 时钟与供应商服务异常

采样时UTC/北京时间与采集端大致一致，NTP报告同步；但本boot早期日志约18:10，采样当前约10:43，日志首条晚于末条约8小时。系统/proc/uptime仅约32分钟，与用户确认昨晚重启也不一致。原因未定，不据此推断额外重启、实例替换或供应商操作。boot ID和本次观察作为证据，管理员确认时间独立保留；历史journal也可能随快照继承。

E5新增：使用服务端时间验证证明/租约，客户端校时前不要靠本地墙钟决定凭据有效期；本地等待用单调时钟。TLS仍正常校验证书，时钟错误时停止登记并报告，不通过跳过校验解决。

供应商swappdev.service本boot失败，ExecMainStatus=1；本轮未读取潜在敏感日志、未修复，未知是否影响供应商磁盘或设备管理。这不应被写成GPU运行失败，但封版和克隆实验前应由管理员确认其用途。

## 不依赖口头保证的最小实验与预案

| 假设/意外 | 我们如何验证 | 未通过时如何处理 |
|---|---|---|
| 同物理GPU跨实例UUID稳定 | 独立克隆A/B顺序绑定同一资产，外部资产/端口/供应商调度记录作锚点，保存原始UUID及版本、boot参数摘要 | 不合并旧资源；改用可信资产映射或仅会话级资源，人工确认 |
| 同UUID只出现一次 | 两个独立实例同步取样+可信调度/硬件证据；另在本地模拟重复UUID验证DB冲突 | 单卡活动会话唯一约束；隔离新冲突，不覆盖旧会话；模拟只能证明应用行为，不能证明平台独占 |
| swbootid是启动代次 | 多个独立克隆对比同次进程恢复/不同次启动，关联外部启动记录；仅采集hash | 不将其作认证凭据；采用每次生成会话ID+可信外部证明 |
| 能注入任意凭据 | 独立测试镜像/克隆传递一次性随机非秘密标记验证交付，随后验证签名、绑定、过期、重复消费 | 本地systemd引导仍可用；可信供应商证明不具备时禁止无人值守生产接入，只保留人工批准试点 |
| 身份清理可靠 | 两个干净克隆对比machine-id、SSH公钥、worker状态目录和会话；不碰现有管理密钥 | 构建时声明首次启动初始化责任，检测镜像污染后拒绝登记，不“自愈”继承旧秘密 |
| 控制面能互达 | 从新worker到登记/原生Server做TLS及API验证，再验证Server到新worker服务端口 | 单独设计允许路径和最小规则，未通不启动worker；不只验证ping/SSH |
| 网络或镜像源不稳定 | 固定摘要镜像离线/内网分发，注册超时与断网恢复模拟 | 指数退避+jitter，持久注册操作ID，对账后补偿；不盲目重复建worker |
| 校时/凭据/撤销异常 | 模拟时间偏移、过期证明与原生撤销失败 | 服务端时间、明确失败状态，不跳TLS验证、不自动解除UNKNOWN |

本轮完成取证与宿主GPU验证，未实施登记服务、启动注入、容器测试或克隆实验。完整证据见 evidence.json、followup.json、boot-clues.json、injection-fields.json及network.json；没有采集设备密钥、批次码或供应商ACK内容。
