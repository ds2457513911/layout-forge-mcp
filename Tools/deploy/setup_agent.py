# -*- coding: utf-8 -*-
r"""
setup_agent.py —— layout-forge-mcp 的「Agent 侧」一键配置

由 deploy.bat 在最后一步调用，也可以单独跑：

    .venv\Scripts\python.exe deploy\setup_agent.py
    .venv\Scripts\python.exe deploy\setup_agent.py --dry-run
    .venv\Scripts\python.exe deploy\setup_agent.py --pcbenv "D:/Cadence/SPB_Data/pcbenv"
    .venv\Scripts\python.exe deploy\setup_agent.py --no-skillbridge

做六件事：
    A. 注册 MCP        —— 写客户端的 mcp.json（venv python + -c 注入 sys.path）
    B. 安装 Skill      —— deploy\skills\* 复制到 landings.json 声明的每个**在场**落点
    C. 配 SkillBridge  —— 调 Tools/install_skillbridge.py --non-interactive
    D. 冒烟验收        —— import 依赖 + 读 MCP 工具总数
    E. 落点校验        —— verify_skills()：真源 vs 每个在场落点，不一致即失败
    F. 契约断言        —— verify_contract()：运行期工具表 vs 源码 AST 数 / 冻结基线

设计约束（★ 都是踩过的坑，改之前先读）：
    1. MCP 的 command 必须是项目 .venv 里的 python.exe。skillbridge 只装在
       项目 venv 里，系统 Python / 托管 Python 都 import 不到。
    2. args 必须用 -c 注入 sys.path。客户端拉起 MCP 时 cwd 不确定，
       不注入会 ModuleNotFoundError: mcp_server。
    3. 写 mcp.json 是「合并」不是「覆盖」——用户可能已经配了别的 server。
       写之前先备份成 <原名>.bak_<时间戳>。
    4. 全程非交互：部署脚本会被双击运行，任何 input() 都会把窗口卡死。
    5. 落点不靠猜（票 #4 / #8）：落点写在机器本地的 deploy\landings.json，客户端
       在不在场要看证据（配置文件里注册了 layout-forge-mcp），零落点直接失败。
       默认用户目录走 SHGetFolderPathW(CSIDL_PROFILE)，**不用** Path.home()——
       本机 HOME / USERPROFILE 被 Cadence 改成了 SPB_Data。

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
#: ★ /2（2026-11-19，票 #8）：landings 的键从「落点 label」换成 landings.json 里的 id。
#:   读到 /1 记录就整体作废（键对不上，宁可判 UNVERIFIABLE 也不误判 STALE），
#:   跑一次 --sync-skills 重建。
MANIFEST_SCHEMA = "layout-forge-deploy-manifest/2"

#: 客户端给 skill 写的元数据：**仓库副本故意不带**，比对/覆盖时必须忽略或保留。
USER_META_NAME = "_user_meta.json"

#: 工具契约：冻结基线（只由 `check_tool_contract.py --update` 写，**不是产地**）
CONTRACT_BASELINE = Path("eval") / "baselines" / "tool_contract.json"
#: 契约套件目录（F 段复用它唯一的 AST 解析器 `tool_catalog.parse_tools`）
CONTRACT_SUITE = Path("eval") / "suites" / "tool_contract"


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
# 本机事实：真实用户目录（**不读环境变量**）
# ============================================================
def real_profile() -> Path:
    """真实用户目录 —— Windows 走 SHGetFolderPathW(CSIDL_PROFILE)。

    ★ 不能用 `Path.home()`：本机 HOME / USERPROFILE 被 Cadence 覆盖成
      C:/Software/Cadence/SPB_Data，跟着它走会把 mcp.json 和技能落点全拼到
      那里去（2026-11-19 实测；票 #4 定案 D2）。
      SHGetFolderPathW 读的是 shell 的「用户配置」目录，与进程环境变量无关。
    """
    if sys.platform != "win32":
        return Path.home()
    import ctypes

    buf = ctypes.create_unicode_buffer(260)          # MAX_PATH
    # CSIDL_PROFILE = 0x0028, SHGFP_TYPE_CURRENT = 0
    hresult = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0028, None, 0, buf)
    if hresult == 0 and buf.value:
        return Path(buf.value)
    log(f"SHGetFolderPathW(CSIDL_PROFILE) 失败（HRESULT={hresult}）；"
        f"不退回 Path.home()——那会拿到被 Cadence 覆盖的目录。", "ERR")
    raise SystemExit(1)


# ============================================================
# A. 注册 MCP
# ============================================================
def candidate_config_files() -> list[tuple[str, Path]]:
    """按优先级列出「已知客户端」的配置文件。"""
    home = real_profile()
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
      （旧行为：退化成生成 deploy\\mcp-config.json 让用户手工合并，
       违背"空电脑一键部署"目标，2026-10-14 删除。）
    """
    home = real_profile()
    for name, path in candidate_config_files():
        if path.exists():
            return name, path, True

    return "WorkBuddy", home / ".workbuddy" / "mcp.json", False


