# 当前系统架构与服务暴露

2026-10-27。按最新部署、001与16487克隆的监听地址及仓库源码核对。实线表示主要调用/控制关系，响应沿原连接返回；第二图虚线表示尚未建立的入口或待核验的边界。当前GPUStack为v2.2.3，E8升级未实施。

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
    T --> EDGE
    TEMPLATE["母版快照<br/>E5引导程序 + 固定worker镜像<br/>无实例身份，不预装OCR"]
    subgraph NODE["当前新克隆 · worker7 · test1 · 10.11.31.2"]
        BOOT["boot / manual引导脚本<br/>验证TLS、签名、兼容性与环境"]
        W["GPUStack worker + Docker<br/>原生注册、状态上报、镜像拉取与部署<br/>管理/代理10150，指标10151"]
        ID[("私有状态与认证<br/>原生身份、SDK只读仓库认证<br/>不共享给OCR容器")]
        OCR["OCR推理容器 · RTX 4080 SUPER<br/>引擎和权重在镜像内<br/>动态端口，当前40013"]
        MODEL[("模型目录 / 缓存<br/>models只读，model-cache可写<br/>当前models仅公开部署标记")]
        BOOT -->|"启动"| W
        BOOT --> ID
        W --> ID
        W -->|"启动 / 代理调用"| OCR
        OCR --> MODEL
    end
    TEMPLATE -->|"管理员保存快照并创建克隆"| BOOT
    BOOT -->|"HTTPS读取配置"| CFG
    W -->|"注册、心跳与状态"| GS
    GS -->|"原生部署控制与推理路由"| W
    W -->|"HTTPS认证拉取固定digest"| REG
```

当前客户逻辑模型仍为ocr.default，业务revision为ocr-registry-r1；原生model18/route17/instance103落在worker7。母版只交付镜像，不是运行时配置源，也不需要保持在线。MaaS输入/结果目前在001的artifact卷，不是NFS或仅保留在worker。自建collector已退役，GPUStack自身资源采集保留。

## 2. 重点：服务暴露与访问边界

```mermaid
flowchart TB
    REMOTE["远程下游消费者"]
    TEST["受控测试客户端<br/>需要信任MaaS自签证书"]
    SSH["授权SSH本地转发<br/>111.4.255.126:2023 → 001<br/>需先建立；不是公网业务API"]
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
    subgraph WP["worker7内部服务 · 不是MaaS客户入口"]
        PROXY["0.0.0.0:10150 · worker代理"]
        METRIC["0.0.0.0:10151 · 指标"]
        ENGINE["0.0.0.0:40013 · 当前OCR<br/>动态范围40000–40063"]
        ACL["未发现项目MAAS防火墙链<br/>克隆侧网络隔离待专项核验<br/>本轮未测试公网可达性"]
        PROXY --> ENGINE
    end
    NATIVE -->|"内部推理"| PROXY
    PEER["其他局域网节点"] -. "不能仅因称为内部端口就认定已阻断" .-> ACL
```

TLS18832与HTTP18831是两个平行Nginx入口，都直接转发18830，并非18832再转发18831。克隆访问配置服务和仓库的信任，与消费者访问MaaS的信任是三条不同通信链。原生GPUStack注册和推理入口当前仍是HTTP18080，不能画成全链路HTTPS。

## 3. 地址、用途与暴露状态

| 位置/端口 | 用途 | 当前实际边界 |
|---|---|---|
| 001 127.0.0.1:18832（同时::1） | MaaS客户HTTPS接口 | 自签证书，仅回环；远程测试需授权转发，尚未新增LAN/公网客户监听 |
| 001 127.0.0.1:18831（同时::1） | MaaS客户HTTP测试入口 | 仅回环，独立于TLS入口 |
| 001 127.0.0.1:18830 | MaaS API内部监听 | 回环，正常客户路径经过Nginx白名单 |
| 001 127.0.0.1:18835 | MaaS PostgreSQL | 回环，客户不直接访问 |
| 001 10.11.31.1:18840 | E5配置HTTPS | LAN来源规则、共享码；返回签名配置和受保护凭据，不是OCR接口 |
| 001 10.11.31.1:18443 → 127.0.0.1:18500 | Registry HTTPS → HTTP存储 | 前端LAN限定、reader/publisher分权；后端仅回环，不是客户图片下载接口 |
| 001 10.11.31.1及127.0.0.1:18080 | GPUStack原生HTTP入口 | 内部注册/管理/推理及受控运维；客户直用会绕过MaaS业务层，不作为消费者配置入口 |
| 新克隆 0.0.0.0:10150、10151 | worker代理与指标 | 全IPv4接口监听；没有检测到项目MAAS链，不能写成已强制阻止其他LAN节点直连 |
| 新克隆 0.0.0.0:40013 | 当前OCR推理实例 | 动态端口；不是稳定客户API，网络可达性与ACL需专项确认 |
| 公网111.4.255.126:2023 / :16487 | 已知SSH映射到001 / 当前克隆 | 运维入口，不能将SSH映射等同于已开放客户API；供应商其他映射未做完整核验 |

001和新克隆分别读取监听信息；新克隆filter/raw表中未发现MAAS链，INPUT策略为ACCEPT。本轮不是完整网络安全审计，未测试第三方LAN/公网直连，也没有修改ACL。图中“非客户入口”描述接口职责，不声称底层网络一定不可达。需要后续核验并落地克隆侧管理/推理端口ACL，不能把001已有规则视为会自动复制到所有克隆。

当前已对客户提供的路径是/v1/chat/completions，以及/maas/v1/artifacts、tasks、查询/取消和capabilities。消费者契约中的POST /ocr兼容门面及文生图入口尚未实现；网络/证书接通不会自动补齐协议差异。
