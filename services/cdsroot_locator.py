# -*- coding: utf-8 -*-
"""
services/cdsroot_locator.py —— 定位 Cadence Allegro 安装根目录（CDSROOT）

CDSROOT 是 extracta.exe 运行所需的环境根，结构举例：
    D:\\Dev_tools\\Cadence_17.2\\Cadence\\Cadence_SPB_17.2-2016\\
    ├── tools\\bin\\extracta.exe
    ├── tools\\bin\\cdsCommon.dll
    └── share\\pcb\\text\\views\\

只找到 extracta.exe 不够——必须同时校验 cdsCommon.dll 和 views 目录，
否则运行时会 0xC0000409 崩溃（缺运行环境）。

搜索策略（v3，新增 HOME 反推）：
  0. HOME 反推（最可靠，优先）：
     Cadence 安装时会设 HOME 指向 <安装根>/SPB_Data。
     而 CDSROOT 要么就是 HOME 的父目录本身，要么与 HOME 同级。
     从 HOME 向上走 3 级，每级：
       a) 先检查 parent 自身是否就是 CDSROOT（应对 <CDSROOT>/SPB_Data 结构）
       b) 再扫 parent 的子目录（应对 <父>/SPB_Data 和 <父>/CDSROOT 平级结构）
     这条策略不依赖 CDSROOT 环境变量。
  1. 环境变量 CDSROOT / CDS_ROOT
  2. Windows 注册表（HKLM\\SOFTWARE\\Cadence Design Systems\\）
  3. 硬编码常见路径（标准安装位置，快速命中）
  4. 盘符智能扫描（名字提示驱动，最多 6 层）
  5. 可选全盘深搜（deep=True，慢）

缓存策略：
  找到 → 模块级缓存，后续调用不再扫描
  找不到 → 不缓存，下次调用会重新扫（避免用户后装 Allegro 后不识别）

调试：
  设置环境变量 LAYOUT_CHECK_CDSROOT_DEBUG=1 输出扫描过程到 stderr

独立运行（诊断）：
    uv run python services/cdsroot_locator.py
"""
from __future__ import annotations

import os
import platform
import string
import sys
from pathlib import Path
from typing import List, Optional


# ============================================================
# 调试日志
# ============================================================
def _log(msg: str) -> None:
    if os.environ.get("LAYOUT_CHECK_CDSROOT_DEBUG", "").strip() in ("1", "true", "yes"):
        print(f"[cdsroot_locator] {msg}", file=sys.stderr)


# ============================================================
# 模块级缓存
# ============================================================
_CACHED_CDSROOT: Optional[Path] = None


# ============================================================
# 校验函数
# ============================================================
def _is_valid_cdsroot(p: Optional[Path]) -> bool:
    """
    判断一个目录是否是有效的 CDSROOT。

    必须同时具备：
      - tools/bin/extracta.exe
      - tools/bin/cdsCommon.dll
      - share/pcb/text/views/（预定义视图目录）

    缺任一项都不算——只有这样才能保证 extracta 运行时不会崩。
    """
    if p is None or not p.is_dir():
        return False
    return (
        (p / "tools" / "bin" / "extracta.exe").is_file()
        and (p / "tools" / "bin" / "cdsCommon.dll").is_file()
        and (p / "share" / "pcb" / "text" / "views").is_dir()
    )


