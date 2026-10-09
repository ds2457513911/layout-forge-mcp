# -*- coding: utf-8 -*-
"""
mcp_server/config.py —— MCP 服务配置

所有路径都基于项目根目录解析，避免客户端拉起时 cwd 不确定导致路径漂移。
"""
from __future__ import annotations

from pathlib import Path

# 项目根目录（mcp_server/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# PDF 页面渲染输出根目录
# （原名叫 MARKER_ROOT，marker 引擎已移除，改名为 RENDER_OUTPUT_ROOT）
RENDER_OUTPUT_ROOT = PROJECT_ROOT / "marker_out"

# PDF 搜索根目录列表
# 当 AI 只传 PDF 的 stem（而非完整路径）时，会在此列表下递归搜索。
# 正常流程 AI 会传完整路径，此列表仅作兜底。
PDF_SEARCH_ROOTS: list[Path] = [
    PROJECT_ROOT,
]

# SkillBridge workspace id（MCP tool 调用 Allegro 时使用）
SKILLBRIDGE_WORKSPACE_ID = "7777"
