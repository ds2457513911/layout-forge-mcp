# -*- coding: utf-8 -*-
"""收尾复查 —— 一条命令确认 Allegro 相关的东西都收干净了。

背景（2026-10-08 真实事故）：
    只跑了 stop_allegro.py 关掉 Allegro **进程**，忘了 TaskStop 掉那条
    "后台挂住" 的 shell。Allegro 是干净了，但后台任务又空转了 29 分钟，
    WorkBuddy 界面一直挂着 "1 个后台任务运行中" —— 用户看到的就是"活没干完"。

本脚本查四项：
    1) 7777 端口是否已释放
    2) allegro.exe / pythonw.exe（SkillBridge 服务）是否还有残留
    3) 项目里有没有孤儿 *.dra.lck
    4) 项目根有没有我留下的 *.log / *.out / 探针脚本（workspace-hygiene 规则）

★ 注意：**后台任务列表本脚本查不了**（那是宿主的能力，不是 OS 层的进程）。
  只能用 `TaskStop(task_id="<随便一个不存在的 ID>")` 去列 —— 脚本最后会提醒。

用法：
    python preflight_check.py                          # 以当前目录当项目根
    python preflight_check.py --project <项目目录>
    python preflight_check.py --port 7777

退出码：0 = 干净；1 = 有残留（需要处理）。
不依赖任何第三方包，任意 Python 3.8+ 可跑。
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path

# ---- 输出小工具（避免依赖 colorama）----
OK = "[ OK ]"
BAD = "[WARN]"
INFO = "[info]"

_issues: list[str] = []


def _say(tag: str, text: str) -> None:
    print(f"{tag} {text}")


def _decode(raw: bytes) -> str:
    """Windows 上 tasklist 的输出是 GBK；UTF-8 环境则可能是 UTF-8。
    两种都试，最后兜底 replace —— 直接把 bytes 当 str 会乱码。"""
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, shell=False)
        return _decode(p.stdout or b"")
    except (FileNotFoundError, OSError):
        return ""


# ---------------------------------------------------------------- 1) 端口
def check_port(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        listening = True
    except (ConnectionRefusedError, socket.timeout, OSError):
        listening = False
    finally:
        s.close()

    if listening:
        _issues.append(f"端口 {port} 仍在监听（Allegro/SkillBridge 还活着）")
        _say(BAD, f"端口 {port}: 仍在 LISTENING —— Allegro 侧服务没退干净")
    else:
        _say(OK, f"端口 {port}: 已释放，无监听")


# ---------------------------------------------------------------- 2) 进程
def check_processes() -> None:
    for exe in ("allegro.exe", "pythonw.exe"):
        out = run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"])
        # 没进程时 tasklist 会回 "INFO: No tasks are running which match..."
        hit = exe.lower() in out.lower()
        if hit:
            lines = [l.strip() for l in out.splitlines() if exe.lower() in l.lower()]
            _issues.append(f"{exe} 仍有残留进程")
            _say(BAD, f"{exe}: 仍有残留 -> {len(lines)} 个")
            for l in lines[:5]:
                _say("      ", l)
        else:
            _say(OK, f"{exe}: 无残留")


# ---------------------------------------------------------------- 3) 孤儿锁
def _walk_pruned(root: Path):
    """遍历但剪掉 .venv / .git / .trash / __pycache__ —— 否则又慢又假阳性。"""
    skip = {".venv", ".git", ".trash", "__pycache__", "node_modules", ".idea"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for f in filenames:
            yield Path(dirpath) / f


def check_locks(project: Path) -> None:
    locks = [p for p in _walk_pruned(project)
             if p.name.lower().endswith((".dra.lck", ".brd.lck", ".lck"))]
    if locks:
        _issues.append(f"孤儿锁文件 {len(locks)} 个")
        _say(BAD, f"孤儿 .lck: {len(locks)} 个（会让下次启动弹「File Exists」框，堵死通道）")
        for p in locks[:10]:
            _say("      ", str(p))
        _say("      ", "→ 处理：mklink 到 .trash 或让用户确认后删，别直接 rm")
    else:
        _say(OK, "孤儿 .lck: 无")


# ---------------------------------------------------------------- 4) 项目根污染
POLLUTE_SUFFIX = (".log", ".out", ".err", ".bak", ".tmp")
POLLUTE_PREFIX = ("probe_", "tmp_", "scratch_", "debug_")


def check_pollution(project: Path) -> None:
    if not project.is_dir():
        _say(INFO, f"项目目录不存在，跳过污染检查: {project}")
        return

    bad = []
    for entry in project.iterdir():
        if not entry.is_file():
            continue
        n = entry.name.lower()
        # 项目自己约定的产物目录不放根目录，这里只看根
        if n.endswith(POLLUTE_SUFFIX) or n.startswith(POLLUTE_PREFIX):
            bad.append(entry)
        # Allegro / extracta 的轮转档 extract.log,1 .. ,3
        elif ".log," in n or ".out," in n:
            bad.append(entry)

    if bad:
        _say(INFO, f"项目根疑似运行痕迹: {len(bad)} 个"
                   "（其中可能有项目自己就认的 extract.log*，请自行判断）")
        for p in bad[:15]:
            try:
                kb = p.stat().st_size / 1024
                _say("      ", f"{p.name}  ({kb:.1f} KB)")
            except OSError:
                _say("      ", p.name)
    else:
        _say(OK, "项目根: 无 *.log / *.out / 探针脚本残留")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="收尾复查：确认 Allegro 相关状态是否干净")
    ap.add_argument("--project", default=os.getcwd(),
                    help="要检查污染的项目根目录（默认当前目录）")
    ap.add_argument("--port", type=int, default=7777,
                    help="SkillBridge 端口（默认 7777）")
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()

    print("=" * 68)
    print("收尾复查 preflight_check")
    print(f"项目根: {project}")
    print("=" * 68)

    check_processes()
    check_port(args.port)
    check_locks(project)
    check_pollution(project)

    print("-" * 68)
    print("★ 后台任务列表（本脚本查不到，必须你手动确认）")
    print("   TaskStop(task_id=\"__list__\")     # 传一个不存在的 ID")
    print("   -> There are no running background tasks.   ← 干净")
    print("   -> Running tasks: <id> (Bash: ...)          ← 还有活的，继续收")
    print("-" * 68)

    if _issues:
        print(f"\n结论: 还有 {len(_issues)} 项没收干净 ——")
        for i, x in enumerate(_issues, 1):
            print(f"  {i}. {x}")
        return 1

    print("\n结论: OS 层已干净（记得再确认一次后台任务列表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