def default_venv_python(project_root: Path) -> Path:
    """MCP 配置里该写的解释器。

    ★ 默认必须是**项目 .venv**，不能是 `sys.executable`：
    本脚本可能被工作区外的解释器跑（沙箱绕过 / 临时 venv / 系统 python），
    那种解释器路径写进配置后，一旦那个目录被清理，MCP 就会静默起不来。
    (2026-10-16 实测：用缓存区 venv 跑本脚本，mcp.json 被写成了缓存区路径。)
    """
    for candidate in (
        project_root / ".venv" / "Scripts" / "python.exe",   # Windows
        project_root / ".venv" / "bin" / "python",           # POSIX
    ):
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


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
#: ★ 落点 = 「模型真的会读 SKILL 的客户端目录」。不猜、不枚举。
#:   清单不存在时按本机已知客户端生成一次，之后以清单为准（改清单 = 改这台机器
#:   的部署契约）。为什么改成声明式（票 #4 D1/D2、票 #8）：旧版用 Path.home() 拼
#:   三个落点，在本机全拼错；而「拼错」恰好落进 N/A 分支静默通过 —— 闸门是绿的，
#:   模型读的却是一份没人更新过的旧 SKILL。清单随 .deploy_manifest.json 一起 gitignore。
LANDINGS_PATH = DEPLOY_DIR / "landings.json"
LANDINGS_SCHEMA = "layout-forge-landings/1"


def dsh_config_candidates(profile: Path) -> list[Path]:
    """DSH 每条 profile 一份连接器补丁；注册 MCP 就落在那里。"""
    root = profile / ".dsh" / "profiles"
    found = sorted(root.glob("*/cordis.patch.yml")) if root.is_dir() else []
    return found or [root / "web" / "cordis.patch.yml"]


def default_landings(profile: Path) -> list[dict]:
    """本机首次生成清单：只写**已知会读 SKILL 的客户端**。

    中央技能库（.skills-manager）不在此列：它是库、不是读侧 —— 往那里写不进
    任何一个模型的上下文，旧版把它算作落点只会制造「看起来已部署」的假象。
    """
    return [
        {
            "id": "workbuddy",
            "client": "WorkBuddy",
            "skills_dir": str(profile / ".workbuddy" / "skills"),
            "require": {
                "kind": "mcp_config",
                "path": str(profile / ".workbuddy" / "mcp.json"),
                "server": SERVER_NAME,
            },
        },
        {
            "id": "dsh",
            "client": "DeepSeek Harness",
            "skills_dir": str(profile / ".dsh" / "skills"),
            "require": {
                "kind": "mcp_config",
                "paths": [str(p) for p in dsh_config_candidates(profile)],
                "server": SERVER_NAME,
            },
        },
    ]


def _mcp_registered(path: Path, server: str) -> bool | None:
    """配置文件里注册了这个 MCP 吗？None = 配置文件不存在。"""
    if not path.is_file():
        return None
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        return isinstance(servers, dict) and server in servers
    try:
        return server in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def evaluate_requirement(req: dict | None) -> tuple[bool, list[str]]:
    """客户端在不在场 —— 返回 (是否在场, 逐条证据)。

    ★ 「不在场」必须带着证据说，而不是「目录不存在 → N/A」了事（票 #4 D3）：
      证据是给人看的，判错了才能一眼看出错在哪一条。
    """
    req = req or {}
    kind = req.get("kind", "always")
    if kind == "always":
        return True, ["清单/参数里显式声明（always）"]
    if kind == "dir":
        p = Path(req["path"])
        ok = p.is_dir()
        return ok, [f"{'有' if ok else '没有'}目录 {p}"]
    if kind == "mcp_config":
        raw = req.get("paths")
        paths = [Path(x) for x in raw] if raw else [Path(req["path"])]
        server = req.get("server", SERVER_NAME)
        evidence: list[str] = []
        present = False
        for p in paths:
            hit = _mcp_registered(p, server)
            if hit is None:
                evidence.append(f"没有配置文件 {p}")
            elif hit:
                present = True
                evidence.append(f"{p} 里注册了 {server}")
            else:
                evidence.append(f"{p} 存在但没有注册 {server}")
        return present, evidence
    raise ValueError(f"未知的 require.kind：{kind!r}（支持 always / dir / mcp_config）")


