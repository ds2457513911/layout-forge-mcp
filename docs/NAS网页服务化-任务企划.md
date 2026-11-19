# layout-forge 网页服务化 · 任务企划

> 状态：**待评估**（本文档只做方案设计，未改动任何代码）
> 编写依据：`PMC\BOM_validation\SERVER_PROMPT.md`（NAS 环境真源）、`ARCHITECTURE_PROMPT.md`（四层架构与踩坑真源）、本仓 `README.md` / `LAYERS.md` / `docs/SOP-工程维护手册.md`
> 目标：把「自动创建封装 + 封装检测」做成同事能用的网页服务，部署到 Synology DS1621+

> ★ **历史快照（2026-11-11 写作时）**：文中「36 个工具」及各段的分组数量都是当时的口径，不是现状。
> 现状工具清单只认 `docs/tools-catalog.md`（生成物）；本文的数字按「历史快照」保留，引用时请标年份。

---

## 一、一句话结论

**这件事能做，但不能"把 MCP 塞进容器"。**

本工具链的核心能力被两个 Windows-only 依赖锁死：读 `.dra` 靠 Cadence 自带的 `extracta.exe`，建封装靠 SkillBridge 连一个**正在运行的 Allegro 交互会话**。而 NAS 是 Linux、没有 Cadence、也没有人坐在前面点 Allegro。

所以真实形态必然是 **"NAS 做大脑、Windows 做工位、人做闸门"** 的三段式：

```
       同事浏览器                    NAS (Linux)                Windows 工位
  ┌──────────────────┐      ┌────────────────────┐      ┌──────────────────┐
  │  上传规格书 PDF   │─────▶│  Web 后端 + Agent   │─────▶│  执行器 (轮询)    │
  │  看进度 / 报告    │◀─────│  PDF 解析/几何/规则  │◀─────│  extracta/建库    │
  └──────────────────┘ SSE  └────────────────────┘ 回调  └──────────────────┘
                                                            │ 需人工前置
                                                            ▼
                                                      Allegro (有人开着)
```

能全自动的部分比想象中多：PDF 读图、参数抽取、几何求解、规则判定、规格库、报告生成，约占 36 个工具的一半（约 18 个），全部是纯 Python、可上 Linux。必须依赖工位的只有"落到 Cadence 里的那一步"。

---

## 二、可行性分级（先分清哪些能搬、哪些搬不动）

按 Linux 可移植性把现有 36 个工具切三段：

| 段位 | 能力 | 代表工具 | Linux 可行性 | 归属 |
|---|---|---|---|---|
| **A. 纯 Python** | PDF 定位/渲染/测量、规格库、几何求解、规则检查、报告生成 | `pdf_probe`、`locate_land_pattern_page`、`render_pdf_region`、`measure_region`、`solve_footprint_geometry`、`validate_land_pattern_json`、`check_footprint_by_rules`（判定层）、`save_spec`/`load_spec`/`find_spec`/`list_specs`、`save_case`/`find_case`/`list_cases` | ✅ **完全可迁移** | NAS 容器 |
| **B. 读 Windows 文件** | 从 `.dra` 提取数据、跑数值规则 | `read_full_footprint`、`check_footprint_by_rules`（提取层） | ⚠️ **需工位代执行** | Windows 执行器 |
| **C. 写 Allegro** | 建 padstack / 放 pin / 画外形 / 存盘 / 导 psm | `create_padstack`、`add_footprint_pin`、`add_footprint_outline`、`add_footprint_text`、`new_footprint_drawing`、`save_footprint_drawing`、`export_psm`、`build_footprint_from_params` | ❌ **只能在工位跑** | Windows 执行器 |

### 关键证据（不是猜测）

- `services/cdsroot_locator.py:79` `(p / "tools" / "bin" / "extracta.exe").is_file()`、`:527` `return cdsroot / "tools" / "bin" / "extracta.exe"` —— 硬编码 Windows 二进制
- `services/extracta_reader.py:27` `import subprocess`，靠 `subprocess` 调 exe 拿 `!` 分隔的 ASCII 输出
- `services/skillbridge_reader.py:360` `ws = Workspace.open(workspace_id=workspace_id or "7777")` —— 连的是**已存在**的服务端
- `services/skillbridge_writer.py:270-300`：通道对不齐时的兜底提示是「请在 Allegro CIW 里执行 `pyKillServer()` / `load("...python_server.il")` / `pyStartServer ?id "7777"`」—— **证明初始化必须人工在 Allegro 里做**
- 反例（好消息）：`read_via_extracta()` **不需要 Allegro 开着**，只要 CDSROOT 有效。所以"检测"比"创建"容易一个数量级

