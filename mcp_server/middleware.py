# -*- coding: utf-8 -*-
"""mcp_server/middleware.py —— L4 遥测的**接线点**（每次 `tools/call` → 一条事件）

## 为什么中间件放在 `mcp_server/` 而不是 `observability/`

因为它要 import `fastmcp`。`observability/` 层必须保持**纯标准库**
（`LAYERS.md` 的依赖方向：`mcp_server → orchestration → services`，
`observability` 是跨切面叶子，只能 import `services.paths`）。
把中间件放在工具层，观测层就永远是"任何解释器都能 import"的安全地带。

## 它记什么

| 字段 | 来源 |
|--|--|
| `tool` | `context.message.name` |
| `args_keys` / `args_hash` | `context.message.arguments`（**只落字段名与指纹**） |
| `ms` / `ok` / `error_code` | 调用包裹 + `telemetry.classify()` |
| `error_source` | `exception`（抛异常）/ `returned`（返回值里 `ok:false`） |
| `degraded[]` | 业务代码主动调 `rec.degrade(...)`（本中间件不产生） |

★ 已实测的 fastmcp 4.0.3 钩子签名：
  `on_call_tool(self, context: MiddlewareContext[mt.CallToolRequestParams],
                call_next: CallNext[mt.CallToolRequestParams, ToolResult]) -> ToolResult`
★ `structured_content` 是工具返回 dict 的结构化副本（`CallToolResult` 字段：
  `meta / content / structured_content / is_error / result_type`）—— 用它判断
  「工具自己判定的失败」（`ok:false`），**不用**去解 JSON 文本。

★ 观测失败绝不影响主流程：本文件里除了 `await call_next(context)` 本身，
  其余一律不抛（`telemetry` 内部已吞异常，字段读取用 `getattr` 兜底）。
"""
from __future__ import annotations

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from observability import telemetry

__all__ = ["TelemetryMiddleware"]


class TelemetryMiddleware(Middleware):
    """把每次工具调用记进 `eval/runs/<date>.jsonl`。"""

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        name = ""
        arguments = {}
        try:
            name = str(getattr(context.message, "name", "") or "")
            arguments = getattr(context.message, "arguments", None) or {}
            if not isinstance(arguments, dict):
                arguments = {}
        except Exception:                   # noqa: BLE001
            pass

        with telemetry.track(tool=name, args=arguments) as rec:
            result = await call_next(context)
            payload = None
            try:
                payload = getattr(result, "structured_content", None)
            except Exception:               # noqa: BLE001
                payload = None
            if isinstance(payload, dict) and payload.get("ok") is False:
                rec.fail(
                    code=str(payload.get("error_code") or "TOOL_FAILED"),
                    detail=str(payload.get("error") or ""),
                )
            return result