def load_landings(*, write_if_missing: bool = True) -> list[dict]:
    """读机器本地落点清单；不存在时按本机已知客户端生成一份。

    :raises SystemExit: 清单坏了（宁可停，也不要拿半份清单去判）
    """
    if LANDINGS_PATH.is_file():
        try:
            data = json.loads(LANDINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"落点清单读不了：{LANDINGS_PATH}（{exc}）", "ERR")
            raise SystemExit(1)
    else:
        profile = real_profile()
        data = {
            "schema": LANDINGS_SCHEMA,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "profile": str(profile),
            "landings": default_landings(profile),
        }
        if not write_if_missing:
            log(f"[DRY-RUN] 将生成落点清单 {LANDINGS_PATH}", "WARN")
        else:
            LANDINGS_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log(f"本机还没有落点清单，已按已知客户端生成：{LANDINGS_PATH}", "OK")
            log("  ★ 这是**这台机器**的部署契约；换机 / 换客户端要改它。", "WARN")

    if not isinstance(data, dict) or data.get("schema") != LANDINGS_SCHEMA:
        bad = data.get("schema") if isinstance(data, dict) else data
        log(f"落点清单 schema 不符：{bad!r}（要 {LANDINGS_SCHEMA!r}）", "ERR")
        raise SystemExit(1)
    landings = data.get("landings")
    if not isinstance(landings, list) or not landings:
        log(f"落点清单里没有落点：{LANDINGS_PATH}", "ERR")
        raise SystemExit(1)

    seen: set[str] = set()
    for item in landings:
        if not isinstance(item, dict) or not item.get("id") or not item.get("skills_dir"):
            log(f"落点条目缺 id / skills_dir：{item!r}", "ERR")
            raise SystemExit(1)
        if item["id"] in seen:
            log(f"落点 id 重复：{item['id']}", "ERR")
            raise SystemExit(1)
        seen.add(item["id"])
        if not os.path.isabs(item["skills_dir"]):
            log(f"落点 {item['id']} 的 skills_dir 必须是绝对路径：{item['skills_dir']}", "ERR")
            raise SystemExit(1)
    return landings


def merge_landings(base: list[dict], extra: list[dict] | None) -> list[dict]:
    """清单 + 显式落点（同 id 覆盖，保持清单顺序）。"""
    merged = {item["id"]: item for item in base}
    for item in extra or []:
        merged[item["id"]] = item
    return list(merged.values())


def judge_landing(truth: dict, obs: dict | None, recorded: dict | None) -> str:
    """单个落点的六态判定（纯函数 —— selfcheck_landings.py 直接测它）。

    MATCH         落点内容 == 真源
    STALE         落点 == 上次部署记录，真源已前进（重跑 --sync-skills）
    DIVERGED      落点既不是真源也不是上次部署记录（有人手改过）
    UNVERIFIABLE  没有可用记录、又没对上真源（判不了，按失败处理）
    MISSING       落点目录里没有这份技能（obs 为 None）
    """
    if obs is None:
        return "MISSING"
    if obs == truth:
        return "MATCH"
    if recorded:
        return "STALE" if obs == recorded else "DIVERGED"
    return "UNVERIFIABLE"


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