---

## 三、架构方案

### 3.1 三段式职责划分

| 层 | 位置 | 职责 | 技术栈 |
|---|---|---|---|
| **Web 前端** | NAS 容器 | 上传、进度、报告查看/下载 | 单页 HTML + SSE（照 `BOM_validation/frontend` 最丑版起步） |
| **Web 后端** | NAS 容器 | HTTP/SSE、任务状态机、文件落盘、编排 | FastAPI + uvicorn |
| **Agent 运行时** | NAS 容器 | 对话、决策、串工具 | DSH SDK (`deepseek-harness-sdk`) |
| **工具插件（A 段）** | NAS 容器 | PDF/几何/规则/规格库 | Node/Cordis 插件调 Python，或直接 Python 服务 |
| **Windows 执行器** | 工位 | B/C 段工具，触达 Cadence | Python HTTP 小服务，主动轮询 NAS |
| **Allegro** | 工位 | 真正的落库动作 | 人工开 + 人工载 `python_server.il` |

### 3.2 为什么是「工位主动轮询」而不是「NAS 反向调用工位」

工位在 `192.168.0.196`（开发机网段），NAS 在 `192.168.30.252`，中间隔着 aTrust VPN。**NAS 主动连工位大概率不通**（NAT/防火墙方向不定），而工位连 NAS 的 8843 是已验证可行的。

因此：工位执行器每隔几秒 `GET /api/jobs/next` 领活，干完 `POST /api/jobs/{id}/result` 回报。这样不需要开新端口、不需要改 VPN 策略，也天然能扛住工位临时关机（任务排队等）。

### 3.3 端口与入口

- **不新开端口**。8843 已被 BOM 校验服务独占，但它占的是「宿主 8843 → 容器 8001」这条映射。
- 建议：**复用 8843 不可行**（同端口不能映射两个容器），需向运维申请**新端口**，或把两个服务合并到一个后端。
- **待决策项**：见 §六 Q1。

---

## 四、分阶段实施计划

原则：**每一步都能独立验收，不做"全做完才知道行不行"的赌博。**

### 阶段 0：环境打样（0.5 天）— 先证伪，别先写业务

| 任务 | 验收 |
|---|---|
| 0.1 容器跑通 `python:3.12-slim` + FastAPI，`/api/health` 返回 `ready:true` | 照 `BOM_validation` 抄，确保 glibc 2.36 够用 |
| 0.2 容器内装 PyMuPDF / pdfplumber，跑通 `pdf_probe` | 证明 A 段依赖能装（避开 glibc 2.28 陷阱） |
| 0.3 容器 `import` 纯 Python 层（`geometry_solver` / `rule_checker` / `spec_library`） | 证明无隐藏 Windows 依赖 |
| 0.4 工位执行器骨架：轮询 + 回执，先不接 Cadence | 证明跨网段这条链路通 |

> **风险闸门**：0.2 若因 wheel 装不上失败，说明整个 A 段方案要改（改用更小镜像或降 Python 版本），越早知道越好。

### 阶段 1：检测闭环（2-3 天）— 先做"查"，因为它不依赖 Allegro

| 任务 | 说明 |
|---|---|
| 1.1 上传 → 落盘 → 起任务（状态机 `uploaded → queued → running → done/failed`） |
| 1.2 工位执行器接 `read_full_footprint`（走 extracta） |
| 1.3 NAS 侧跑 `check_footprint_by_rules` 判定层 + 出 Markdown 报告 |
| 1.4 SSE 推 `status` / `progress` / `delta` / `done`，带 15s 心跳 |
| 1.5 前端最丑版：上传框 + 进度条 + 报告下载 |

> 选检测先做的理由：**它不需要有人守着 Allegro**，验收成本最低，且立刻对同事产生价值。同时能把 SSE、状态机、执行器握手这些基础设施全部打磨好。

### 阶段 2：创建闭环（3-5 天）— 最硬的一段

| 任务 | 说明 |
|---|---|
| 2.1 `solve_footprint_geometry` 在 NAS 跑，产出坐标方案给 AI/人复核 | 纯离线，可先独立验证 |
| 2.2 人工闸门：复核通过后，任务才派给工位 | 不允许 AI 直接落库 |
| 2.3 工位执行器接 C 段工具链（建 padstack → pin → outline → 存盘 → psm） |
| 2.4 建完自动回读（extracta）做**出厂自检**，形成「建-验」闭环 |
| 2.5 失败回滚：走 `_quarantine_file()` 隔离，不 `os.remove` |

