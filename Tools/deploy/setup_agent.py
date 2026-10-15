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
import hashlib
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
DEFAULT_PROJECT_ROOT = DEPLOY_DIR.parent.parent

#: ★ 部署记录（「上次我部署的是哪份内容」）——**不是真源**。
#:   真源永远是 `deploy\skills\**`；本文件只用来区分两种「客户端和仓库不一致」：
#:     · STALE   = 仓库改了、客户端还是上次部署的旧内容（重跑部署即可）
#:     · DIVERGED= 客户端那份被人手改过（仓库没变）——**这才是静默回退的病灶**
#:   放 `Tools\deploy\` 而不是 tools 之外：`Tools/` 在 .gitignore 里，本文件是
#:   「本机的部署状态」，不该进版本库（换台机器应当重新生成）。
MANIFEST_PATH = DEPLOY_DIR / ".deploy_manifest.json"
MANIFEST_SCHEMA = "layout-forge-deploy-manifest/1"

#: 客户端给 skill 写的元数据：**仓库副本故意不带**，比对/覆盖时必须忽略或保留。
USER_META_NAME = "_user_meta.json"


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

    选法：先找**已存在**的配置文件（按优先级）；一个都没有时，
    默认在 WorkBuddy 固定路径新建 ~/.workbuddy/mcp.json。
    ★ 空电脑（客户端还没装过）也直接写这个路径：目录由 write_mcp_config
      创建，客户端装好后启动即读取，不需要等客户端先装好。
      （旧行为：退化成生成 deploy\mcp-config.json 让用户手工合并，
       违背"空电脑一键部署"目标，2026-10-14 删除。）
    """
    home = Path.home()
    for name, path in candidate_config_files():
        if path.exists():
            return name, path, True

    return "WorkBuddy", home / ".workbuddy" / "mcp.json", False


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

    if existed:
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
# B. 安装 Skill（+ 部署记录 / 静默回退校验，2026-10-15 Phase 3）
# ============================================================
def skill_landings() -> list[tuple[str, Path]]:
    """SKILL 的**三个落点**（2026-10-14 实测：后两个常是 junction）。

    详见 `README.md` 坑 13 —— 只部署 `.workbuddy` 不会刷新 `.dsh` 视图，
    而模型实际读的可能是 `.dsh` 那份。
    """
    home = Path.home()
    return [
        ("workbuddy", home / ".workbuddy" / "skills"),
        ("skills-manager", home / ".skills-manager" / "skills"),
        ("dsh", home / ".dsh" / "skills"),
    ]


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root: Path) -> dict:
    """目录内容指纹：{相对路径: sha256}。`_user_meta.json` 不计入（客户端私有）。"""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == USER_META_NAME:
            continue
        try:
            out[p.relative_to(root).as_posix()] = _sha256_file(p)
        except OSError:
            continue
    return out


def _link_target(p: Path) -> Path | None:
    """是软链接/junction 就回目标真身，否则 None。

    ★ 3.10 上 `Path.is_symlink()` 对 Windows **junction** 的判定不可靠，
      所以再用 `realpath` 兜一层（junction 与目标路径不同名）。
    """
    try:
        if p.is_symlink():
            return Path(os.path.realpath(p))
    except OSError:
        pass
    try:
        real = Path(os.path.realpath(p))
    except OSError:
        return None
    if os.path.normcase(str(real)) != os.path.normcase(str(p)):
        return real
    return None


def _diff_hashes(a: dict, b: dict) -> list[str]:
    """返回「不一致的文件名」列表（含只在一侧存在的）。"""
    return sorted(set(a) ^ set(b) | {k for k in set(a) & set(b) if a[k] != b[k]})


def mirror_skill(src: Path, dst: Path, dry_run: bool, backup_root: Path) -> dict:
    """把真源 skill 目录**覆盖写进** `dst`（保留 `_user_meta.json`）。

    ★ 与旧实现的区别（2026-10-15）：旧版用 `shutil.move` 把已存在的目标整体
      移成 `<名字>.bak_<时间戳>`，这在 junction 落点上会把**链接本身**移走、
      换成一个真目录 —— 正是「三个落点各自陈旧」的根因（README 坑 13）。
      现在改为「**写在真身上**、逐文件覆盖」，链接关系保持不变。

    :return: {"written": n, "extra": [...], "backup": str|None}
    """
    src_files = hash_tree(src)
    dst_files = hash_tree(dst)
    extra = sorted(set(dst_files) - set(src_files))

    backup: Path | None = None
    # 目标里有真源没有的文件 = 可能是**手改过**的内容：先留一份再覆盖。
    if extra:
        backup = backup_root / f"{dst.name}.bak_{ts()}"
        for rel in extra:
            bp = backup / rel
            bp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst / rel, bp)

    saved_meta: bytes | None = None
    meta_path = dst / USER_META_NAME
    if meta_path.is_file():
        saved_meta = meta_path.read_bytes()

    written = 0
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
        for rel in sorted(src_files):
            if dst_files.get(rel) == src_files[rel]:
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, target)
            written += 1
        if saved_meta is not None:
            meta_path.write_bytes(saved_meta)
    return {"written": written, "extra": extra,
            "backup": str(backup) if backup else None}


def install_skills(dry_run: bool) -> list[Path]:
    """把 `deploy\\skills\\*` 同步到**每个落点的真身**（去重，junction 只写一次）。

    :return: 实际写入过的真身目录列表（供部署记录使用）
    """
    src_root = DEPLOY_DIR / "skills"
    if not src_root.is_dir():
        log(f"未找到 {src_root}，跳过 Skill 安装。", "WARN")
        return []

    landing_roots = skill_landings()
    backup_root = DEPLOY_DIR / "skills_backup"
    touched: list[Path] = []
    touched_keys: set[str] = set()

    for src in sorted(src_root.iterdir()):
        if not src.is_dir():
            continue
        for label, root in landing_roots:
            dst = root / src.name
            # 落点不存在：只有 .workbuddy 是「必须新建」（空电脑也要能一键到底）
            if not dst.exists() and label != "workbuddy":
                continue
            real = _link_target(dst)
            if real is not None:
                # junction：写到它的真身（一次写入同时刷新所有指向它的落点）
                dst_real = real
                log(f"{label}: {dst.name} 是链接 → 真身 {dst_real}")
            else:
                dst_real = dst
            key = os.path.normcase(str(dst_real))
            if key in touched_keys:
                continue
            touched_keys.add(key)
            if dry_run:
                log(f"[DRY-RUN] 将同步 {src.name} -> {dst_real}（{label}）", "WARN")
                touched.append(dst_real)
                continue
            info = mirror_skill(src, dst_real, dry_run, backup_root)
            touched.append(dst_real)
            msg = f"Skill 已同步：{dst_real}（{label}，写 {info['written']} 个文件）"
            log(msg, "OK")
            if info["extra"]:
                log(f"  ⚠ 目标里有真源没有的文件（可能是手改），已另存：{info['backup']}", "WARN")
            if (dst_real / USER_META_NAME).is_file():
                log("  保留原 _user_meta.json")
    return touched


# ============================================================
# B2. 部署记录 + 防静默回退校验（Phase 3）
# ============================================================
def read_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        return {}
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_deploy_manifest(written_roots: list[Path], project_root: Path) -> Path:
    """记录「本次部署的每个落点写入了什么内容」。

    ★ 只记 `deploy\\skills\\**` 的 hash（真源快照）+ 每个落点写入后的实况，
      这样下次就能把「仓库前进」与「客户端被手改」区分开。
    """
    src_root = DEPLOY_DIR / "skills"
    skills: dict[str, dict] = {}
    for src in sorted(src_root.iterdir()):
        if src.is_dir():
            skills[src.name] = hash_tree(src)

    landings: dict[str, dict] = {}
    for label, root in skill_landings():
        for name in skills:
            dst = root / name
            if not dst.exists():
                continue
            real = _link_target(dst)
            obs_root = real if real is not None else dst
            landings[f"{label}:{name}"] = {
                "path": str(dst),
                "real": str(obs_root),
                "junction": real is not None,
                "files": hash_tree(obs_root),
            }

    data = {
        "schema": MANIFEST_SCHEMA,
        "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "project_root": str(project_root),
        "source": "Tools/deploy/skills",
        "source_hashes": skills,
        "landings": landings,
        "written_real_roots": [str(p) for p in written_roots],
    }
    if not DEPLOY_DIR.is_dir():
        DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    log(f"部署记录已写：{MANIFEST_PATH}", "OK")
    return MANIFEST_PATH


def check_skill_declarations(src_root: Path) -> list[str]:
    """软校验：每份 SKILL.md 头部是否声明了可追溯信息（Phase 3 §7）。

    只 WARN，不影响退出码：声明是「给人/给下次的自己」的溯源线索，
    不是运行期依赖。
    """
    problems: list[str] = []
    for skill in sorted(src_root.iterdir()) if src_root.is_dir() else []:
        md = skill / "SKILL.md"
        if not md.is_file():
            continue
        head = md.read_text(encoding="utf-8", errors="replace")
        # ★ 只看 front-matter 区（首个 --- 到第二个 ---），不要用固定字符窗口：
        #   有 skill 的 description 长达 2.5 KB，把 metadata 挤到 2000 字符之外，
        #   固定窗口会「明明声明了却报缺失」（2026-10-15 实测踩到）。
        fm_end = head.find("\n---", 3)
        block = head[:fm_end] if fm_end > 0 else head[:4000]
        missing = [k for k in ("mcp_min_commit", "verified_with", "eval_baseline")
                   if k not in block]
        if missing:
            problems.append(f"{skill.name}: SKILL.md 头部缺声明 {'、'.join(missing)}")
    return problems


def verify_skills() -> int:
    """★ 防静默回退：比对 `deploy\\skills\\**`（真源）与三个落点（读侧副本）。

    判定：
      MATCH    一致 —— 模型读到的就是仓库里的规则
      STALE    落点内容 == 上次部署记录，但仓库已前进 → **重跑部署**即可
      DIVERGED 落点内容既不是仓库现状、也不是上次部署的记录 → **有人手改过**
               （最危险：仓库与客户端各说各话，看的是哪份取决于客户端）
      MISSING  落点根存在、但里面没有这份 skill
      N/A      落点根整个不存在（该客户端没装）—— 不计入失败
    :return: 0 = 全部 MATCH / N/A；1 = 存在 STALE / DIVERGED / MISSING
    """
    src_root = DEPLOY_DIR / "skills"
    manifest = read_manifest()
    man_landings = manifest.get("landings") or {}

    hr("E. 防静默回退校验（SKILL 三落点 vs 真源）")
    log(f"真源：{src_root}")
    if manifest:
        log(f"部署记录：{manifest.get('deployed_at')}（{MANIFEST_PATH.name}）")
    else:
        log("没有部署记录 —— 只能判「一致 / 不一致」，无法区分 STALE 与 DIVERGED。", "WARN")

    bad = 0
    for skill in sorted(src_root.iterdir()) if src_root.is_dir() else []:
        if not skill.is_dir():
            continue
        truth = hash_tree(skill)
        log(f"· {skill.name}（{len(truth)} 个文件）")
        for label, root in skill_landings():
            if not root.is_dir():
                log(f"    [{label}] N/A（落点不存在：{root}）")
                continue
            dst = root / skill.name
            rec = man_landings.get(f"{label}:{skill.name}") or {}
            if not dst.exists():
                log(f"    [{label}] 缺失 → {dst}", "WARN")
                bad += 1
                continue
            real = _link_target(dst)
            obs_root = real if real is not None else dst
            obs = hash_tree(obs_root)
            note = f"（链接 → {obs_root}）" if real is not None else ""
            if obs == truth:
                log(f"    [{label}] MATCH{note}", "OK")
                continue
            diff = _diff_hashes(truth, obs)
            if rec.get("files") and obs == rec.get("files"):
                verdict = "STALE（落后于仓库，重跑部署）"
            else:
                verdict = "DIVERGED（客户端那份与仓库/上次部署都不同 → 先查是谁改的）"
            bad += 1
            log(f"    [{label}] {verdict}{note}  差异 {len(diff)} 个文件", "ERR")
            for rel in diff[:5]:
                log(f"        · {rel}")
            if len(diff) > 5:
                log(f"        … 另有 {len(diff) - 5} 个")

    for problem in check_skill_declarations(src_root):
        log(f"声明缺失（WARN）：{problem}", "WARN")

    if bad:
        log(f"不通过：{bad} 处落点与真源不一致 —— **模型读的规则可能不是仓库里的规则**。", "ERR")
        log("处理：`setup_agent.py --sync-skills`（把真源同步到三个落点）后重跑本校验。", "WARN")
        return 1
    log("通过：已安装的落点都与真源一致。", "OK")
    return 0


def sync_skills(project_root: Path) -> int:
    """把真源同步到三个落点（junction 写一次真身），随后复核。"""
    hr("E. 同步 SKILL 到三个落点")
    written = install_skills(dry_run=False)
    if not written:
        log("没有可写的落点。", "WARN")
    write_deploy_manifest(written, project_root)
    return verify_skills()


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

    # 当前版本的期望值（2026-10-13：31 → 34，新增读规格书三件套；
    #                2026-10-14：34 → 36，新增 folder_preflight / get_run_stats）
    EXPECTED_TOOLS = 36

    if count:
        log(f"MCP 工具总数：{count}（当前版本期望 {EXPECTED_TOOLS}）", "OK")
        if count < EXPECTED_TOOLS:
            log(f"  少了 {EXPECTED_TOOLS - count} 个，多半是仓库版本旧，不是装坏了；", "WARN")
            log("  到仓库根 git pull 拉最新代码后重连客户端。", "WARN")
        elif count > EXPECTED_TOOLS:
            log("  比期望多 —— 可能是你本地加了新工具，更新本脚本的 EXPECTED_TOOLS。", "WARN")
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
        # ★ 子进程 stdout 必须强制 UTF-8：同事机器控制台是 cp936，selfcheck
        #   总结行里的 "✓" 用 GBK 编码不了 -> print 抛 UnicodeEncodeError ->
        #   rc=2（2026-10-14 部署实锤，而且 stderr 里才有 traceback）。
        _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [str(Path(sys.executable)), str(p)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env,
        )
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        tail = lines[-2:] if lines else ["(无输出)"]
        if proc.returncode == 0:
            log(f"{script} 通过", "OK")
        else:
            log(f"{script} 失败（rc={proc.returncode}）", "ERR")
            err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
            for ln in err_lines[-3:]:
                log(f"    [stderr] {ln.strip()}")
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
    ap.add_argument("--verify-skills", action="store_true",
                    help="只做防静默回退校验：比对 deploy\\skills 与三个落点，然后退出")
    ap.add_argument("--sync-skills", action="store_true",
                    help="只把 deploy\\skills 同步到三个落点，然后校验")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    venv_python = Path(sys.executable)

    # ★ 两个「只干这一件事」的入口：不重复跑 MCP/冒烟，装完就能单独复核。
    if args.verify_skills:
        return verify_skills()
    if args.sync_skills:
        return sync_skills(project_root)

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
        ok, cfg_path, client = write_mcp_config(
            project_root, venv_python, args.dry_run
        )
        results["mcp"] = ok
        log(f"配置目标：{cfg_path}（{client}）")

    if not args.no_skills:
        hr("B. 安装 Skill（三个落点）")
        written = install_skills(args.dry_run)
        if args.dry_run:
            log("[DRY-RUN] 跳过部署记录", "WARN")
        else:
            write_deploy_manifest(written, project_root)

    if not args.no_skillbridge:
        hr("C. 配置 Allegro SkillBridge 自动加载")
        install_skillbridge(project_root, args.pcbenv, args.dry_run)

    if not args.no_smoke:
        hr("D. 冒烟验收")
        if args.dry_run:
            log("[DRY-RUN] 跳过冒烟", "WARN")
        else:
            results["smoke"] = smoke_test(project_root)

    # ★ E：装完必须自证「三个落点都读得到同一份规则」——这是防静默回退的
    #   最后一道闸；不一致就让一键部署**失败**，而不是留下一个说不清的现场。
    if not args.no_skills and not args.dry_run:
        results["skills_sync"] = verify_skills() == 0

    hr("收尾")
    print("  下一步：")
    print("    1. 完全退出并重启你的 Agent 客户端（MCP 是长驻进程，必须重启才加载）")
    print("    2. 客户端里确认 layout-forge-mcp 已连接，工具数为 36")
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
