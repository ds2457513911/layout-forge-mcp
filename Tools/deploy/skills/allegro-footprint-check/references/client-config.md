# 客户端配置示例（换机器 / 给同事）

> 从 SKILL.md 剥出（原「客户端配置示例」整节）。
> **使用场景**：新机器首次接入，或客户端看不到任何工具（"工具全都不见了"）。

## 客户端配置示例

本技能与 `allegro-skillbridge-ops` 共用同一个 MCP 服务 `layout-forge-mcp`
（36 个工具：建封装 18 + spec/案例库 7 + 读 PDF/目录 7 + 检查 2 + 编排/统计 2）。已经注册过就不用再配。

下面给的是**换机器 / 给同事**时的写法。

### 推荐：直接指向项目 venv 的 Python（改代码即生效，无缓存）

    {
      "mcpServers": {
        "layout-forge-mcp": {
          "command": "C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp/.venv/Scripts/python.exe",
          "args": [
            "-c",
            "import sys; sys.path.insert(0, r'C:/Users/ds245/Documents/Work_Bmorn/Layout/layout-forge-mcp'); from mcp_server.server import main; main()"
          ]
        }
      }
    }

★ **`command` 必须指向装了 fastmcp / skillbridge 的那个 Python。**
本项目是 `.venv/Scripts/python.exe` —— 实测系统 Python 和 WorkBuddy 托管
Python 都 `import skillbridge` 失败，用错解释器会直接连不上 Allegro。

★★ **`.venv` 不存在时先 `uv sync` 重建**（步骤见 `docs/SOP-工程维护手册.md` 的"重建 venv"一节）：
2026-10-14 实测**本机 `layout-forge-mcp\.venv\` 不存在**，而 WorkBuddy 的 `--mcp-config`
里 `command` 仍指向它 —— 也就是说**这台机器上 `layout-forge-mcp` 根本起不来**，
客户端只会看到"工具全都不见了"。判断方法：`Test-Path .venv\Scripts\python.exe`。
临时替代解释器：兄弟仓 `layout-check-mcp\.venv\Scripts\python.exe`（含 fastmcp 4.0.3 + skillbridge）。

---
