# deploy/ —— 部署脚本

> 最后更新：2026-09-30

## 这个目录是干什么的

把本 MCP 服务**装到目标环境**：注册 MCP、配置 SkillBridge 自动加载等。
纯 Windows 批处理（`.bat`）。

## 文件清单

| 文件 | 功能 | 影响 |
|--|--|--|
| `deploy.bat` | 部署 MCP 服务（装依赖、注册到 MCP 配置） | 会改用户级配置，**执行前先看脚本里的目标路径** |
| `deploy_skillbridge.bat` | 配置 Allegro 侧的 SkillBridge 自动加载（改 `pcbenv` 下的 `allegro.ilinit` 等） | 影响 Allegro 启动行为 |

## 关键约定与坑

1. ★ **这两个脚本会写用户环境**（MCP 配置 / Allegro `pcbenv`）。
   跑之前先确认路径，必要时先备份被改的文件。
2. `Tools/install_skillbridge.py` 是**同类功能的 Python 版**（更细的控制）。
   两者改的是同一处，**别重复跑**。
3. 部署完要**重启 MCP 客户端**才会加载新工具。
4. 与 `Tools/start_allegro.cmd` 的分工：那个只管**启动一次**，
   这里管**持久化配置**。

## 相关目录

- 服务源码：`../services/`、`../mcp_server/`
- 单次启动 / 收尾：`../Tools/start_allegro.cmd`、`../Tools/stop_allegro.py`
