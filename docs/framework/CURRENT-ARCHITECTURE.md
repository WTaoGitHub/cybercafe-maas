# 当前系统架构与服务暴露

> **2026-10-29网关后续修复**：已稳定复现控制端防火墙误丢弃落入受保护端口范围的出站返回包；将早期过滤迁至conntrack之后的mangle/PREROUTING，并仅允许ESTABLISHED/REPLY。26项本地回归、15项隔离内核检查、三轮副本重建15次OCR、实际入站隔离及一笔异步单计量均通过。MaaS已恢复接单（generation46）、UNKNOWN=0、监控正常；四模型副本和四候选executor保留，未重启VM/Docker/worker。原始单次503缺少源端口证据，Issue #1保持开放观察；母版/克隆网络文件未更新，E3剩余矩阵未自动续跑。[详细报告](../../results/maas-gateway-503-followup-20261029/REPORT.md)。


> 2026-10-28母版补充：SSH_PORT_REDACTED/tower已安装隔离文件并enable，未对母版应用宿主规则。新快照与独立克隆启动仍待管理员安排；当前worker7/8规则不变。[实际部署](../../results/maas-e5-template-network-20261028/REPORT.md)。

2026-10-28更新：001控制端、SSH_PORT_REDACTED/worker7及SSH_PORT_REDACTED/worker8同时在线，14个OCR副本各7个。结合前次入口监听核对及本轮双克隆证据；实线表示主要调用/控制关系，响应沿原连接返回；第二图虚线表示非客户调用路径或尚未建立的入口。当前两克隆网络边界和全局维护已验收；母版快照尚未更新。当前GPUStack为v2.2.3，E8升级未实施。

## 1. 整体组件架构

```mermaid
flowchart TB
    C["客户 / 下游 / 测试客户端"]
    T["受控访问通道<br/>当前远程测试需授权SSH转发"]
    C --> T
    subgraph CTRL["XZ-31-001 · 10.11.31.1 · 控制端，不运行GPUStack worker"]
        EDGE["Nginx客户入口<br/>私有TLS 18832 / HTTP 18831<br/>均仅回环监听"]
        API["MaaS API · 18830<br/>鉴权、模型授权、配额、维护与UNKNOWN"]
        DB[("MaaS PostgreSQL · 18835<br/>任务、租约、状态、配额与计量")]
        FILES[("MaaS artifact卷<br/>输入图片、任务结果、TTL管理")]
        EXEC["任务执行器<br/>领取异步任务、调用模型、保存结果"]
        GS["GPUStack v2.2.3 · 18080<br/>管理、调度、状态与原生推理网关"]
        GDB[("GPUStack独立数据<br/>内置PostgreSQL，与任务库分开")]
        CFG["E5配置服务 · HTTPS 18840<br/>共享码认证、签名配置与受保护凭据"]
        REG["镜像仓库 · HTTPS 18443<br/>只读拉取 / 管理端发布<br/>后端回环18500"]
        OBS["Observer / 宿主监控<br/>观测业务状态，不作为全池推理门禁"]
        CEDGE["Docker下游专用Nginx<br/>172.18.0.1:18831 · CIDR限制"]
        CEDGE --> API
        EDGE --> API
        API --> DB
        API --> FILES
        EXEC --> DB
        EXEC --> FILES
        API -->|"同步OCR"| GS
        EXEC -->|"异步OCR"| GS
        GS --> GDB
        OBS -. "读取状态" .-> DB
    end
    DOWNSTREAM["001下游容器 · 172.18.0.0/16"] --> CEDGE
    T --> EDGE
    TEMPLATE["母版快照<br/>E5引导程序 + 固定worker镜像<br/>无实例身份，不预装OCR"]
    subgraph NODE["当前新克隆 · worker7 · test1 · 10.11.31.2"]
        BOOT["boot / manual引导脚本<br/>验证TLS、签名、兼容性与环境"]
        W["GPUStack worker + Docker<br/>原生注册、状态上报、镜像拉取与部署<br/>管理/代理10150，指标10151"]
        ID[("私有状态与认证<br/>原生身份、SDK只读仓库认证<br/>不共享给OCR容器")]
        OCR["7个OCR推理副本 · RTX 4080 SUPER<br/>引擎和权重在镜像内<br/>动态端口，当前40013"]
        MODEL[("模型目录 / 缓存<br/>models只读，model-cache可写<br/>当前models仅公开部署标记")]
        BOOT -->|"启动"| W
        BOOT --> ID
        W --> ID
        W -->|"启动 / 代理调用"| OCR
        OCR --> MODEL
    end
    TEMPLATE -->|"管理员保存快照并创建克隆"| BOOT
    subgraph NODE8["新增XZ-31-003 · worker8 · test1 · 10.11.31.3 · SSHSSH_PORT_REDACTED"]
        BOOT8["boot自动配置 · APPLIED"]
        W8["GPUStack worker8 · Ready"]
        OCR8["7个OCR副本 · RTX4080SUPER<br/>models RO / cache RW，身份与认证不共享"]
        BOOT8 --> W8
        W8 --> OCR8
    end
    TEMPLATE -->|"管理员同快照创建额外克隆"| BOOT8
    BOOT8 -->|"HTTPS统一配置"| CFG
    W8 -->|"自动注册/状态"| GS
    GS -->|"原生调度/推理"| W8
    W8 -->|"认证拉取固定digest"| REG

    BOOT -->|"HTTPS读取配置"| CFG
    W -->|"注册、心跳与状态"| GS
    GS -->|"原生部署控制与推理路由"| W
    W -->|"HTTPS认证拉取固定digest"| REG
```

