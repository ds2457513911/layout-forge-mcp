# -*- coding: utf-8 -*-
r"""
setup_agent.py —— layout-forge-mcp 的「Agent 侧」一键配置

由 deploy.bat 在最后一步调用，也可以单独跑：

    .venv\Scripts\python.exe deploy\setup_agent.py
    .venv\Scripts\python.exe deploy\setup_agent.py --dry-run
    .venv\Scripts\python.exe deploy\setup_agent.py --pcbenv "D:/Cadence/SPB_Data/pcbenv"
    .venv\Scripts\python.exe deploy\setup_agent.py --no-skillbridge

做四件事：
    A. 注册 MCP        —— 写客户端的 mcp.json（venv python + -c 注入 sys.path）
    B. 安装 Skill      —— deploy\skills\* 复制到 %USERPROFILE%\.workbuddy\skills\
    C. 配 SkillBridge  —— 调 Tools/install_skillbridge.py --non-interactive
    D. 冒烟验收        —— import 依赖 + 读 MCP 工具总数

设计约束（★ 都是踩过的坑，改之前先读）：
    1. MCP 的 command 必须是项目 .venv 里的 python.exe。skillbridge 只装在
       项目 venv 里，系统 Python / 托管 Python 都 import 不到。
    2. args 必须用 -c 注入 sys.path。客户端拉起 MCP 时 cwd 不确定，
       不注入会 ModuleNotFoundError: mcp_server。
    3. 写 mcp.json 是「合并」不是「覆盖」——用户可能已经配了别的 server。
       写之前先备份成 <原名>.bak_<时间戳>。
    4. 全程非交互：部署脚本会被双击运行，任何 input() 都会把窗口卡死。

退出码：0 = 成功（允许 SkillBridge 未配，那是 WARN）；1 = 失败。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "layout-forge-mcp"

# ---- 本文件在 deploy\ 下，项目根 = 上一级 ----
DEPLOY_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_ROOT = DEPLOY_DIR.parent


# ============================================================
# 输出
# ============================================================
def log(msg: str = "", level: str = "INFO") -> None:
    prefix = {
        "INFO": "   ",
        "OK":   "[OK]",
        "WARN": "[!!]",
        "ERR":  "[XX]",
        "STEP": ">>>",
    }.get(level, "   ")
    print(f"{prefix} {msg}" if msg else "")


def hr(title: str = "") -> None:
    print()
    if title:
        print("=" * 62)
        print(f" {title}")
        print("=" * 62)
    else:
        print("-" * 62)


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ============================================================
# A. 注册 MCP
# ============================================================
def candidate_config_files() -> list[tuple[str, Path]]:
    """按优先级列出「已知客户端」的配置文件。"""
    home = Path.home()
    return [
        ("WorkBuddy", home / ".workbuddy" / "mcp.json"),
        ("Claude Desktop", home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"),
        ("Cursor", home / ".cursor" / "mcp.json"),
        ("Trae", home / ".trae" / "mcp.json"),
    ]


def pick_config_target() -> tuple[str, Path, bool]:
    """
    返回 (客户端名, 配置文件路径, 是既有文件还是新建)。

    选法：先找**已存在**的（按优先级）；一个都没有就用 WorkBuddy 的路径新建。
    全都没有客户端目录时，退回 deploy\mcp-config.json，让用户手工复制。
    """
    home = Path.home()
    for name, path in candidate_config_files():
        if path.exists():
            return name, path, True

    wb = home / ".workbuddy"
    if wb.is_dir():
        return "WorkBuddy", wb / "mcp.json", False

    return "manual", DEPLOY_DIR / "mcp-config.json", False


def build_server_entry(project_root: Path, venv_python: Path) -> dict:
    root_posix = str(project_root).replace("\\", "/")
    py_posix = str(venv_python).replace("\\", "/")
    return {
        "command": py_posix,
        "args": [
            "-c",
            f"import sys; sys.path.insert(0, r'{root_posix}'); "
            f"from mcp_server.server import main; main()",
        ],
    }


def write_mcp_config(project_root: Path, venv_python: Path, dry_run: bool) -> tuple[bool, Path, str]:
    name, target, existed = pick_config_target()

    if name == "manual":
        log("没找到已知的客户端配置目录，改为生成独立的配置文件：", "WARN")
        log(f"  {target}")
        log("  装完请手工把里面的 mcpServers 合并进你的客户端配置。", "WARN")
        data: dict = {"mcpServers": {}}
    elif existed:
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig"))
            log(f"发现 {name} 的现有配置：{target}")
        except Exception as exc:  # 坏 json 不要炸，备份后重写
            log(f"{target} 不是合法 JSON（{exc}），将备份后重建。", "WARN")
            data = {}
    else:
        log(f"未发现既有配置，将在 {name} 下新建：{target}")
        data = {}

    data.setdefault("mcpServers", {})
    existed_servers = [k for k in data["mcpServers"] if k != SERVER_NAME]
    data["mcpServers"][SERVER_NAME] = build_server_entry(project_root, venv_python)

    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if dry_run:
        log("[DRY-RUN] 将要写入的内容：", "WARN")
        print(payload)
        return True, target, name

    if target.exists():
        backup = target.with_name(f"{target.name}.bak_{ts()}")
        shutil.copy2(target, backup)
        log(f"已备份原配置：{backup.name}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    log(f"MCP 已注册：{SERVER_NAME}", "OK")
    if existed_servers:
        log(f"保留的其他 server：{', '.join(existed_servers)}")
    return True, target, name


# ============================================================
# B. 安装 Skill
# ============================================================
def install_skills(dry_run: bool) -> bool:
    src_root = DEPLOY_DIR / "skills"
    if not src_root.is_dir():
        log(f"未找到 {src_root}，跳过 Skill 安装。", "WARN")
        return False

    home = Path.home()
    dst_root = home / ".workbuddy" / "skills"
    if not (home / ".workbuddy").is_dir():
        log("未发现 %USERPROFILE%\\.workbuddy\\ 目录，跳过 Skill 安装。", "WARN")
        log(f"如需手工安装，把下面这俩目录整体复制到你的 Agent skills 目录：", "WARN")
        for d in sorted(src_root.iterdir()):
            if d.is_dir():
                log(f"  {d}")
        return False

    ok = True
    for src in sorted(src_root.iterdir()):
        if not src.is_dir():
            continue
        dst = dst_root / src.name

        # ★ _user_meta.json 是客户端给 skill 写的元数据（installedAt / source）。
        #   仓库副本里没有它，覆盖安装必须把它留下来，否则客户端会认不出
        #   这个 skill 是已安装的。同理，备份目录里也不要丢。
        saved_meta: bytes | None = None
        meta_path = dst / "_user_meta.json"
        if meta_path.is_file():
            saved_meta = meta_path.read_bytes()

        if dst.exists():
            backup = dst_root / f"{src.name}.bak_{ts()}"
            if dry_run:
                log(f"[DRY-RUN] 已存在，将备份为 {backup.name} 后覆盖：{src.name}", "WARN")
            else:
                shutil.move(str(dst), str(backup))
                if saved_meta is not None:
                    (backup / "_user_meta.json").write_bytes(saved_meta)
                log(f"已存在，备份为 {backup.name}")
        if dry_run:
            log(f"[DRY-RUN] 将复制 {src.name} -> {dst}", "WARN")
            continue
        dst_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(dst))
        if saved_meta is not None:
            (dst / "_user_meta.json").write_bytes(saved_meta)
            log("  保留原 _user_meta.json")
        log(f"Skill 已安装：{dst}", "OK")
    return ok


# ============================================================
# C. 配 SkillBridge 自动加载
# ============================================================
def install_skillbridge(project_root: Path, pcbenv: str | None, dry_run: bool) -> bool:
    installer = project_root / "Tools" / "install_skillbridge.py"
    if not installer.is_file():
        log(f"未找到 {installer}，跳过 SkillBridge 配置。", "WARN")
        log("  建封装**必须**走 SkillBridge，请后续补配：启动一次 Allegro", "WARN")
        log("  生成 pcbenv 后，再单独跑一次本脚本。", "WARN")
        return False

    argv = [str(installer), "--non-interactive"]
    if pcbenv:
        argv += ["--pcbenv", pcbenv]
    if dry_run:
        argv += ["--dry-run"]

    sys.path.insert(0, str(installer.parent))
    try:
        import importlib

        mod = importlib.import_module("install_skillbridge")
    except Exception as exc:
        log(f"导入 install_skillbridge 失败：{exc}", "ERR")
        return False

    saved_argv = sys.argv
    sys.argv = argv
    try:
        rc = mod.main()
    except SystemExit as exc:  # 找不到 pcbenv 时它内部 sys.exit(1)
        rc = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = saved_argv

    if rc == 0:
        log("Allegro SkillBridge 自动加载已配置（端口 7777）", "OK")
        return True

    log("SkillBridge 自动加载未配成（MCP 本身不受影响）。", "WARN")
    log("  常见原因：Allegro 从未启动过，机器还没有 pcbenv 目录。", "WARN")
    log("  处理：启动一次 Allegro PCB Editor，然后重跑本脚本；", "WARN")
    log("        或用 --pcbenv \"完整路径\" 直接指定。", "WARN")
    return False


# ============================================================
# D. 冒烟验收
# ============================================================
def smoke_test(project_root: Path) -> bool:
    ok = True

    # ---- D1. 依赖能否 import ----
    for mod, label in [
        ("fastmcp", "fastmcp"),
        ("skillbridge", "skillbridge"),
        ("fitz", "PyMuPDF"),
        ("pdfplumber", "pdfplumber"),
    ]:
        try:
            __import__(mod)
            log(f"import {label}  OK")
        except Exception as exc:
            log(f"import {label}  失败：{exc}", "ERR")
            ok = False

    if not ok:
        return False

    # ---- D2. MCP 工具总数 ----
    sys.path.insert(0, str(project_root))
    try:
        from mcp_server.server import mcp  # type: ignore
    except Exception as exc:
        log(f"导入 mcp_server.server 失败：{exc}", "ERR")
        return False

    # fastmcp 4.x: asyncio.run(mcp._list_tools()) -> list[Tool]
    count = None
    try:
        import asyncio

        count = len(asyncio.run(mcp._list_tools()))  # type: ignore[attr-defined]
    except Exception:
        tm = getattr(mcp, "_tool_manager", None)
        tools = getattr(tm, "_tools", None)
        if isinstance(tools, (dict, list, tuple)):
            count = len(tools)

    if count:
        log(f"MCP 工具总数：{count}（当前版本期望 28）", "OK")
        if count != 28:
            log("  数字对不上多半是仓库版本旧，不是装坏了；", "WARN")
            log("  到仓库根 git pull 拉最新代码后重连客户端。", "WARN")
    else:
        log("读不到工具总数（不阻塞部署，请重连客户端后自行确认）。", "WARN")

    # ---- D3. 离线自检（本地开发机才有 Tools/） ----
    for script, expect in [
        ("selfcheck_geometry.py", None),
        ("selfcheck_skill_syntax.py", None),
    ]:
        p = project_root / "Tools" / script
        if not p.is_file():
            continue
        proc = subprocess.run(
            [str(Path(sys.executable)), str(p)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        tail = lines[-2:] if lines else ["(无输出)"]
        if proc.returncode == 0:
            log(f"{script} 通过", "OK")
        else:
            log(f"{script} 失败（rc={proc.returncode}）", "ERR")
            ok = False
        for ln in tail:
            log(f"    {ln.strip()}")

    return ok


# ============================================================
# main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="layout-forge-mcp Agent 侧一键配置")
    ap.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT),
                    help="项目根目录（默认：本文件所在目录的上一级）")
    ap.add_argument("--pcbenv", default=None,
                    help="手动指定 Allegro 的 pcbenv 目录")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    ap.add_argument("--no-mcp", action="store_true", help="跳过 MCP 注册")
    ap.add_argument("--no-skills", action="store_true", help="跳过 Skill 安装")
    ap.add_argument("--no-skillbridge", action="store_true", help="跳过 SkillBridge 配置")
    ap.add_argument("--no-smoke", action="store_true", help="跳过冒烟验收")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    venv_python = Path(sys.executable)

    print("=" * 62)
    print(" layout-forge-mcp  一键配置")
    print("=" * 62)
    print(f"  项目根   : {project_root}")
    print(f"  venv py  : {venv_python}")
    print(f"  dry-run  : {args.dry_run}")
    print()

    if not (project_root / "pyproject.toml").is_file():
        log(f"{project_root} 里没有 pyproject.toml，不像项目根。", "ERR")
        return 1

    results: dict[str, bool] = {}

    if not args.no_mcp:
        hr("A. 注册 MCP")
        results["mcp"], cfg_path, client = write_mcp_config(
            project_root, venv_python, args.dry_run
        )
        results["mcp"] = True

    if not args.no_skills:
        hr("B. 安装 Skill")
        install_skills(args.dry_run)

    if not args.no_skillbridge:
        hr("C. 配置 Allegro SkillBridge 自动加载")
        install_skillbridge(project_root, args.pcbenv, args.dry_run)

    if not args.no_smoke:
        hr("D. 冒烟验收")
        if args.dry_run:
            log("[DRY-RUN] 跳过冒烟", "WARN")
        else:
            results["smoke"] = smoke_test(project_root)

    hr("收尾")
    print("  下一步：")
    print("    1. 完全退出并重启你的 Agent 客户端（MCP 是长驻进程，必须重启才加载）")
    print("    2. 客户端里确认 layout-forge-mcp 已连接，工具数为 28")
    print("    3. 启动 Allegro PCB Editor，命令行执行 netstat -ano | findstr 7777")
    print("       有 LISTENING 说明 SkillBridge 通道已通")
    print()
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
