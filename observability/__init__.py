# -*- coding: utf-8 -*-
"""
observability/__init__.py —— L4 可观测性层（遥测 / 时间线 / 统计）

分层约定（唯一权威见 ../LAYERS.md）：

* 本层是**跨切面叶子**：只允许 import `services.paths`，**不 import 任何业务模块**
  （不 import mcp_server / orchestration / services 的其它模块）。
* 任何遥测写入**绝不抛异常** —— 观测失败不能把主流程拖下水。
* 只落**字段名 + 参数 hash**，绝不落参数值（客户料号、尺寸不进遥测文件）。
* 本层由 `mcp_server` 的中间件调用；本层**不反向调用** MCP 工具。
"""