当前客户逻辑模型仍为ocr.default，业务revision为ocr-registry-r1；原生model18/route17目标14，instance103–109在worker7、110–116在worker8，各7个且全部running。母版只交付镜像，不是运行时配置源，也不需要保持在线。MaaS输入/结果目前在001的artifact卷，不是NFS或仅保留在worker。自建collector已退役，GPUStack自身资源采集保留。

## 2. 重点：服务暴露与访问边界

```mermaid
flowchart TB
    REMOTE["远程下游消费者"]
    TEST["受控测试客户端<br/>需要信任MaaS自签证书"]
    SSH["授权SSH本地转发<br/>192.0.2.10:SSH_PORT_REDACTED → 001<br/>需先建立；不是公网业务API"]
    FUTURE["客户局域网 / 公网业务入口<br/>尚未部署"]
    REMOTE -. "直接业务接入待安排" .-> FUTURE
    TEST --> SSH
    subgraph LOOP["001回环区 · 仅本机 / 经授权隧道访问"]
        TLS["HTTPS 127.0.0.1:18832<br/>MaaS独立自签证书"]
        HTTP["HTTP 127.0.0.1:18831<br/>替代测试入口"]
        APP["MaaS API 127.0.0.1:18830<br/>Bearer鉴权 + 共享准入与计量"]
        PG["任务数据库 127.0.0.1:18835<br/>不是客户接口"]
        STORE["Registry后端 127.0.0.1:18500<br/>无独立客户入口"]
        TLS --> APP
        HTTP --> APP
        APP --> PG
    end
    DC["001下游Docker容器 · 172.18.0.0/16"] --> CE["桥网关HTTP 172.18.0.1:18831<br/>精确raw例外 + CIDR ACL + 业务白名单"]
    CE --> APP
    SSH --> TLS
    FUTURE -. "后续单独设计和部署" .-> TLS
    subgraph LAN["001局域网入口 · 不等于MaaS客户入口"]
        CONFIG["HTTPS 10.11.31.1:18840<br/>配置服务：共享码 + 签名验证<br/>独立TLS信任，来源CIDR限制"]
        REGISTRY["HTTPS 10.11.31.1:18443<br/>镜像仓库：只读拉取认证<br/>独立TLS信任，来源CIDR限制"]
        NATIVE["HTTP 10.11.31.1:18080<br/>GPUStack注册 / 管理 / 原生推理<br/>同时有127.0.0.1:18080映射<br/>001原生访问规则限制"]
    end
    REGISTRY --> STORE
    CLIENTBOOT["克隆引导程序"] --> CONFIG
    WORKER["克隆worker / Docker"] --> REGISTRY
    WORKER -->|"原生凭据：注册、状态"| NATIVE
    APP -->|"同步；异步由执行器调用"| NATIVE
    subgraph WP["worker7/8内部服务 · 不是MaaS客户入口"]
        PROXY["0.0.0.0:10150 · worker代理"]
        METRIC["0.0.0.0:10151 · 指标"]
        ENGINE["当前OCR实例<br/>动态端口40000–40063"]
        ACL["MAAS-CLONE raw/INPUT/DOCKER-USER<br/>仅001可访问受保护TCP/UDP端口<br/>保留SSH、loopback及出站"]
        PROXY --> ENGINE
    end
    NATIVE -->|"内部推理"| PROXY
    PEER["其他局域网节点"] -. "受保护端口阻断；两克隆互访已实测" .-> ACL
```