> **风险闸门**：2.3 要求工位上 Allegro 已开、`python_server.il` 已载、通道已对齐。任何一环没准备好都会失败，且失败信息对同事毫无意义。必须设计**工位就绪探针**（开机自检显示绿灯才领活）。

### 阶段 3：生产化（2-3 天）

| 任务 | 说明 |
|---|---|
| 3.1 凭据：API Key 模式（**绝不拷 Windows 的账号凭据**，设备绑定不认） |
| 3.2 目录：NAS `/volume1/研发中心/...`，注意 btrfs 已用 93% |
| 3.3 部署脚本：照 `pack-for-nas.ps1` + `deploy-on-nas.sh` |
| 3.4 运维文档 + 故障排查表 |

**总计约 8-12 人天**（不含同事试用反馈迭代）。

---

## 五、必须提前想清楚的坑（都来自规格书实测记录）

| 坑 | 后果 | 对策 |
|---|---|---|
| DSM glibc 2.26 vs wheel 要 2.28 | 装不上，无解报错 | 用 `python:3.12-slim`（2.36） |
| Docker Hub 不通 | `docker build` 卡死 | 用 `docker.m.daocloud.io` 镜像站 |
| 容器内 apt/pip 连不上外网 | 构建 828 秒后超时 | 换 tuna 源 |
| btrfs 只剩 1.8TB / 93% | 构建失败或写满 | 镜像用完即清，产物定期归档 |
| 禁删约束 | 误 `docker rm` 打断 mssql | 所有清理走确认流程 |
| 设备绑定凭据 | Windows 凭据在 Linux 401 | 只用 API Key |
| SSE 60s 无数据断连 | 长任务看起来像卡死 | 15s keep-alive 心跳 |
| `tool/result` 无工具名 | 进度显示错乱 | 自建 `callId → name` 反查表 |
| 产物定位退回"任意 xlsx" | 下载到旧文件 | 只认本次时间戳 + 排除源文件 |

---

## 六、需要你拍板的四件事

### Q1：端口怎么解决？（阻塞部署，不阻塞开发）
- **A**：向运维申请新端口（如 8844），两个服务并列 —— *推荐，改动最小*
- **B**：把封装服务和 BOM 校验合并到一个后端 —— 省端口，但两套依赖耦合，风险大
- **C**：先只在局域网内网测试（不加 VPN 端口），做出效果再申请

### Q2：Windows 工位用哪台机器？
- 需要长期开机、装着 Cadence、且**有人愿意让它挂着**。
- 你的开发机 `192.168.0.196` 是否合适？还是另有专门工位？

### Q3：创建功能对"人工闸门"的接受度
- **A**：AI 出方案 → 人复核 → 才落库（*推荐，符合现状的谨慎程度*）
- **B**：常规封装全自动，只有异常才转人工
- 这决定了阶段 2 的复杂度和同事的使用习惯。

### Q4：第一版做多宽？
- **A**：只做「封装检测」（阶段 0+1，约 3 天）—— *推荐先落地*
- **B**：检测 + 创建全做（阶段 0-2，约 8 天）
- **C**：只做 PDF 选型参数抽取（更窄但极快，1-2 天）

---

## 七、我建议的推进顺序

1. **先定 Q1/Q4**（端口和范围），这两个决定了要不要现在就找运维。
2. **阶段 0 打样**（半天），只为证伪——如果 PyMuPDF 装不上，后面全白搭。
3. **阶段 1 检测闭环**，交付一个同事真能用的东西。
4. 拿到反馈后再决定阶段 2 的闸门形式。

**不建议**按规格书的顺序先写工具插件：本项目的 36 个工具**已经存在且经过 eval 验证**，直接复用比按 BOM 校验的模式重写一遍划算得多。这是本项目与参考案例最大的不同。

---

## 八、未决问题（需要你补充信息）

1. 同事的使用频次和并发量？（决定要不要任务队列）
2. 规格书 PDF 是否涉密、能否上传到 NAS？（决定是否要落盘加密）
3. 是否存在历史封装库需要批量检测？（那会是另一个数量级的任务）
4. Windows 工位是否允许常驻一个小服务？（IT 策略问题）
