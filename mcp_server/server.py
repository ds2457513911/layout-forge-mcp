# -*- coding: utf-8 -*-
"""
mcp_server/server.py —— MCP 服务入口

启动方式：
    python -m mcp_server.server

客户端配置示例（Claude Desktop / Cursor / Trae）：
    {
      "mcpServers": {
        "layout-forge-mcp": {
          "command": "uv",
          "args": ["run", "--directory", "C:\\\\path\\\\to\\\\layout-forge-mcp", "layout-forge-mcp"]
        }
      }
    }

说明：
  - cwd 必须设置为项目根目录，这样 `-m mcp_server.server` 才能找到包。
  - 所有路径配置见 mcp_server/config.py。
  - 生成类工具需要 Allegro 正在运行，且 SkillBridge 端口（默认 7777）可用。
"""
from __future__ import annotations

from fastmcp import FastMCP

# 创建 MCP 实例
mcp = FastMCP("layout-forge-mcp")

# 注册 tool / resource / prompt
# 注意：注册函数接收 mcp 作为参数，避免模块间循环导入。
from mcp_server.tools import register as register_tools
from mcp_server.resources import register as register_resources
from mcp_server.prompts import register as register_prompts

register_tools(mcp)
register_resources(mcp)
register_prompts(mcp)

# 遥测中间件（L4）：每次 tools/call 落一条事件到 eval/runs/<date>.jsonl。
# 放在工具层是因为它要 import fastmcp；observability/ 保持纯标准库。
# 埋点绝不抛异常 —— 即使遥测整体坏掉，工具调用照常返回。
from mcp_server.middleware import TelemetryMiddleware   # noqa: E402  (注册后导入，顺序无副作用)

mcp.add_middleware(TelemetryMiddleware())


def main():
    mcp.run()


if __name__ == "__main__":
    main()