# ============================================================
# 策略 0：从 HOME 反推 CDSROOT（最可靠）
# ============================================================
def _from_home_env() -> Optional[Path]:
    """
    从 HOME 环境变量反推 CDSROOT。

    原理：
      Cadence 安装时会设置 HOME=<安装根>/SPB_Data。
      而 CDSROOT 有两种可能的相对位置：
        结构 A（HOME 在 CDSROOT 里）：
            D:\\...\\Cadence_SPB_17.2-2016\\        ← CDSROOT
            ├── SPB_Data                              ← HOME
            └── tools/
        结构 B（HOME 和 CDSROOT 同级）：
            D:\\Software\\Cadence\\                   ← 父目录
            ├── SPB_Data                              ← HOME
            └── Cadence_SPB_17.2-2016                 ← CDSROOT

    做法：
      1. 读 HOME，确认它"看起来是 Cadence 的 HOME"（路径含 cadence/spb/allegro，
         或目录名本身就是 SPB_Data 之类）
      2. 从 HOME 向上走 3 级，每级：
         a) 先检查 parent 自身是否就是 CDSROOT（覆盖结构 A）
         b) 再扫 parent 的子目录（覆盖结构 B）
    """
    home_val = os.environ.get("HOME")
    if not home_val:
        _log("HOME 未设置，跳过")
        return None

    home = Path(home_val)
    if not home.is_dir():
        _log(f"HOME={home} 不是有效目录，跳过")
        return None

    # ---- 判断 HOME 是否像 Cadence 的 HOME ----
    # 避免普通用户目录（C:\Users\xxx）被误当成 Cadence HOME
    name_lower = home.name.lower()
    path_lower = str(home).lower()
    is_cadence_home = (
        name_lower in ("spb_data", "cds_data", "pcbenv")
        or "cadence" in path_lower
        or "allegro" in path_lower
        or "spb_" in path_lower
    )
    if not is_cadence_home:
        _log(f"HOME={home} 不像 Cadence HOME，跳过")
        return None

    _log(f"HOME={home} 看起来是 Cadence HOME，尝试反推 CDSROOT")

    # ---- 从 HOME 向上走 3 级 ----
    current = home
    for level in range(3):
        parent = current.parent
        if not parent.is_dir() or parent == current:
            break

        # ---- a) 检查 parent 自身（结构 A：HOME 在 CDSROOT 里） ----
        if _is_valid_cdsroot(parent):
            _log(f"  HOME 反推命中 (parent 自身, 向上 {level} 级): {parent}")
            return parent

        # ---- b) 扫 parent 的子目录（结构 B：HOME 和 CDSROOT 同级） ----
        try:
            children = sorted(
                parent.iterdir(),
                key=lambda x: x.name,
                reverse=True,   # 名字倒序，版本号较大的优先
            )
        except (PermissionError, OSError):
            current = parent
            continue

        for child in children:
            if not child.is_dir():
                continue
            if _is_valid_cdsroot(child):
                _log(f"  HOME 反推命中 (向上 {level} 级): {child}")
                return child

        current = parent

    _log("  HOME 反推未命中")
    return None


# ============================================================
# 硬编码的常见路径（快速命中）
# ============================================================
_COMMON_PATHS: List[Path] = [
    # ============ D:/software/cadence 及常见子结构 ============
    Path("D:/software/cadence"),
    Path("C:/software/cadence"),
    Path("E:/software/cadence"),
    Path("D:/software/cadence/Cadence_SPB_17.2-2016"),
    Path("D:/software/cadence/Cadence_SPB_17.4-2019"),
    Path("D:/software/cadence/Cadence_SPB_16.6-2015"),
    Path("C:/software/cadence/Cadence_SPB_17.2-2016"),
    Path("C:/software/cadence/Cadence_SPB_17.4-2019"),
    Path("E:/software/cadence/Cadence_SPB_17.2-2016"),
    Path("E:/software/cadence/Cadence_SPB_17.4-2019"),
    Path("D:/software/cadence/SPB_17.2"),
    Path("D:/software/cadence/SPB_17.4"),
    Path("C:/software/cadence/SPB_17.2"),
    Path("C:/software/cadence/SPB_17.4"),

    # ============ 标准 Cadence 安装 ============
    Path("C:/Cadence/SPB_17.2"),
    Path("D:/Cadence/SPB_17.2"),
    Path("E:/Cadence/SPB_17.2"),
    Path("F:/Cadence/SPB_17.2"),
    Path("C:/Cadence/SPB_17.4"),
    Path("D:/Cadence/SPB_17.4"),
    Path("E:/Cadence/SPB_17.4"),
    Path("F:/Cadence/SPB_17.4"),

    # ============ Program Files 下 ============
    Path("C:/Program Files/Cadence/SPB_17.2"),
    Path("C:/Program Files/Cadence/SPB_17.4"),
    Path("C:/Program Files (x86)/Cadence/SPB_17.2"),
    Path("C:/Program Files (x86)/Cadence/SPB_17.4"),

    # ============ Dev_tools 类型（常见用户环境） ============
    Path("D:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"),
    Path("C:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"),
    Path("E:/Dev_tools/Cadence_17.2/Cadence/Cadence_SPB_17.2-2016"),
    Path("D:/Dev_tools/Cadence_17.4/Cadence/Cadence_SPB_17.4-2019"),
    Path("D:/Dev_tools/Cadence/Cadence_SPB_17.2-2016"),
]


# ============================================================
# 名字提示：这些名字暗示"值得深入扫描"
# ============================================================
_DEEPEN_HINTS = (
    "cadence", "spb", "allegro", "orcad", "eda",
    "software", "apps", "app", "program", "tools", "dev",
    "ic", "chip", "pcb", "design", "workspace", "work",
)

# 这些目录名直接跳过（系统/缓存目录，不可能装 Cadence）
_SKIP_DIRS = {
    "windows", "$recycle.bin", "system volume information",
    "recovery", "node_modules", ".git", "__pycache__",
    "temp", "tmp", "cache", "logs",
}