def install_skills(dry_run: bool, extra_landings: list[dict] | None = None) -> list[Path]:
    """把 `deploy\\skills\\*` 同步到**每个在场落点**的真身（junction 只写一次）。

    ★ 落点来自 landings.json（+ --landing）；客户端不在场（没注册这个 MCP）的
      不写 —— 把技能塞进没装的客户端目录只会制造「已部署」的假象。
    :return: 实际写入过的真身目录列表（供部署记录使用）
    """
    src_root = DEPLOY_DIR / "skills"
    if not src_root.is_dir():
        log(f"未找到 {src_root}，跳过 Skill 安装。", "WARN")
        return []

    landings = merge_landings(load_landings(write_if_missing=not dry_run), extra_landings)
    presence = {item["id"]: evaluate_requirement(item.get("require")) for item in landings}
    backup_root = DEPLOY_DIR / "skills_backup"
    touched: list[Path] = []
    touched_keys: set[str] = set()

    for src in sorted(src_root.iterdir()):
        if not src.is_dir():
            continue
        for item in landings:
            lid = item["id"]
            required, evidence = presence[lid]
            root = Path(item["skills_dir"])
            if not required:
                log(f"[{lid}] 客户端不在场，跳过：{'；'.join(evidence)}", "WARN")
                continue
            dst = root / src.name
            if not root.is_dir():
                # 客户端在场却还没有技能目录：建（旧版只给 .workbuddy 开这个口子）
                if dry_run:
                    log(f"[DRY-RUN] 将新建落点目录 {root}（{lid}）", "WARN")
                else:
                    root.mkdir(parents=True, exist_ok=True)
                    log(f"落点目录不存在、客户端在场 → 新建 {root}（{lid}）", "OK")
            real = _link_target(dst)
            if real is not None:
                # junction：写到它的真身（一次写入同时刷新所有指向它的落点）
                dst_real = real
                log(f"[{lid}] {dst.name} 是链接 → 真身 {dst_real}")
            else:
                dst_real = dst
            key = os.path.normcase(str(dst_real))
            if key in touched_keys:
                continue
            touched_keys.add(key)
            if dry_run:
                log(f"[DRY-RUN] 将同步 {src.name} -> {dst_real}（{lid}）", "WARN")
                touched.append(dst_real)
                continue
            info = mirror_skill(src, dst_real, dry_run, backup_root)
            touched.append(dst_real)
            msg = f"Skill 已同步：{dst_real}（{lid}，写 {info['written']} 个文件）"
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
    """记录「本次部署的每个在场落点写入了什么内容」。

    ★ 只记 `deploy\\skills\\**` 的 hash（真源快照）+ 每个落点写入后的实况，
      这样下次就能把「仓库前进」与「客户端被手改」区分开。
    ★ 键 = landings.json 的 id（schema /2）；manifest 只做哈希基线，**不枚举落点**
      （票 #4 D2：落点来源只有一个 —— 声明）。
    """
    src_root = DEPLOY_DIR / "skills"
    skills: dict[str, dict] = {}
    for src in sorted(src_root.iterdir()):
        if src.is_dir():
            skills[src.name] = hash_tree(src)

    landings: dict[str, dict] = {}
    for item in load_landings():
        lid = item["id"]
        if not evaluate_requirement(item.get("require"))[0]:
            continue
        root = Path(item["skills_dir"])
        for name in skills:
            dst = root / name
            if not dst.exists():
                continue
            real = _link_target(dst)
            obs_root = real if real is not None else dst
            landings[f"{lid}:{name}"] = {
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


def verify_skills(extra_landings: list[dict] | None = None, *,
                  src_root: Path | None = None,
                  landings: list[dict] | None = None,
                  manifest: dict | None = None) -> int:
    """★ 覆盖校验（2026-10-15 Phase 3；2026-11-19 按票 #8 重做）。

    比对 `deploy/skills/**`（真源）与 landings.json 里**每个在场落点**（读侧副本）：

      MATCH         一致 —— 模型读到的就是仓库里的规则
      STALE         落点 == 上次部署记录，但仓库已前进 → 重跑 --sync-skills
      DIVERGED      落点既不是仓库现状、也不是上次部署记录 → 有人手改过
      MISSING       客户端在场，但没有这份技能（或整个技能目录缺失）
      UNVERIFIABLE  没有可用记录、又没对上真源 → 判不了，按失败处理
      NOT_INSTALLED 客户端没接入这个 MCP（带证据）—— 不算失败

    ★ 退出码 0 要两条同时成立：没有任何 MISSING / STALE / DIVERGED /
      UNVERIFIABLE，且**至少一个 MATCH**。零落点 = 闸门没在守任何东西，必失败
      （票 #4 D1）。STALE 也判失败：#4 要求「已接入的客户端必须 MATCH」，落后于
      仓库同样意味着模型读的是旧规则。

    :return: 0 = 真绿；1 = 有落点不合格，或零落点
    """
    src_root = src_root or (DEPLOY_DIR / "skills")
    if landings is None:
        landings = load_landings()
    landings = merge_landings(landings, extra_landings)
    if manifest is None:
        manifest = read_manifest()
    schema = manifest.get("schema") if manifest else None
    if manifest and schema != MANIFEST_SCHEMA:
        log(f"部署记录是旧版 schema（{schema!r}），键对不上 —— 本次不作基线；"
            f"跑一次 --sync-skills 重建。", "WARN")
        manifest = {}
    manifest_landings = manifest.get("landings") or {}

    hr("E. 防静默回退校验（SKILL 落点 vs 真源）")
    log(f"真源：{src_root}")
    log(f"落点声明：{LANDINGS_PATH}（{len(landings)} 个）")
    if manifest:
        log(f"部署记录：{manifest.get('deployed_at')}（{MANIFEST_PATH.name}）")
    else:
        log("没有可用部署记录 —— 未对上的落点只能判 UNVERIFIABLE。", "WARN")

    presence: dict[str, tuple[bool, list[str]]] = {}
    for item in landings:
        lid = item["id"]
        present, evidence = evaluate_requirement(item.get("require"))
        presence[lid] = (present, evidence)
        log(f"  [{lid}] {item.get('client', lid)}：{'在场' if present else '不在场'}"
            f"（{len(evidence)} 处证据）")

    skills = sorted(x for x in src_root.iterdir() if x.is_dir()) if src_root.is_dir() else []
    if not skills:
        log(f"真源里没有 SKILL：{src_root}", "ERR")
        return 1

    counts = {k: 0 for k in ("MATCH", "STALE", "DIVERGED", "MISSING",
                             "UNVERIFIABLE", "NOT_INSTALLED")}
    bad = 0
    for skill in skills:
        truth = hash_tree(skill)
        log(f"· {skill.name}（{len(truth)} 个文件）")
        for item in landings:
            lid = item["id"]
            root = Path(item["skills_dir"])
            required, evidence = presence[lid]
            if not required:
                counts["NOT_INSTALLED"] += 1
                log(f"    [{lid}] NOT_INSTALLED（客户端没接入 {SERVER_NAME}）", "WARN")
                for line in evidence[:3]:
                    log(f"        证据：{line}")
                if len(evidence) > 3:
                    log(f"        … 另有 {len(evidence) - 3} 处配置同样没有注册")
                continue

            dst = root / skill.name
            recorded = (manifest_landings.get(f"{lid}:{skill.name}") or {}).get("files")
            note = ""
            obs: dict | None = None
            if not dst.exists():
                state = "MISSING"
            else:
                real = _link_target(dst)
                obs_root = real if real is not None else dst
                note = f"（链接 → {obs_root}）" if real is not None else ""
                obs = hash_tree(obs_root)
                state = judge_landing(truth, obs, recorded)
            counts[state] += 1

            if state == "MATCH":
                log(f"    [{lid}] MATCH{note}", "OK")
                continue
            bad += 1
            if state == "MISSING":
                log(f"    [{lid}] 缺失 → {dst}{note}", "ERR")
                for line in evidence[:3]:
                    log(f"        客户端在场证据：{line}")
                continue
            if state == "STALE":
                log(f"    [{lid}] STALE（落后于仓库，重跑 --sync-skills）{note}", "ERR")
            elif state == "DIVERGED":
                log(f"    [{lid}] DIVERGED（与仓库/上次部署都不同 → 先查是谁改的）{note}", "ERR")
            else:
                log(f"    [{lid}] UNVERIFIABLE（没有部署记录可对照，重跑 --sync-skills 建基线）{note}", "ERR")
            if obs is not None:
                diff = _diff_hashes(truth, obs)
                log(f"        差异 {len(diff)} 个文件")
                for rel in diff[:5]:
                    log(f"        · {rel}")
                if len(diff) > 5:
                    log(f"        … 另有 {len(diff) - 5} 个")

    for problem in check_skill_declarations(src_root):
        log(f"声明缺失（WARN）：{problem}", "WARN")

    summary = "、".join(f"{k} {v}" for k, v in counts.items() if v) or "无"
    log(f"判定汇总：{summary}")

    if bad:
        log(f"不通过：{bad} 处落点与真源不一致 —— **模型读的规则可能不是仓库里的规则**。", "ERR")
        log("处理：`setup_agent.py --sync-skills`（按清单同步到每个在场落点）后重跑本校验。", "WARN")
        return 1
    if counts["MATCH"] == 0:
        log(f"不通过：零落点 —— 没有任何客户端落点与真源一致（MATCH=0，闸门没在守东西）。", "ERR")
        log(f"处理：确认客户端已注册 {SERVER_NAME}（见清单里的 require 证据），或用 --landing 显式指定。", "WARN")
        return 1
    log(f"通过：{counts['MATCH']} 个在场落点与真源一致"
        f"（NOT_INSTALLED {counts['NOT_INSTALLED']} 个已按证据排除）。", "OK")
    return 0

def sync_skills(project_root: Path, dry_run: bool = False,
                extra_landings: list[dict] | None = None) -> int:
    """把真源同步到清单里的每个在场落点（junction 写一次真身），随后复核。

    ★ 2026-10-23 修复（--dry-run 空转缺陷）：旧版这里硬编码 dry_run=False，
      且 main 的 --sync-skills 分支不传参，导致 `--sync-skills --dry-run`
      **照样真写盘**（实测把三落点覆盖了一遍、还写了部署记录）。
      现在：dry_run=True 时只走 install_skills() 的预览分支，不写部署记录，
      也不做复核（复核比对的正是没被改动的旧文件，没有信息量）。
    """
    hr("E. 同步 SKILL 到落点")
    if dry_run:
        log("[DRY-RUN] 只预览差异，不写任何文件", "WARN")
        install_skills(dry_run=True, extra_landings=extra_landings)
        log("[DRY-RUN] 跳过部署记录与复核", "WARN")
        return 0
    written = install_skills(dry_run=False, extra_landings=extra_landings)
    if not written:
        log("没有可写的落点 —— 清单里没有在场客户端？", "WARN")
    write_deploy_manifest(written, project_root)
    return verify_skills(extra_landings)


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
def _parse_source_tools(project_root: Path) -> list[dict] | None:
    """AST 解析 `mcp_server/tools.py` 里的 @mcp.tool 定义（不 import、不跑代码）。

    ★ 复用契约套件的 `tool_catalog.parse_tools`（`eval/suites/tool_contract/`）：
      工具名 / 数量只有一份产地，解析器也只该有一份 —— 两边各写一份，才是
      「自检说 39、部署说 38」这类两套数并存的温床（#7 要消灭的就是这个）。
      只有套件不在（只拷了 mcp_server 的瘦检出）才退回下面的极简解析。

    :return: [{name, ...}]（按名字排序）；读不到 / 解析失败返回 None
    """
    src = project_root / "mcp_server" / "tools.py"
    if not src.is_file():
        return None

    suite = project_root / CONTRACT_SUITE
    if suite.is_dir() and str(suite) not in sys.path:
        sys.path.insert(0, str(suite))
    try:
        from tool_catalog import parse_tools  # type: ignore

        tools, _ = parse_tools(src)
        return tools
    except Exception:  # noqa: BLE001
        pass

    # 瘦检出兜底：只要名字，够 D2 报「运行期 / 源码」两个数
    try:
        import ast

        tree = ast.parse(src.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call):
                deco = deco.func
            if (isinstance(deco, ast.Attribute) and deco.attr == "tool"
                    and isinstance(deco.value, ast.Name) and deco.value.id == "mcp"):
                names.append({"name": node.name})
                break
    return sorted(names, key=lambda t: t["name"])


def _source_tool_count(project_root: Path) -> int | None:
    """数 mcp_server/tools.py 里 @mcp.tool 定义的个数。

    为什么不用常量：手写期望值（旧的 EXPECTED_TOOLS = 39）是第二份真源，
    加一个工具就得记得改它，忘了就永远比对通过。这里比的是同一份 clone 的
    「源码定义数」与「运行期注册数」，两个量同源，不一致才有意义。
    """
    tools = _parse_source_tools(project_root)
    return len(tools) if tools is not None else None


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

    # 期望值不手写：从同一份 clone 的源码现算（见 _source_tool_count）
    source_defs = _source_tool_count(project_root)
    if count and source_defs is not None:
        log(f"MCP 工具总数：运行期 {count} / 源码 {source_defs}", "OK")
        if count < source_defs:
            log(f"  运行期比源码少 {source_defs - count} 个：多半是客户端还连着旧进程，", "WARN")
            log("  重连客户端（或重启 Allegro）后再看；源码没拉全就到仓库根 git pull。", "WARN")
        elif count > source_defs:
            log("  运行期比源码多：进程里挂着本地未提交的改动，或 clone 落后于正在跑的那份代码。", "WARN")
    elif count:
        log(f"MCP 工具总数：{count}（读不到源码 tools.py，跳过同源比对）", "WARN")
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
# F. 工具契约断言（--verify-contract；票 #9，口径见 #4 D5 / #7）
# ============================================================
def _load_contract_baseline(project_root: Path) -> dict | None:
    """读冻结基线；缺文件 / 坏 JSON 都返回 None（调用方当失败，不许假绿）。"""
    p = project_root / CONTRACT_BASELINE
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _runtime_tool_names(project_root: Path) -> list[str] | None:
    """运行期实际注册的工具名（就是客户端拿到的那张表）。

    与 `smoke_test` 的 D2 读同一处（`mcp._list_tools()`，退回 `_tool_manager._tools`），
    这样「冒烟报的数」与「闸门断言的数」一定来自同一份运行期状态。
    """
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        from mcp_server.server import mcp  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log(f"导入 mcp_server.server 失败：{exc}", "ERR")
        return None

    try:
        import asyncio

        return [t.name for t in asyncio.run(mcp._list_tools())]  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None)
    if isinstance(tools, dict):
        return list(tools.keys())
    return None


