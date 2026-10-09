# -*- coding: utf-8 -*-
"""
services/allegro_service.py —— Allegro 封装文件操作服务

职责：
  - 扫描文件夹里的 .dra（排除 AUTOSAVE）
  - 扫描文件夹里的 .pad
  - 列出文件夹概览（PDF / .dra / .pad）
  - 列出父文件夹下的子文件夹

v4.0 变更：
  - 删除 read_one（旧版焊盘读取，返回阻焊开窗，已废弃）
  - 不再 import services.allegro_reader
"""
from __future__ import annotations

from pathlib import Path

from services.pdf_service import find_first_pdf


# ---------- 文件扫描 ----------
def scan_dra(folder: Path) -> list[Path]:
    """
    扫描文件夹里的所有 .dra，排除 AUTOSAVE 备份。

    :param folder: 目标文件夹
    :return: .dra 绝对路径列表（按名称排序）
    """
    if not folder.is_dir():
        return []
    files = sorted(p for p in folder.glob("*.dra") if p.is_file())
    return [f for f in files if not f.name.upper().startswith("AUTOSAVE")]


def scan_pad(folder: Path) -> list[Path]:
    """
    扫描文件夹里的所有 .pad。

    :param folder: 目标文件夹
    :return: .pad 绝对路径列表（按名称排序）
    """
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.pad") if p.is_file())


# ---------- 文件夹概览 ----------
def list_subfolders(parent: Path) -> list[str]:
    """列出父文件夹下所有子文件夹名（按名称排序）。"""
    if not parent.is_dir():
        return []
    return sorted(
        p.name for p in parent.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def list_folder_files(folder: Path) -> dict:
    """
    列出文件夹里的 PDF / .dra / .pad，供 AI 决策前了解文件夹内容。

    :param folder: 目标文件夹
    :return: {
        "folder": str,
        "pdf": str | None,
        "dra_files": [str],
        "pad_files": [str],
        "error": str | None
    }
    """
    if not folder.is_dir():
        return {
            "folder": str(folder),
            "pdf": None,
            "dra_files": [],
            "pad_files": [],
            "error": f"文件夹不存在: {folder}",
        }

    pdf = find_first_pdf(folder)
    dra_files = scan_dra(folder)
    pad_files = scan_pad(folder)

    return {
        "folder": str(folder.resolve()),
        "pdf": str(pdf.resolve()) if pdf else None,
        "dra_files": [str(p.resolve()) for p in dra_files],
        "pad_files": [str(p.resolve()) for p in pad_files],
        "error": None,
    }