def _looks_like_deepen(name: str) -> bool:
    """目录名是否暗示值得深入扫描"""
    lower = name.lower()
    if lower in _SKIP_DIRS:
        return False
    if lower.startswith("."):
        return False
    return any(hint in lower for hint in _DEEPEN_HINTS)


# ============================================================
# 策略 1：环境变量
# ============================================================
def _from_env() -> Optional[Path]:
    for var in ("CDSROOT", "CDS_ROOT"):
        val = os.environ.get(var)
        if val:
            p = Path(val)
            _log(f"env {var}={val}")
            if _is_valid_cdsroot(p):
                return p
    return None


# ============================================================
# 策略 2：Windows 注册表
# ============================================================
def _from_registry() -> List[Path]:
    """
    查询 Windows 注册表，返回所有找到的有效 CDSROOT。

    Cadence 常见注册表位置：
      HKLM\\SOFTWARE\\Cadence Design Systems\\
      HKLM\\SOFTWARE\\WOW6432Node\\Cadence Design Systems\\
      HKCU\\SOFTWARE\\Cadence Design Systems\\
    """
    if platform.system() != "Windows":
        return []

    try:
        import winreg
    except ImportError:
        return []

    found: List[Path] = []
    seen: set = set()

    def _add(p: Path):
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key not in seen and _is_valid_cdsroot(p):
            seen.add(key)
            found.append(p)
            _log(f"registry hit: {p}")

    def _scan_key(root_hive, subkey_path: str, depth: int = 2) -> None:
        if depth < 0:
            return
        try:
            with winreg.OpenKey(root_hive, subkey_path) as key:
                # 扫本键的值
                i = 0
                while True:
                    try:
                        _, value_data, _ = winreg.EnumValue(key, i)
                        i += 1
                    except OSError:
                        break
                    if isinstance(value_data, str) and value_data:
                        base = Path(value_data)
                        _add(base)
                        # 有些值指向父目录，尝试拼接常见子路径
                        for suffix in (
                            "Cadence_SPB_17.2-2016",
                            "Cadence_SPB_17.4-2019",
                            "SPB_17.2",
                            "SPB_17.4",
                            "Cadence/Cadence_SPB_17.2-2016",
                            "Cadence/Cadence_SPB_17.4-2019",
                        ):
                            _add(base / suffix)

                # 扫子键
                j = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, j)
                        j += 1
                    except OSError:
                        break
                    try:
                        _scan_key(
                            root_hive,
                            f"{subkey_path}\\{subkey_name}",
                            depth - 1,
                        )
                    except Exception:
                        pass
        except (FileNotFoundError, PermissionError, OSError):
            pass

    registry_roots = [
        ("HKLM", r"SOFTWARE\Cadence Design Systems"),
        ("HKLM", r"SOFTWARE\WOW6432Node\Cadence Design Systems"),
        ("HKCU", r"SOFTWARE\Cadence Design Systems"),
    ]

    for hive_name, key_path in registry_roots:
        hive = (
            winreg.HKEY_LOCAL_MACHINE if hive_name == "HKLM"
            else winreg.HKEY_CURRENT_USER
        )
        _log(f"scanning registry: {hive_name}\\{key_path}")
        _scan_key(hive, key_path, depth=2)

    return found


# ============================================================
# 策略 3/4：智能盘符扫描
# ============================================================
def _walk_for_cdsroot(root: Path, max_depth: int = 6) -> Optional[Path]:
    """
    从 root 开始向下扫描，寻找有效的 CDSROOT。

    只深入名字"像容器"或"像 Cadence 相关"的目录，避免全盘遍历。
    """
    # 先检查自身
    if _is_valid_cdsroot(root):
        return root

    if max_depth <= 0:
        return None

    try:
        children = list(root.iterdir())
    except (PermissionError, OSError):
        return None

    for child in children:
        if not child.is_dir():
            continue
        if not _looks_like_deepen(child.name):
            continue
        result = _walk_for_cdsroot(child, max_depth - 1)
        if result:
            return result

    return None


def _scan_drive(drive_letter: str) -> Optional[Path]:
    """扫描单个盘符，找有效 CDSROOT"""
    root = Path(f"{drive_letter}:\\")
    if not root.exists():
        return None
    _log(f"scanning drive {drive_letter}:")
    return _walk_for_cdsroot(root, max_depth=6)


