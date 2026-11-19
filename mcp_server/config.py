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
# （原名叫 MARKER_ROOT，marker 引擎已移除，改名为 RENDER_OUTPUT_ROOT；
#   2026-10-12 重构：落盘位置从根 marker_out/ 迁到 Tools/log/render/，
#   运行痕迹统一进 Tools/log，项目根不再被污染）
RENDER_OUTPUT_ROOT = PROJECT_ROOT / "Tools" / "log" / "render"

# PDF 搜索根目录列表
# 当 AI 只传 PDF 的 stem（而非完整路径）时，会在此列表下递归搜索。
# 正常流程 AI 会传完整路径，此列表仅作兜底。
PDF_SEARCH_ROOTS: list[Path] = [
    PROJECT_ROOT,
]

# SkillBridge workspace id（MCP tool 调用 Allegro 时使用）
SKILLBRIDGE_WORKSPACE_ID = "7777"

# 等通道锁的超时（秒）：同一时刻只允许一个会话持有 SkillBridge 通道，
# 撞锁时最多等这么久，超时由工具返回 error_code=CHANNEL_BUSY + 持有人信息。
# ★ 默认值与实现只有一处：services/channel_guard.py；环境变量 FORGE_CHANNEL_LOCK_TIMEOUT 可覆盖。
from services import channel_guard as _channel_guard  # noqa: E402  (就近引入，避免顶部循环依赖)

CHANNEL_LOCK_TIMEOUT_S = _channel_guard.CHANNEL_LOCK_TIMEOUT_S
