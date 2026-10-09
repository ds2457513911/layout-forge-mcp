# -*- coding: utf-8 -*-
"""
mcp_server/resources.py —— MCP 资源定义

Resource 的作用是把"数据"暴露给客户端 AI 读。
AI 通过读 resource 拿到 PDF 页图片后，可以用自己的多模态能力提取参数。

URI 约定：
  datasheet://{pdf_stem}/page/{page}          PyMuPDF 整页图（PNG）
  datasheet://{pdf_stem}/info                 PDF 元信息（JSON 字符串）
"""
from __future__ import annotations

import json

from fastmcp import FastMCP

from mcp_server import config
from services import pdf_service


def register(mcp: FastMCP) -> None:
    """把所有 resource 注册到给定的 FastMCP 实例。"""

    @mcp.resource(
        "datasheet://{pdf_stem}/page/{page}",
        mime_type="image/png",
        description="PDF 指定页的整页图片（PyMuPDF 渲染），供 AI 直接查看",
    )
    def get_datasheet_page_image(pdf_stem: str, page: int) -> bytes:
        """
        返回 PDF 指定页的整页图片（PNG 字节）。

        渲染引擎固定为 PyMuPDF。
        如果客户端不支持 image resource，请改用 render_pdf_page tool。
        """
        pdf = pdf_service.resolve_pdf(pdf_stem, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            raise ValueError(f"找不到 PDF: {pdf_stem}")
        img_path = pdf_service.get_or_render_image(
            pdf_path=pdf,
            page=int(page),
            out_root=config.RENDER_OUTPUT_ROOT,
            engine="pymupdf",
        )
        if img_path is None or not img_path.is_file():
            raise ValueError(f"渲染失败: pdf={pdf.name} page={page}")
        return img_path.read_bytes()

    @mcp.resource(
        "datasheet://{pdf_stem}/info",
        description="PDF 元信息：总页数、land pattern 命中页、匹配关键词",
    )
    def get_datasheet_info(pdf_stem: str) -> str:
        """返回 PDF 的元信息（JSON 字符串）"""
        pdf = pdf_service.resolve_pdf(pdf_stem, config.PDF_SEARCH_ROOTS)
        if pdf is None:
            return json.dumps(
                {"error": f"找不到 PDF: {pdf_stem}"}, ensure_ascii=False
            )
        info = pdf_service.locate_land_pattern_page(pdf)
        info["pdf_path"] = str(pdf)
        info["pdf_name"] = pdf.name
        return json.dumps(info, ensure_ascii=False, indent=2)