# ============================================================
# 公共 API：返回所有找到的 CDSROOT（诊断用）
# ============================================================
def find_all_cdsroots() -> List[Path]:
    """
    返回所有找到的有效 CDSROOT（不缓存）。
    用于诊断，正常流程用 find_cdsroot() 即可。
    """
    results: List[Path] = []
    seen: set = set()

    def _add(p: Optional[Path]):
        if p is None:
            return
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key not in seen and _is_valid_cdsroot(p):
            seen.add(key)
            results.append(p)

    # 0. HOME 反推
    _add(_from_home_env())

    # 1. 环境变量
    _add(_from_env())

    # 2. 注册表
    for p in _from_registry():
        _add(p)

    # 3. 常见路径
    for p in _COMMON_PATHS:
        _add(p)

    # 4. 盘符扫描
    if platform.system() == "Windows":
        for drive in string.ascii_uppercase:
            p = _scan_drive(drive)
            _add(p)

    return results


# ============================================================
# 主入口（保持原 API 兼容）
# ============================================================
def find_cdsroot(deep: bool = False) -> Optional[Path]:
    """
    查找 CDSROOT。

    顺序：
      0. HOME 反推
      1. 环境变量 CDSROOT / CDS_ROOT
      2. 注册表
      3. 硬编码常见路径
      4. 盘符智能扫描
      5. 可选全盘深搜

    :param deep: 是否启用全盘深搜（很慢，仅在前面的策略都失败时用）
    :return: 有效的 CDSROOT Path，或 None
    """
    global _CACHED_CDSROOT

    # ---- 缓存命中 ----
    if _CACHED_CDSROOT is not None and _is_valid_cdsroot(_CACHED_CDSROOT):
        return _CACHED_CDSROOT
    else:
        _CACHED_CDSROOT = None

    # ---- 策略 0：HOME 反推（最可靠，先试） ----
    p = _from_home_env()
    if p:
        _CACHED_CDSROOT = p
        return p

    # ---- 策略 1：环境变量 ----
    p = _from_env()
    if p:
        _CACHED_CDSROOT = p
        return p

    # ---- 策略 2：注册表 ----
    for p in _from_registry():
        if _is_valid_cdsroot(p):
            _CACHED_CDSROOT = p
            return p

    # ---- 策略 3：硬编码常见路径 ----
    for p in _COMMON_PATHS:
        if _is_valid_cdsroot(p):
            _log(f"common path hit: {p}")
            _CACHED_CDSROOT = p
            return p

    # ---- 策略 4：盘符扫描 ----
    if platform.system() == "Windows":
        for drive in string.ascii_uppercase:
            p = _scan_drive(drive)
            if p:
                _CACHED_CDSROOT = p
                return p

    # ---- 策略 5：全盘深搜（可选，慢） ----
    if deep:
        import warnings
        warnings.warn(
            "find_cdsroot(deep=True) 会扫描整个磁盘，耗时几分钟。"
            "建议优先设置 CDSROOT 环境变量或使用 --pcbenv 指定安装路径。",
            RuntimeWarning,
        )
        if platform.system() == "Windows":
            for drive in string.ascii_uppercase:
                root = Path(f"{drive}:\\")
                if not root.exists():
                    continue
                p = _walk_for_cdsroot(root, max_depth=8)
                if p:
                    _CACHED_CDSROOT = p
                    return p

    return None


# ============================================================
# 便捷函数（保持原 API）
# ============================================================
def extracta_path(cdsroot: Path) -> Path:
    """根据 CDSROOT 拼接 extracta.exe 的完整路径。"""
    return cdsroot / "tools" / "bin" / "extracta.exe"


# ============================================================
# 独立运行：诊断输出
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  CDSROOT 定位诊断")
    print("=" * 60)

    # 先打印 HOME（关键线索）
    home_val = os.environ.get("HOME")
    if home_val:
        print(f"\nHOME = {home_val}")
    else:
        print("\nHOME = (未设置)")

    print("\n[扫描中，可能需要几秒到几十秒...]")
    results = find_all_cdsroots()

    if results:
        print(f"\n找到 {len(results)} 个有效 CDSROOT：\n")
        for i, p in enumerate(results, 1):
            print(f"  [{i}] {p}")
        print()
        print("推荐设置环境变量（用户级）：")
        print(f'  setx CDSROOT "{results[0]}"')
        sys.exit(0)
    else:
        print("\n[!] 未找到任何有效的 CDSROOT")
        print()
        print("请检查：")
        print("  1. 本机是否安装了 Cadence Allegro / SPB")
        print("  2. HOME 环境变量是否指向 Cadence 的 SPB_Data 目录")
        print("  3. 安装目录下是否有：")
        print("     - tools\\bin\\extracta.exe")
        print("     - tools\\bin\\cdsCommon.dll")
        print("     - share\\pcb\\text\\views\\")
        print()
        print("如需详细扫描日志，设置：")
        print("  set LAYOUT_CHECK_CDSROOT_DEBUG=1")
        print("  然后重新运行本脚本")
        sys.exit(1)