def verify_contract(project_root: Path) -> int:
    """工具契约的**部署侧**断言（`--verify-contract`）。

    只断言两条，都是「部署完成后实际发生的事实」：

      1. **数量同源**：运行期注册数 == 同一份 clone 的源码 AST 定义数。
         不写期望常量（旧的 `EXPECTED_TOOLS = 39` 是第二份真源，已按 #7 删除）——
         两个量出自同一份代码，不等就说明「跑着的不是这份代码」或「源码没拉全」。
      2. **名字集合**：运行期注册的工具名 == `eval/baselines/tool_contract.json`
         的 `tools[].name`（冻结基线）。基线只回答「有没有被悄悄改名 / 删掉 / 新增」，
         不参与生成任何东西。

    **不做**：流程步骤数 —— 没有稳定锚点（步骤随分工调整，也没有单一真源），
    硬比一个数等于再造一份手写期望值；等有锚点再议（票 #9 里写明了这个取舍）。

    :return: 0 = 两条都成立；1 = 任一不符（缺基线 / 读不到源码 / 读不到运行期也算）
    """
    hr("F. 工具契约断言（工具数同源 + 名字集合 == 冻结基线）")
    log(f"真源：{project_root / 'mcp_server' / 'tools.py'} 的 @mcp.tool 定义")
    log(f"基线：{project_root / CONTRACT_BASELINE}")
    log("      （基线只作冻结比对物，不是产地；改工具后由 --update 重冻）")

    baseline = _load_contract_baseline(project_root)
    if baseline is None:
        log(f"缺契约基线（{CONTRACT_BASELINE.as_posix()} 不存在或不是合法 JSON）—— 无法断言。", "ERR")
        log("  冻结基线：python eval/suites/tool_contract/check_tool_contract.py --update", "WARN")
        return 1

    base_names = {t["name"] for t in baseline.get("tools") or [] if t.get("name")}
    log(f"基线冻结于 {baseline.get('frozen_at', '?')}，{len(base_names)} 个工具名")

    source_tools = _parse_source_tools(project_root)
    if source_tools is None:
        log("读不到源码里的工具定义（mcp_server/tools.py 缺失或解析失败）—— 无法同源比较。", "ERR")
        return 1
    source_names = [t["name"] for t in source_tools]

    runtime_names = _runtime_tool_names(project_root)
    if runtime_names is None:
        log("读不到运行期注册的工具表 —— 无法断言。", "ERR")
        log("  常见原因：用系统 Python 跑本脚本（fastmcp / mcp_server 只在项目 .venv 里），"
            "或客户端还连着旧进程。", "WARN")
        return 1

    log(f"工具数：运行期 {len(runtime_names)} / 源码 {len(source_names)} / 基线 {len(base_names)}")

    bad = 0

    # ---- 1. 数量同源（运行期 vs 这份 clone 的源码） ----
    if len(runtime_names) != len(source_names):
        bad += 1
        log(f"数量不符：运行期 {len(runtime_names)} != 源码 {len(source_names)}"
            f"（差 {abs(len(runtime_names) - len(source_names))} 个）", "ERR")
        log("  运行期少 = 进程还挂着旧代码（重启客户端）；多 = clone 落后于正在跑的那份。", "WARN")

    # ---- 2. 名字集合 == 冻结基线（缺 = 改名/删除；多 = 新增未重冻） ----
    runtime_set = set(runtime_names)
    missing = sorted(base_names - runtime_set)
    extra = sorted(runtime_set - base_names)
    for name in missing:
        bad += 1
        log(f"基线里的工具不在运行期：{name}（改名 / 删除 = 破坏契约）", "ERR")
    for name in extra:
        bad += 1
        log(f"运行期多出基线没有的工具：{name}（确认无破坏后跑 --update 重冻基线）", "ERR")

    if bad:
        log(f"不通过：{bad} 处不符 —— 部署出来的工具表不是契约所冻结的那一份。", "ERR")
        return 1
    log("通过：运行期工具表 == 源码定义 == 冻结基线。", "OK")
    return 0