TLS18832与HTTP18831是两个平行Nginx入口，都直接转发18830，并非18832再转发18831。克隆访问配置服务和仓库的信任，与消费者访问MaaS的信任是三条不同通信链。原生GPUStack注册和推理入口当前仍是HTTP18080，不能画成全链路HTTPS。

## 3. 地址、用途与暴露状态

| 位置/端口 | 用途 | 当前实际边界 |
|---|---|---|
| 001 127.0.0.1:18832（同时::1） | MaaS客户HTTPS接口 | 自签证书，仅回环；远程测试需授权转发，尚未新增LAN/公网客户监听 |
| 001 127.0.0.1:18831（同时::1） | MaaS客户HTTP测试入口 | 仅回环，独立于TLS入口 |
| 001 172.18.0.1:18831 | 同机下游容器HTTP入口 | 仅指定Docker桥/172.18.0.0/16允许，仍须MaaS Bearer；原API内部端口及原生GPUStack未向该网段放开 |
| 001 127.0.0.1:18830 | MaaS API内部监听 | 回环，正常客户路径经过Nginx白名单 |
| 001 127.0.0.1:18835 | MaaS PostgreSQL | 回环，客户不直接访问 |
| 001 10.11.31.1:18840 | E5配置HTTPS | LAN来源规则、共享码；返回签名配置和受保护凭据，不是OCR接口 |
| 001 10.11.31.1:18443 → 127.0.0.1:18500 | Registry HTTPS → HTTP存储 | 前端LAN限定、reader/publisher分权；后端仅回环，不是客户图片下载接口 |
| 001 10.11.31.1及127.0.0.1:18080 | GPUStack原生HTTP入口 | 内部注册/管理/推理及受控运维；客户直用会绕过MaaS业务层，不作为消费者配置入口 |
| 当前worker7/8 0.0.0.0:10150、10151 | worker代理与指标 | 仅001从指定LAN接口放行，其他来源按IPv4/IPv6规则阻断；SSH不变 |
| 当前worker7/8 40000–40063 | 当前OCR动态实例端口 | 仅001可访问；两克隆互访实际模型端口均受阻，非客户API |
| 公网192.0.2.10:SSH_PORT_REDACTED / :SSH_PORT_REDACTED / :SSH_PORT_REDACTED | 已知SSH映射到001 / worker7 / worker8 | 运维入口，不能将SSH映射等同于已开放客户API；供应商其他映射未做完整核验 |

2026-10-28规则已应用到当前两克隆：raw pre-DNAT、INPUT及存在的DOCKER-USER覆盖，001管理/指标/模型健康可达；克隆间直连实测阻断。全局维护停止/恢复后仍有效。未全量扫描供应商公网映射或第三方LAN；IPv6规则安装和隔离内核验证通过，但未做IPv6真实模型调用。当前unit已enable及bootstrap依赖已配置，未做VM重启/新快照跨启动验证；未来克隆仍需母版overlay发布。

2026-10-27兼容门面上线后，当前客户路径包括/ocr、/v1/ocr、/v1/chat/completions，以及/maas/v1/artifacts、tasks、查询/取消和capabilities。POST /ocr兼容门面已上线，文生图仍未提供；网络监听和worker ACL状态没有因兼容适配而改变。

2026-10-27同机下游接入更新：新增独立maas-container-http入口，不替换原HTTP/TLS进程；只在172.18.0.1监听18831，持久raw规则仅放指定桥接来源到该目的地址和端口。已从下游后端network namespace验证401/200及路径限制，其他桥/内部API/原生管理仍阻断。下游业务渠道须自行更新endpoint，未测试公网或VM重启。见[报告](../../results/maas-container-http/REPORT.md)。

2026-10-28额外克隆正常扩容通过：新worker8无需手动安装/apply/pull/load即补齐7个实例；私有MaaS网关真实覆盖两GPU，异步/结果/单事件通过。该扩容阶段仅取证；随后已部署两克隆MAAS-CLONE链并通过全局维护，见下方最新记录。参见[本轮报告](../../results/maas-e5-dual-clone-20261028/REPORT.md)。


2026-10-28维护与网络验收：schema-v2绑定worker7/8、model18、route17；104.875秒全局窗口验证实际GPU停止、14副本7/7恢复、排队任务单次执行与唯一计量。四个MaaS容器ID和两克隆boot ID/worker容器ID保留，监控无告警和UNKNOWN。详见[验收报告](../../results/maas-topology-20261028/REPORT.md)和[当前运维手册](P4-MAINTENANCE.md)。E1未上线，下一步服务认证契约。
