# -*- coding: utf-8 -*-
"""
services/cdsroot_locator.py —— 定位 Cadence Allegro 安装根目录（CDSROOT）

CDSROOT 是 extracta.exe 运行所需的环境根，结构举例：
    D:\Dev_tools\Cadence_17.2\Cadence\Cadence_SPB_17.2-2016\
    ├── tools\bin\extracta.exe
    ├── tools\bin\cdsCommon.dll
    └── share\pcb\text\views\

只找到 extracta.exe 不够——必须同时校验 cdsCommon.dll 和 views 目录，
否则运行时会 0xC0000409 崩溃（缺运行环境）。

搜索策略（借鉴 install_skillbridge.py 找 pcbenv 的思路）：
  1. 环境变量 CDSROOT / CDS_ROOT
  2. 常见安装路径（Cadence / Dev_tools / Program Files）
  3. 各盘符一级目录扫描
  4. 全盘深搜（可选，慢）

缓存策略：
  找到 → 模块级缓存，后续调用不再扫描
  找不到 → 不缓存，下次调用会重新扫（避免用户后装 Allegro 后不识别）
"""
from __future__ import annotations

import os
import platform
import string
from pathlib import Path
from typing import Optional


# 模块级缓存：一旦找到就记住
_CACHED_CDSROOT: Optional[Path] = None


# 常见安装路径（先按最可能的顺序）
_COMMON_PATHS = [
    # 标准 Cadence 安装
    Path("C:/Cadence/SPB_17.2"),
    Path("D:/Cadence/SPB_17.2"),
    Path("E:/Cadence/SPB_17.2"),
    Path("C:/Cadence/SPB_17.4"),
    Path("D:/Cadence/SPB_17.4"),
    Path("E:/Cadence/SPB_17.4"),
    # Program Files 下
    Path("C:/Program Files/Cadence/SPB_17.2"),
    Path("C:/Program Files/Cadence/SPB_17.4"),
    Path("C:/Program Files (x86)/Cadence/SPB_17.2"),
    Path("C:/Program Files (x86)/Cadence/SPB_17.4"),
    # Dev_tools 类型（用户实际环境）
    Path("D:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"),
    Path("C:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"),
    Path("E:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"),
    Path("D:/Dev_tools/Cadence_17.4/Cadence/Cadence_SPB_17.4-2019"),
    Path("D:/Dev_tools/Cadence/Cadence_SPB_17.2-2016"),
]


# 一级目录扫描时尝试的父目录模式
_SCAN_PARENTS = [
    "Cadence",
    "Dev_tools",
    "Program Files/Cadence",
    "Program Files (x86)/Cadence",
]


def _is_valid_cdsroot(p: Path) -> bool:
    """
    判断一个目录是否是有效的 CDSROOT。

    必须同时具备：
      - tools/bin/extracta.exe
      - tools/bin/cdsCommon.dll
      - share/pcb/text/views/（预定义视图目录）

    缺任一项都不算——只有这样才保证 extracta 运行时不会崩。
    """
    if not p.is_dir():
        return False
    return (
        (p / "tools" / "bin" / "extracta.exe").is_file()
        and (p / "tools" / "bin" / "cdsCommon.dll").is_file()
        and (p / "share" / "pcb" / "text" / "views").is_dir()
    )


def _scan_drive(drive_letter: str) -> Optional[Path]:
    """
    扫描一个盘符下常见位置的 CDSROOT。

    检查模式：
      <drive>:\Cadence\SPB_*                                → 直接就是 CDSROOT
      <drive>:\Cadence\*\Cadence\Cadence_SPB_*              → 嵌套版
      <drive>:\Dev_tools\Cadence_*\Cadence\Cadence_SPB_*    → Dev_tools 版
      <drive>:\Program Files\Cadence\SPB_*
    """
    root = Path(f"{drive_letter}:\\")
    if not root.exists():
        return None

    for parent_rel in _SCAN_PARENTS:
        parent = root / parent_rel
        if not parent.is_dir():
            continue
        try:
            for sub in parent.iterdir():
                if not sub.is_dir():
                    continue

                # 情况 A：sub 本身就是 CDSROOT（如 Cadence/SPB_17.2）
                if _is_valid_cdsroot(sub):
                    return sub

                # 情况 B：sub 下嵌套（如 Cadence_17.2/Cadence/Cadence_SPB_17.2-2016）
                try:
                    for sub2 in sub.iterdir():
                        if not sub2.is_dir():
                            continue
                        if _is_valid_cdsroot(sub2):
                            return sub2
                        # 再深一层（Cadence_17.2/Cadence/Cadence_SPB_*）
                        try:
                            for sub3 in sub2.glob("Cadence_SPB_*"):
                                if _is_valid_cdsroot(sub3):
                                    return sub3
                        except (PermissionError, OSError):
                            pass
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass

    return None


def find_cdsroot(deep: bool = False) -> Optional[Path]:
    """
    查找 CDSROOT。

    :param deep: 是否启用全盘深搜（很慢，几分钟）
    :return: 有效的 CDSROOT Path，或 None
    """
    global _CACHED_CDSROOT

    # ---- 缓存命中 ----
    if _CACHED_CDSROOT is not None and _is_valid_cdsroot(_CACHED_CDSROOT):
        return _CACHED_CDSROOT
    else:
        # 缓存失效（目录被删/移动），清空缓存重新找
        _CACHED_CDSROOT = None

    # ---- 策略 1：环境变量 ----
    for var in ("CDSROOT", "CDS_ROOT"):
        val = os.environ.get(var)
        if val:
            p = Path(val)
            if _is_valid_cdsroot(p):
                _CACHED_CDSROOT = p
                return p

    # ---- 策略 2：常见路径（快速） ----
    for p in _COMMON_PATHS:
        if _is_valid_cdsroot(p):
            _CACHED_CDSROOT = p
            return p

    # ---- 策略 3：各盘符扫描 ----
    if platform.system() == "Windows":
        for drive in string.ascii_uppercase:
            found = _scan_drive(drive)
            if found is not None:
                _CACHED_CDSROOT = found
                return found

    # ---- 策略 4：全盘深搜（可选） ----
    if deep:
        import warnings
        warnings.warn(
            "find_cdsroot(deep=True) 会扫描整个磁盘，耗时几分钟。"
            "建议优先设置 CDSROOT 环境变量或使用 --pcbenv 指定安装路径。",
            RuntimeWarning,
        )
        # 兜底：只扫一层（避免真全盘深搜）
        if platform.system() == "Windows":
            for drive in string.ascii_uppercase:
                root = Path(f"{drive}:\\")
                if not root.exists():
                    continue
                try:
                    for sub in root.iterdir():
                        if not sub.is_dir():
                            continue
                        if _is_valid_cdsroot(sub):
                            _CACHED_CDSROOT = sub
                            return sub
                except (PermissionError, OSError):
                    pass

    return None


def extracta_path(cdsroot: Path) -> Path:
    """根据 CDSROOT 拼接 extracta.exe 的完整路径。"""
    return cdsroot / "tools" / "bin" / "extracta.exe"