# ============================================================
# main
# ============================================================
def parse_landing_arg(raw: str) -> dict:
    """解析 `--landing ID=PATH`。

    ★ 显式指定 = 你替「这个客户端在场」作证，所以 require 记 always：不再去查
      配置文件（客户端可能把这台机器当远端、或配置放在别处）。
    """
    lid, sep, path = raw.partition("=")
    lid, path = lid.strip(), path.strip()
    if not sep or not lid or not path:
        raise SystemExit(f"--landing 要写成 ID=PATH，收到：{raw!r}")
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    return {"id": lid, "client": f"{lid}（--landing 显式指定）",
            "skills_dir": path, "require": {"kind": "always"}}


def main() -> int:
    ap = argparse.ArgumentParser(description="layout-forge-mcp Agent 侧一键配置")
    ap.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT),
                    help="项目根目录（默认：本文件所在目录的上一级）")
    ap.add_argument("--pcbenv", default=None,
                    help="手动指定 Allegro 的 pcbenv 目录")
    ap.add_argument("--venv-python", default=None,
                    help="写进 MCP 配置的 python.exe（默认=项目 .venv 的解释器）。"
                         "★ 在受限环境里用「工作区外」的解释器跑本脚本时**必须**显式给这个参数，"
                         "否则配置会被写成那个临时解释器的路径（2026-10-16 实测）。")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    ap.add_argument("--no-mcp", action="store_true", help="跳过 MCP 注册")
    ap.add_argument("--no-skills", action="store_true", help="跳过 Skill 安装")
    ap.add_argument("--no-skillbridge", action="store_true", help="跳过 SkillBridge 配置")
    ap.add_argument("--no-smoke", action="store_true", help="跳过冒烟验收")
    ap.add_argument("--landing", action="append", default=[], metavar="ID=PATH",
                    help="显式追加一个落点（可多次，同 id 覆盖 landings.json 里的条目）。"
                         "指定即视为该客户端在场（不查配置文件证据）")
    ap.add_argument("--verify-skills", action="store_true",
                    help="只做覆盖校验：比对 deploy\\skills 与 landings.json 里的每个在场落点，然后退出")
    ap.add_argument("--verify-contract", action="store_true",
                    help="只做工具契约断言：运行期工具表 vs 源码 AST 数 / 冻结基线，然后退出")
    ap.add_argument("--sync-skills", action="store_true",
                    help="只把 deploy\\skills 同步到清单里的在场落点，然后校验")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    venv_python = (
        Path(args.venv_python).resolve()
        if args.venv_python
        else default_venv_python(project_root)
    )
    if args.venv_python is None and venv_python != Path(sys.executable):
        log(f"配置里将写入项目 .venv：{venv_python}（本脚本运行在 {sys.executable}）", "WARN")
    elif not venv_python.is_file():
        log(f"没找到解释器 {venv_python}；请先用部署脚本建好 .venv。", "ERR")
        return 1

    extra_landings = [parse_landing_arg(raw) for raw in args.landing]

    # ★ 三个「只干这一件事」的入口：不重复跑 MCP/冒烟，装完就能单独复核。
    if args.verify_skills:
        return verify_skills(extra_landings)
    if args.verify_contract:
        return verify_contract(project_root)
    if args.sync_skills:
        return sync_skills(project_root, dry_run=args.dry_run, extra_landings=extra_landings)

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
        hr("B. 安装 Skill（清单里的在场落点）")
        written = install_skills(args.dry_run, extra_landings)
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

    # ★ E：装完必须自证「每个在场落点都读得到同一份规则、且至少有一个落点」——
    #   这是防静默回退的最后一道闸；不一致（或零落点）就让一键部署**失败**，
    #   而不是留下一个说不清的现场。
    if not args.no_skills and not args.dry_run:
        results["skills_sync"] = verify_skills(extra_landings) == 0

    # ★ F：契约也是「装对了吗」的一部分 —— 运行期注册的必须就是这份源码里定义
    #   的那份、且没偏离冻结基线（票 #9 / 决策 #4 D5）。任一不符 → results 记
    #   False → 整体 exit 1，而不是只打一行提示。
    if not args.dry_run:
        results["tool_contract"] = verify_contract(project_root) == 0

    hr("收尾")
    print("  下一步：")
    print("    1. 完全退出并重启你的 Agent 客户端（MCP 是长驻进程，必须重启才加载）")
    print("    2. 客户端里确认 layout-forge-mcp 已连接；工具清单见 docs/tools-catalog.md"
          "（数量以那里为准，别在这里写死）")
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
