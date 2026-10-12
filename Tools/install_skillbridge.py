# -*- coding: utf-8 -*-
r"""
install_skillbridge.py —— 为 Allegro 配置 SkillBridge 自动加载

v3 变更（配合 v4 deploy.bat 的单环境方案）：
  - 不再创建/管理任何 venv；环境由 deploy.bat 的 `uv sync` 建在 <项目>\.venv
  - 只做两件事：
      1. 定位 pcbenv 目录
      2. 写 allegro.ilinit，指向 <项目>\.venv\Scripts\pythonw.exe
  - 原来的 ENV_DIR_NAME / ensure_uv / ensure_skillbridge_env 全部删除

为什么找 pcbenv 的活放在 Python 里而不是 bat：
  - 需要跨盘扫描、解析环境变量、多策略去重排序
  - bat 写这些会极其脆弱、难以维护
  - 这也让"一个入口 + 一个辅助脚本"成为最务实的方案

用法（一般由 deploy.bat 自动调用）：
    uv run python Tools/install_skillbridge.py
    uv run python Tools/install_skillbridge.py --pcbenv "C:/path/to/pcbenv"
    uv run python Tools/install_skillbridge.py --uninstall
    uv run python Tools/install_skillbridge.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import string
import sys
from datetime import datetime
from pathlib import Path

# ---- 项目根 = 本文件所在目录（Tools/）的上一级 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
VENV_PYTHONW = VENV_DIR / "Scripts" / "pythonw.exe"

# ---- 标记，用于在 allegro.ilinit 里定位插入的段落 ----
BEGIN_MARK = ";; ==== SkillBridge Auto-Load BEGIN (do not edit) ===="
END_MARK   = ";; ==== SkillBridge Auto-Load END ===="

# ---- 判断是否为 pcbenv 的标志文件 ----
PCBENV_MARKER_FILES = ["allegro.ilinit", "allegro.ini", "env", "pcbenv.ini"]


# ============================================================
# 基础工具
# ============================================================
def log(msg: str, level: str = "INFO") -> None:
    prefix = {
        "INFO": "   ",
        "OK":   "✅",
        "WARN": "⚠️",
        "ERR":  "❌",
        "STEP": "▶",
    }[level]
    print(f"{prefix} {msg}")


# ============================================================
# 校验 .venv 是否就绪（由 deploy.bat 的 uv sync 创建）
# ============================================================
def check_venv() -> tuple[Path, Path] | None:
    r"""
    检查 <项目>\.venv 是否已就绪，返回 (pythonw_path, il_path) 或 None。

    il_path 是 skillbridge 的 SKILL 加载入口
    （.venv\Lib\site-packages\skillbridge\server\python_server.il）。
    """
    if not VENV_PYTHON.is_file():
        log(f"未找到 .venv 的 Python: {VENV_PYTHON}", "ERR")
        log("请先运行 deploy.bat（它会执行 uv sync 建好 .venv）", "INFO")
        return None

    # pythonw 用于无窗口启动，若不存在则退化用 python.exe
    pythonw = VENV_PYTHONW if VENV_PYTHONW.is_file() else VENV_PYTHON

    hits = list(VENV_DIR.glob("**/skillbridge/server/python_server.il"))
    if not hits:
        log(f"在 {VENV_DIR} 下找不到 skillbridge/server/python_server.il", "ERR")
        log("可能 uv sync 未完成，请重新运行 deploy.bat", "INFO")
        return None
    il_path = hits[0]

    log(f"Pythonw: {pythonw}", "OK")
    log(f"SKILL:   {il_path}", "OK")
    return pythonw, il_path


# ============================================================
# 多策略搜索 pcbenv（逻辑与原版一致，保留不动）
# ============================================================
def _search_dir_recursive(root: Path, max_depth: int, results: list[Path],
                          visited: set[str]) -> None:
    if max_depth <= 0:
        return
    try:
        key = str(root.resolve()).lower()
    except Exception:
        key = str(root).lower()
    if key in visited:
        return
    visited.add(key)

    skip_names = {
        "windows", "program files", "program files (x86)",
        "$recycle.bin", "system volume information", "recovery",
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "anaconda3", "miniconda3", "python", "python3",
    }

    try:
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            lname = entry.name.lower()
            if lname in skip_names:
                continue
            if lname.startswith("."):
                continue

            if lname == "pcbenv":
                results.append(entry)
                continue
            if any((entry / m).is_file() for m in PCBENV_MARKER_FILES):
                results.append(entry)
                continue

            _search_dir_recursive(entry, max_depth - 1, results, visited)
    except (PermissionError, OSError):
        pass


def find_pcbenv_candidates(deep: bool = False) -> list[Path]:
    candidates: list[Path] = []
    system = platform.system()

    if system == "Windows":
        home = Path.home()

        # ---- 策略 1：环境变量 ----
        for env_var in [
            "SPB_Data", "SPB_DATA", "CDS_Data", "CDS_DATA",
            "CDSROOT", "CDS_ROOT", "HOME",
        ]:
            val = os.environ.get(env_var)
            if not val:
                continue
            try:
                p = Path(val)
            except Exception:
                continue
            if (p / "pcbenv").is_dir():
                candidates.append(p / "pcbenv")
            if p.name.lower() == "pcbenv" and p.is_dir():
                candidates.append(p)

        # ---- 策略 2：常见位置 ----
        common = [
            home / "pcbenv",
            home / "AppData" / "Roaming" / "pcbenv",
            Path("C:/SPB_Data/pcbenv"),
            Path("D:/SPB_Data/pcbenv"),
            Path("E:/SPB_Data/pcbenv"),
            Path("C:/Cadence/SPB_Data/pcbenv"),
            Path("D:/Cadence/SPB_Data/pcbenv"),
            Path("E:/Cadence/SPB_Data/pcbenv"),
        ]
        for p in common:
            if p.is_dir():
                candidates.append(p)

        # ---- 策略 3：Roaming 下的 SPB_* ----
        roaming = home / "AppData" / "Roaming"
        if roaming.is_dir():
            try:
                candidates.extend(roaming.glob("SPB_*/pcbenv"))
                candidates.extend(roaming.glob("*/pcbenv"))
            except Exception:
                pass

        # ---- 策略 4：各盘符一级目录 ----
        for drive in string.ascii_uppercase:
            root = Path(f"{drive}:\\")
            if not root.exists():
                continue
            try:
                for sub in root.iterdir():
                    if not sub.is_dir():
                        continue
                    for pattern in [
                        sub / "pcbenv",
                        sub / "SPB_Data" / "pcbenv",
                        sub / "Cadence" / "SPB_Data" / "pcbenv",
                        sub / "Cadence" / "pcbenv",
                    ]:
                        if pattern.is_dir():
                            candidates.append(pattern)
            except (PermissionError, OSError):
                pass

        # ---- 策略 5：全盘深度搜索 ----
        if deep:
            log("启用深度搜索（可能耗时几分钟）...", "INFO")
            visited: set[str] = set()
            for drive in string.ascii_uppercase:
                root = Path(f"{drive}:\\")
                if not root.exists():
                    continue
                _search_dir_recursive(root, max_depth=4, results=candidates,
                                      visited=visited)

    elif system == "Darwin":
        candidates.extend((Path.home() / "cds").glob("*pcbenv"))
        candidates.extend(Path("/Applications").glob("*Cadence*/SPB_Data/pcbenv"))
        candidates.extend(Path.home().glob("pcbenv"))
        if deep:
            _search_dir_recursive(Path.home(), 4, candidates, set())

    else:  # Linux
        candidates.extend(Path.home().glob("cds*/pcbenv"))
        candidates.extend(Path.home().glob("pcbenv"))
        if deep:
            _search_dir_recursive(Path.home(), 4, candidates, set())

    # ---- 去重 ----
    result: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue
        try:
            key = str(p.resolve()).lower()
        except Exception:
            key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(p)
    return sorted(result)


def choose_pcbenv(candidates: list[Path], dry_run: bool,
                  non_interactive: bool = False) -> Path:
    if not candidates:
        print()
        print("=" * 60)
        print("❌ 自动搜索未能找到 Allegro 的 pcbenv 目录")
        print("=" * 60)
        print()
        print("可能的原因：")
        print("  1. Allegro 从未启动过（首次启动才会创建 pcbenv）")
        print("  2. Allegro 装在非常规路径")
        print()
        print("建议：")
        print("  [A] 启动一次 Allegro PCB Editor，然后重新运行 deploy.bat")
        print("  [B] 手动指定：")
        print("      uv run python Tools/install_skillbridge.py --pcbenv \"C:/完整/路径/pcbenv\"")
        print()
        print("如何找到 pcbenv 路径？")
        print("  打开 Allegro，命令窗口输入：getShellEnvVar(\"SPB_Data\")")
        print("  返回值后面拼上 /pcbenv 就是它")
        print()
        sys.exit(1)

    if len(candidates) == 1:
        log(f"找到 pcbenv: {candidates[0]}", "OK")
        return candidates[0]

    print()
    print(f"发现 {len(candidates)} 个 pcbenv 目录：")
    for i, c in enumerate(candidates):
        print(f"  [{i}] {c}")
    print()

    if dry_run:
        log(f"[DRY-RUN] 默认选第一个: {candidates[0]}", "WARN")
        return candidates[0]

    # 一键部署（deploy.bat -> setup_agent.py）走这条路：不能弹交互式提问，
    # 否则双击运行的窗口会一直卡在等待输入。
    if non_interactive:
        log(f"非交互模式，默认选第一个: {candidates[0]}", "WARN")
        log("  若选错，用 --pcbenv \"完整路径\" 重新跑一次即可覆盖。", "WARN")
        return candidates[0]

    while True:
        try:
            idx = int(input("选择要使用的编号（回车默认 0）：").strip() or "0")
            if 0 <= idx < len(candidates):
                return candidates[idx]
        except (ValueError, KeyboardInterrupt):
            print("输入无效，请重试")


# ============================================================
# 写 allegro.ilinit
# ============================================================
def build_marked_block(pythonw: Path, il_path: Path) -> str:
    """
    生成要插入的 SKILL 段落。

    注意：allegro.ilinit 本身是 SKILL 文件，不要写单独的 `skill` 前缀。
    """
    py = str(pythonw).replace("\\", "/")
    il = str(il_path).replace("\\", "/")
    now = datetime.now().isoformat(timespec="seconds")

    return f"""{BEGIN_MARK}
