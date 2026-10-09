# -*- coding: utf-8 -*-
"""
services/pdf_renderer.py —— PDF 单页渲染（PyMuPDF）

被 services/pdf_service.py 用于把 PDF 指定页渲染成图片，
供 MCP resource 层把图片暴露给客户端 AI 读取。

说明：
  - 已移除 marker 引擎（版面分析）
  - 已移除 moondream 调用（视觉提取由客户端 AI 完成）
  - 只保留 PyMuPDF 整页渲染
"""
from __future__ import annotations

from pathlib import Path

# 默认渲染 DPI
DEFAULT_PYMUPDF_DPI = 150


def _render_page_with_pymupdf(
    pdf_path: Path,
    target_page: int,
    out_root: Path,
    dpi: int = DEFAULT_PYMUPDF_DPI,
) -> dict:
    """
    用 PyMuPDF 把 PDF 指定页渲染成整页 PNG。

    输出路径: <out_root>/<pdf_stem>/pymupdf_pages/page_{n}.png

    :param pdf_path: PDF 绝对路径
    :param target_page: 人眼 1-based 页码
    :param out_root: 输出根目录
    :param dpi: 渲染 DPI
    :return: {
        "ok": bool,
        "engine": "pymupdf",
        "image_path": str | None,
        "md_path": None,
        "stderr": str,
        "stdout": str,
    }
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "ok": False,
            "engine": "pymupdf",
            "image_path": None,
            "md_path": None,
            "stderr": "PyMuPDF 未安装，请运行: pip install PyMuPDF",
            "stdout": "",
        }

    out_dir = out_root / pdf_path.stem / "pymupdf_pages"
    out_dir.mkdir(parents=True, exist_ok=True)

    img_path = out_dir / f"page_{target_page}.png"

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return {
            "ok": False,
            "engine": "pymupdf",
            "image_path": None,
            "md_path": None,
            "stderr": f"PyMuPDF 打开 PDF 失败: {e}",
            "stdout": "",
        }

    try:
        page_index = target_page - 1
        if page_index < 0 or page_index >= len(doc):
            return {
                "ok": False,
                "engine": "pymupdf",
                "image_path": None,
                "md_path": None,
                "stderr": f"页码越界: page={target_page}, total={len(doc)}",
                "stdout": "",
            }
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        pix.save(str(img_path))
    except Exception as e:
        return {
            "ok": False,
            "engine": "pymupdf",
            "image_path": None,
            "md_path": None,
            "stderr": f"PyMuPDF 渲染失败: {e}",
            "stdout": "",
        }
    finally:
        doc.close()

    if not img_path.is_file():
        return {
            "ok": False,
            "engine": "pymupdf",
            "image_path": None,
            "md_path": None,
            "stderr": "PyMuPDF 渲染后图片文件不存在",
            "stdout": "",
        }

    return {
        "ok": True,
        "engine": "pymupdf",
        "image_path": str(img_path),
        "md_path": None,
        "stderr": "",
        "stdout": "",
    }


def _render_page(
    pdf_path: Path,
    target_page: int,
    out_root: Path,
    engine: str = "pymupdf",
) -> dict:
    """
    统一渲染入口。当前只支持 "pymupdf"。

    保留 engine 参数是为了向后兼容旧调用方；传入其他值会返回错误。
    """
    if engine != "pymupdf":
        return {
            "ok": False,
            "engine": engine,
            "image_path": None,
            "md_path": None,
            "stderr": (
                f"不支持的渲染引擎: {engine!r}"
                f"（已移除 marker，只支持 'pymupdf'）"
            ),
            "stdout": "",
        }
    return _render_page_with_pymupdf(pdf_path, target_page, out_root)
