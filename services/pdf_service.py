# -*- coding: utf-8 -*-
"""
services/pdf_service.py —— PDF 操作服务

职责：
  - 在文件夹里找 PDF
  - 把用户给的 PDF 引用（全路径 / stem / 文件名）解析成绝对路径
  - 定位 land pattern 页（复用 job_find_land_pattern_page）
  - 渲染指定页为图片（复用 lib_datasheet_parser._render_page）

注意：
  lib_datasheet_parser._render_page 目前是下划线开头的内部函数，
  但渲染能力是 MCP 层需要的公共能力，暂时直接复用。
  未来建议在 lib_datasheet_parser 里暴露公共的 render_page()。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from services.pdf_locate import find_land_pattern_page
from services.pdf_renderer import _render_page


# ---------- 文件发现 ----------
def find_first_pdf(folder: Path) -> Optional[Path]:
    """
    在文件夹根目录下找第一个 PDF（按文件名排序，排除 ~$ 临时文件）。

    :param folder: 目标文件夹
    :return: PDF 绝对路径，找不到返回 None
    """
    if not folder.is_dir():
        return None
    pdfs = sorted(
        p for p in folder.glob("*.pdf")
        if p.is_file() and not p.name.startswith("~$")
    )
    return pdfs[0] if pdfs else None


# ---------- 路径解析 ----------
def resolve_pdf(pdf_ref: str, search_roots: list[Path]) -> Optional[Path]:
    """
    把用户给的 PDF 引用解析成绝对路径。

    支持三种形式：
      1. 完整路径（如 C:\\a\\b\\xxx.pdf）—— 直接返回
      2. 文件名（如 xxx.pdf）—— 在 search_roots 下递归找
      3. stem（如 xxx）—— 在 search_roots 下递归找 <stem>.pdf

    :param pdf_ref: PDF 引用字符串
    :param search_roots: 搜索根目录列表
    :return: PDF 绝对路径，找不到返回 None
    """
    if not pdf_ref:
        return None

    # 形式 1：直接路径
    p = Path(pdf_ref)
    if p.is_file() and p.suffix.lower() == ".pdf":
        return p.resolve()

    # 形式 2 / 3：在 search_roots 下搜索
    for root in search_roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob(f"{pdf_ref}.pdf"):
            if candidate.is_file():
                return candidate.resolve()
        for candidate in root.rglob(f"{pdf_ref}*.pdf"):
            if candidate.is_file():
                return candidate.resolve()
    return None


# ---------- 定位 ----------
def locate_land_pattern_page(pdf_path: Path) -> dict:
    """
    定位 PDF 中的 land pattern 页。

    :param pdf_path: PDF 绝对路径
    :return: find_land_pattern_page 的原始返回
    """
    return find_land_pattern_page(pdf_path)


# ---------- 渲染 ----------
def render_page(
    pdf_path: Path,
    page: int,
    out_root: Path,
    engine: str = "pymupdf",
) -> dict:
    """
    把 PDF 指定页渲染成图片。

    :param pdf_path: PDF 绝对路径
    :param page: 人眼 1-based 页码
    :param out_root: 输出根目录
    :param engine: "pymupdf"（默认，整页渲染）或 "marker"（版面分析）
    :return: {
        "ok": bool,
        "engine": str,
        "image_path": str | None,
        "md_path": str | None,
        "stderr": str,
        "stdout": str,
    }
    """
    return _render_page(pdf_path, page, out_root, engine=engine)


def get_or_render_image(
    pdf_path: Path,
    page: int,
    out_root: Path,
    engine: str = "pymupdf",
) -> Optional[Path]:
    """
    先检查缓存，命中则直接返回；否则渲染后返回图片路径。

    用于 resource 层频繁读同一页时避免重复渲染。

    :return: 图片绝对路径，渲染失败返回 None
    """
    # 缓存检查（仅 pymupdf 输出路径可预测）
    if engine == "pymupdf":
        cached = out_root / pdf_path.stem / "pymupdf_pages" / f"page_{page}.png"
        if cached.is_file():
            return cached

    res = render_page(pdf_path, page, out_root, engine=engine)
    if res.get("ok") and res.get("image_path"):
        return Path(res["image_path"])
    return None
