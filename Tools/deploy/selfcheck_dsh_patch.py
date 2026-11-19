# -*- coding: utf-8 -*-
"""
Tools/deploy/selfcheck_dsh_patch.py —— A2 步（DSH 连接器写入）的自检

为什么需要它：
  A2 改的是**宿主的组合 patch 文件**：写坏了 DSH 可能整个 profile 起不来，
  而这种文件在每个 profile 里都是几百行、带大量注释。所以真正要守的是四条：
  ① 只追加、注释一行不丢；② 已有同名 serverName 时一个字不动（幂等）；
  ③ 空的 [] 能被替换成条目；④ 写完仍是合法 YAML 序列才落盘。
  这些都在临时目录里跑，不碰任何真实 profile。

用法：
    .venv/Scripts/python.exe Tools/deploy/selfcheck_dsh_patch.py [-v]

退出码：0 = 全部通过；1 = 有失败项
"""
from __future__ import annotations

import contextlib
import importlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import setup_agent as S  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    print("[SKIP] 没有 PyYAML，A2 本身也会跳过写盘，本自检无意义。")
    sys.exit(0)

VERBOSE = "-v" in sys.argv
FAILS: list[str] = []
CHECKS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"[ OK ] {name}")
        return
    FAILS.append(name)
    print(f"[FAIL] {name}  {detail}")


def run_a2(home: Path, dry_run: bool = False) -> tuple[bool, str]:
    """在 home 下跑一次 A2，返回 (ok, 日志)。"""
    old = S.real_profile
    S.real_profile = lambda: home  # type: ignore[assignment]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = S.ensure_dsh_connectors(Path("C:/repo"), Path("C:/repo/.venv/Scripts/python.exe"), dry_run)
    finally:
        S.real_profile = old  # type: ignore[assignment]
    out = buf.getvalue()
    if VERBOSE:
        print(out)
    return ok, out


def new_home(names: tuple[str, ...], content: dict[str, str]) -> Path:
    home = Path(tempfile.mkdtemp(prefix="a2selfcheck_"))
    for n in names:
        d = home / ".dsh" / "profiles" / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "cordis.patch.yml").write_text(content[n], encoding="utf-8")
    return home


def main() -> int:
    # ① 普通列表：纯追加、条目可解析、注释不丢
    home = new_home(("web",), {"web": "# header\n- id: a\n  name: x\n# tail note\n"})
    ok, out = run_a2(home)
    p = home / ".dsh/profiles/web/cordis.patch.yml"
    txt = p.read_text(encoding="utf-8")
    data = yaml.safe_load(txt)
    check("普通列表：写入成功且 exit 0", ok and "已写入" in out, out)
    check("普通列表：够 2 项", len(data) == 2, str(data))
    check("普通列表：条目名与 id 正确",
          data[-1]["name"] == S.DSH_MCP_BUNDLE and data[-1]["id"] == f"mcp-{S.SERVER_NAME}", str(data[-1]))
    check("普通列表：注释保留", "# header" in txt and "# tail note" in txt)
    check("普通列表：原文逐行未动", txt.startswith("# header\n- id: a\n  name: x\n# tail note\n"))
    check("普通列表：生成了备份", bool(list(p.parent.glob("cordis.patch.yml.bak_*"))))

    # ② 幂等：再跑一次，文件字节不变
    before = p.read_bytes()
    ok2, out2 = run_a2(home)
    check("幂等：第二次 ok 且跳过", ok2 and "已注册" in out2, out2)
    check("幂等：文件一个字节没变", p.read_bytes() == before)

    # ③ 空列表：\`[]\` 被替换成条目（desktop 那种）
    home = new_home(("desktop",), {"desktop": "# c1\n# c2\n[]\n"})
    ok, out = run_a2(home)
    txt = (home / ".dsh/profiles/desktop/cordis.patch.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(txt)
    check("空列表：写入成功", ok and len(data) == 1, out)
    check("空列表：注释保留、没有残留 []", txt.startswith("# c1\n# c2\n") and "[]" not in txt)

    # ④ 多 profile：一次全写，缺文件的那份不算失败
    home = new_home(("web", "tauri"), {"web": "- id: a\n  name: x\n", "tauri": "- id: b\n  name: y\n"})
    ok, out = run_a2(home)
    check("多 profile：两份都写上",
          ok and all(yaml.safe_load((home / f".dsh/profiles/{n}/cordis.patch.yml").read_text(encoding="utf-8"))[-1]["name"] == S.DSH_MCP_BUNDLE
                     for n in ("web", "tauri")), out)

    # ⑤ 坏文件：追加后不是合法 YAML 时不落盘
    home = new_home(("web",), {"web": "- id: a\n  name: x\n  bad: [1,\n"})
    ok, out = run_a2(home)
    txt = (home / ".dsh/profiles/web/cordis.patch.yml").read_text(encoding="utf-8")
    check("坏文件：判失败且未写入", (not ok) and "未写入" in out and "mcp-" not in txt, out)

    # ⑥ --dry-run：只打印、不落盘
    home = new_home(("web",), {"web": "- id: a\n  name: x\n"})
    before = (home / ".dsh/profiles/web/cordis.patch.yml").read_bytes()
    ok, out = run_a2(home, dry_run=True)
    check("dry-run：ok 且打印、文件未变",
          ok and "DRY-RUN" in out and (home / ".dsh/profiles/web/cordis.patch.yml").read_bytes() == before, out)

    # ⑦ 生成的块本身必须是合法 YAML（@ 开头的 bundle 名最容易踩）
    block = S.dsh_connector_block(Path("C:/repo"), Path("C:/repo/.venv/Scripts/python.exe"))
    parsed = yaml.safe_load("\n".join(block))
    check("块语法：可解析为单条目", isinstance(parsed, list) and len(parsed) == 1
          and parsed[0]["config"]["serverName"] == S.SERVER_NAME, str(parsed))

    # ⑧ 有没有 serverName 判定不看行 id（手改过 id 的行也算已注册）
    check("判定：按 serverName 而不是 id",
          S._dsh_has_server("- id: handmade\n  config:\n    serverName: layout-forge-mcp\n")
          and not S._dsh_has_server("- id: mcp-layout-forge-mcp\n"))

    print()
    if FAILS:
        print(f"[XX] {len(FAILS)}/{CHECKS} 项失败：" + "、".join(FAILS))
        return 1
    print(f"[OK] {CHECKS} 项全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
