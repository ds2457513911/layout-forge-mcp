# mcp_server/ —— MCP 工具层（对外门面）

> 最后更新：2026-11-19（**工具清单改为生成物 `docs/tools-catalog.md`**，本目录不再抄数量；2026-10-14 曾新增 `folder_preflight` / `get_run_stats` 与 `middleware.py` 遥测中间件）

## 这个目录是干什么的

把 `services/` 的能力**包装成 MCP 工具**，暴露给 AI / 客户端调用。
**这一层只做参数转接 + 文档 + 注册，不写业务逻辑** ——
真正干活的全在 `services/`。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `tools.py` | ★ **工具注册总表（最大文件）**。工具清单不在此抄写：全表见 `docs/tools-catalog.md`（生成物）。近期新增：`folder_preflight`（目录歧义预检）/ `get_run_stats`（运行统计）为当日新增，读规格书三件套 `pdf_probe` / `render_pdf_region` / `measure_region` 为 2026-10-13 新增），每个工具 = 一个 `@mcp.tool()` 函数 + 完整 docstring（**docstring 就是给 AI 看的说明书，改行为必须同步改它**） | 会 import `services/geometry_solver` / `footprint_builder` / `library_install` / `footprint_extractor` / `pdf_service` 等，以及 `orchestration/preflight` / `observability/stats` |
| `middleware.py` | ★ **遥测中间件** `TelemetryMiddleware(Middleware)`：包住每次工具调用，把 `ts/run_id/tool/ok/ms/error_code/args_keys/args_hash` 落进 `eval/runs/<date>.jsonl`（**只落参数名与 hash，不落参数值**）；返回 `ok:false` 的业务失败也记（`error_source:"returned"`） | 放这一层而非 `observability/`，因为它必须 import `fastmcp`，而 `observability/` 要保持纯 stdlib |
| `prompts.py` | 预置提示词 | 纯文本，无副作用 |
| `resources.py` | MCP 资源（只读暴露） | 纯读 |
| `config.py` | 配置读取 | — |
| `server.py` | 服务启动入口（创建 `FastMCP`、挂载工具/资源/提示词，并 `add_middleware(TelemetryMiddleware())`） | **进程入口**，改它影响启动 |
| `__init__.py` | 包标记 | — |

## 工具契约（改这些会红）

`python eval/suites/tool_contract/check_tool_contract.py` —— 静态 AST 断言**工具数量 / 名字集合 /
docstring 是否写了每个参数 / 遥测字段口径 / 中间件是否挂上**，基线在 `eval/baselines/tool_contract.json`（进 git）。

★ 因此**加/删/改工具或参数时必须**：① 同轮改 `Tools/deploy/setup_agent.py::EXPECTED_TOOLS`（现 **36**）；
② 同轮改两份 SKILL 的工具清单与 `allegro-skillbridge-ops/references/mcp-tools.md`；
③ 跑 `check_tool_contract.py --update` 重新冻结基线。漏掉任何一步，断言或部署冒烟会红。

## 最近一次重要变更（2026-09-30）

位号口径改成以人工模板库为准，`solve_footprint_geometry` 的工具签名新增：

| 参数 | 默认 | 说明 |
|--|--|--|
| `refdes_text` | `"#REF"` | 原来默认 `"U*"`，**已改** |
| `refdes_targets` | `None` → 求解器用 `["refdes_assembly"]` | 目标层 |
| `refdes_place` | `"body_center"` | 居中于本体（旧行为 `"outside"` 仍可用）|
| `refdes_dx` / `refdes_dy` | `0.0` | 相对本体中心的偏移 |
| `refdes_x` / `refdes_y` | `None` | 绝对坐标覆盖（最高优先级）|
| `refdes_justify` | `"left"` | 与模板一致 |

`export_psm` 返回里新增 `evidence`（成功证据清单）；
`install_shadow_library` / `list_shadow_library` 的返回里新增 `colocation`。

## 关键约定与坑

1. **docstring 即接口文档**：客户端/AI 只看到 docstring。改了返回结构或默认值
   **必须同步改 docstring**，否则 AI 会按旧文档调用。
2. 这一层**不需要 Allegro** 就能 import（只有真正执行时才连）。
3. 注册新工具时**别忘 `server.py` 里挂载**（取决于组织方式：若 `tools.py` 里
   用装饰器注册到同一个 `mcp` 实例，则只要 import 到即可）。

## 相关目录

- 业务逻辑：`../services/`
- 部署/注册脚本：`../Tools/deploy/`（2026-10-12 从根 deploy/ 迁入）
- 工具清单总览（面向人）：`../README.md` 的「工具清单」段
