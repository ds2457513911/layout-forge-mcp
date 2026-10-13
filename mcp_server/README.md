# mcp_server/ —— MCP 工具层（对外门面）

> 最后更新：2026-10-13（新增 `pdf_probe` / `render_pdf_region` / `measure_region`，共 34 个工具）

## 这个目录是干什么的

把 `services/` 的能力**包装成 MCP 工具**，暴露给 AI / 客户端调用。
**这一层只做参数转接 + 文档 + 注册，不写业务逻辑** ——
真正干活的全在 `services/`。

## 文件清单

| 文件 | 功能 | 影响 / 依赖 |
|--|--|--|
| `tools.py` | ★ **工具注册总表（最大文件）**。**共 34 个工具**（2026-10-13；其中读规格书三件套 `pdf_probe` / `render_pdf_region` / `measure_region` 为当日新增），每个工具 = 一个 `@mcp.tool()` 函数 + 完整 docstring（**docstring 就是给 AI 看的说明书，改行为必须同步改它**） | 会 import `services/geometry_solver` / `footprint_builder` / `library_install` / `footprint_extractor` / `pdf_service` 等 |
| `prompts.py` | 预置提示词 | 纯文本，无副作用 |
| `resources.py` | MCP 资源（只读暴露） | 纯读 |
| `config.py` | 配置读取 | — |
| `server.py` | 服务启动入口（创建 `FastMCP`、挂载工具/资源/提示词） | **进程入口**，改它影响启动 |
| `__init__.py` | 包标记 | — |

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