;; 生成时间: {now}
;; Python:   {py}
;; SKILL:    {il}
;; 卸载: uv run python Tools/install_skillbridge.py --uninstall
load("{il}")
pyKillServer
pyStartServer ?id "7777" ?python "{py}"
{END_MARK}"""


def remove_marked_block(text: str) -> tuple[str, bool]:
    pattern = re.compile(
        re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"\s*",
        re.DOTALL,
    )
    if not pattern.search(text):
        return text, False
    return pattern.sub("", text), True


def write_ilinit(pcbenv: Path, pythonw: Path, il_path: Path, dry_run: bool) -> Path:
    target = pcbenv / "allegro.ilinit"
    block = build_marked_block(pythonw, il_path)

    if target.is_file():
        old = target.read_text(encoding="utf-8")
        cleaned, removed = remove_marked_block(old)
        if removed:
            log("检测到旧的 SkillBridge 标记块，将替换", "INFO")
        new_content = block + "\n\n" + cleaned.lstrip()
    else:
        new_content = block + "\n"

    if dry_run:
        log(f"[DRY-RUN] 将写入: {target}", "WARN")
        print("--- 预览 ---")
        print(new_content)
        print("------------")
        return target

    if target.is_file():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = target.with_name(f"allegro.ilinit.bak_{ts}")
        shutil.copy2(target, backup)
        log(f"已备份原文件: {backup.name}", "OK")

    target.write_text(new_content, encoding="utf-8")
    log(f"已写入: {target}", "OK")
    return target


# ============================================================
# 卸载
# ============================================================
def uninstall() -> int:
    candidates = find_pcbenv_candidates(deep=False)
    if not candidates:
        log("找不到任何 pcbenv 目录", "ERR")
        return 1

    found_any = False
    for pcbenv in candidates:
        target = pcbenv / "allegro.ilinit"
        if not target.is_file():
            continue
        old = target.read_text(encoding="utf-8")
        cleaned, removed = remove_marked_block(old)
        if not removed:
            log(f"{target} 未发现标记块，跳过", "WARN")
            continue
        found_any = True
        backup = target.with_name(
            f"allegro.ilinit.uninstall_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(target, backup)
        target.write_text(cleaned, encoding="utf-8")
        log(f"已从 {target} 移除标记块（备份: {backup.name}）", "OK")

    print()
    if found_any:
        print("提示：")
        print("  - Allegro 下次启动将不再自动加载 SkillBridge")
    return 0


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="为 Allegro 配置 SkillBridge 自动加载（依赖 <项目>/.venv）"
    )
    parser.add_argument("--uninstall", action="store_true",
                        help="从 allegro.ilinit 里移除 SkillBridge 标记块")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览，不实际写入文件")
    parser.add_argument("--deep-search", action="store_true",
                        help="启用全盘深度搜索 pcbenv（慢，但覆盖最全）")
    parser.add_argument("--non-interactive", action="store_true",
                        help="非交互：多个 pcbenv 候选时直接选第一个（供 deploy.bat 一键部署用）")
    parser.add_argument("--pcbenv", type=str, default=None,
                        help="手动指定 pcbenv 目录路径")
    args = parser.parse_args()

    print("=" * 60)
    print(" SkillBridge 自动加载配置工具")
    print("=" * 60)
    print()

    if args.uninstall:
        return uninstall()

    # ---- 步骤 1：校验 .venv（由 deploy.bat 的 uv sync 建好） ----
    log("步骤 1/3：校验 .venv")
    checked = check_venv()
    if checked is None:
        return 1
    pythonw, il_path = checked
    print()

    # ---- 步骤 2：找 pcbenv ----
    log("步骤 2/3：查找 Allegro 的 pcbenv 目录")
    if args.pcbenv:
        pcbenv = Path(args.pcbenv)
        if not pcbenv.is_dir():
            log(f"手动指定的 pcbenv 不存在: {pcbenv}", "ERR")
            return 1
        log(f"使用手动指定的 pcbenv: {pcbenv}", "OK")
    else:
        candidates = find_pcbenv_candidates(deep=args.deep_search)
        pcbenv = choose_pcbenv(candidates, args.dry_run, args.non_interactive)
    print()

    # ---- 步骤 3：写 allegro.ilinit ----
    log("步骤 3/3：更新 allegro.ilinit")
    target = write_ilinit(pcbenv, pythonw, il_path, args.dry_run)
    print()

    if not args.dry_run:
        print("=" * 60)
        print("✅ 配置完成")
        print("=" * 60)
        print()
        print("下一步：")
        print("  1. 完全关闭 Allegro PCB Editor")
        print("  2. 重新打开")
        print("  3. 观察 CIW 命令窗口是否输出 SkillBridge 启动信息")
        print("  4. 命令行验证端口: netstat -ano | findstr 7777")
        print()
        print("如果出问题：")
        print(f"  - 备份文件目录: {target.parent}")
        print("  - 一键卸载: uv run python Tools/install_skillbridge.py --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())