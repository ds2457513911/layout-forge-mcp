# -*- coding: utf-8 -*-
"""
Tools/deploy/selfcheck_landings.py —— 部署闸门的六态判定自检（不需要任何 Agent 客户端）

为什么需要它：
  --verify-skills 的价值全在「判得对」上。六个状态里有四个是失败，而真实落点
  在一台机器上只有一份 —— 靠手改用户的技能目录去试一遍，既脏又难复现。
  这里用临时目录把 MATCH / STALE / DIVERGED / MISSING / UNVERIFIABLE 和
  「零落点」全部走一遍，外加 real_profile() 不读环境变量、--landing 解析。

用法：
    .venv/Scripts/python.exe Tools/deploy/selfcheck_landings.py
    .venv/Scripts/python.exe Tools/deploy/selfcheck_landings.py -v

退出码：0 = 全部通过；1 = 有失败项
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "deploy"))

import setup_agent as S  # noqa: E402

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


def quiet_verify(extra=None, **kwargs):
    """跑 verify_skills 并吞掉它的大量日志（-v 时再打出来）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = S.verify_skills(extra, **kwargs)
    out = buf.getvalue()
    if VERBOSE:
        print(out)
    return rc, out


def test_judge_landing() -> None:
    truth = {"SKILL.md": ["h1"], "a.md": ["h2"]}
    same = dict(truth)
    other = {"SKILL.md": ["h9"], "a.md": ["h2"]}
    check("judge: MATCH", S.judge_landing(truth, same, None) == "MATCH")
    check("judge: MISSING", S.judge_landing(truth, None, None) == "MISSING")
    check("judge: STALE", S.judge_landing(truth, other, other) == "STALE")
    check("judge: DIVERGED", S.judge_landing(truth, other, {"x.md": ["h7"]}) == "DIVERGED")
    check("judge: UNVERIFIABLE", S.judge_landing(truth, other, None) == "UNVERIFIABLE")


def test_real_profile() -> None:
    real = S.real_profile()
    check("real_profile 返回存在的目录", real.is_dir(), str(real))
    if sys.platform != "win32":
        return
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    try:
        os.environ["HOME"] = r"C:\__dsh_selfcheck_nope__"
        os.environ["USERPROFILE"] = r"C:\__dsh_selfcheck_nope__"
        check("real_profile 不读 HOME/USERPROFILE", S.real_profile() == real,
              f"{S.real_profile()} != {real}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_requirement(tmp: Path) -> None:
    check("require: always", S.evaluate_requirement({"kind": "always"})[0] is True)

    ok_dir = tmp / "there"
    ok_dir.mkdir()
    present, evidence = S.evaluate_requirement({"kind": "dir", "path": str(ok_dir)})
    check("require: dir 存在 → 在场", present is True and evidence)
    present, evidence = S.evaluate_requirement({"kind": "dir", "path": str(tmp / "nope")})
    check("require: dir 不存在 → 不在场", present is False and "没有目录" in evidence[0])

    cfg = tmp / "mcp.json"
    cfg.write_text('{"mcpServers": {"layout-forge-mcp": {}}}', encoding="utf-8")
    present, _ = S.evaluate_requirement(
        {"kind": "mcp_config", "path": str(cfg), "server": "layout-forge-mcp"})
    check("require: mcp.json 已注册 → 在场", present is True)
    cfg.write_text('{"mcpServers": {"other": {}}}', encoding="utf-8")
    present, evidence = S.evaluate_requirement(
        {"kind": "mcp_config", "path": str(cfg), "server": "layout-forge-mcp"})
    check("require: mcp.json 未注册 → 不在场", present is False and "没有注册" in evidence[0])

    yml = tmp / "cordis.patch.yml"
    yml.write_text("- id: x\n  config:\n    serverName: layout-forge-mcp\n", encoding="utf-8")
    present, _ = S.evaluate_requirement(
        {"kind": "mcp_config", "paths": [str(tmp / "missing.yml"), str(yml)],
         "server": "layout-forge-mcp"})
    check("require: yml 文本注册 → 在场（多路径任一命中）", present is True)

    present, evidence = S.evaluate_requirement(
        {"kind": "mcp_config", "paths": [str(tmp / "missing.yml")], "server": "x"})
    check("require: 配置全不存在 → 不在场", present is False and any("没有配置文件" in e for e in evidence))


def test_parse_landing_arg() -> None:
    item = S.parse_landing_arg("probe=" + str(Path(tempfile.gettempdir())))
    check("--landing 解析 id", item["id"] == "probe")
    check("--landing 视为在场", S.evaluate_requirement(item["require"])[0] is True)
    check("--landing 相对路径转绝对", os.path.isabs(S.parse_landing_arg("p=rel/dir")["skills_dir"]))
    try:
        S.parse_landing_arg("bad")
    except SystemExit:
        check("--landing 缺 = 时报错退出", True)
    else:
        check("--landing 缺 = 时报错退出", False)


def test_verify_states(tmp: Path) -> None:
    """端到端：用临时真源 + 临时落点把六态各走一次。"""
    names = sorted(p.name for p in S.DEPLOY_DIR.joinpath("skills").iterdir() if p.is_dir())
    if not names:
        check("真源里有 SKILL", False)
        return

    src = tmp / "src"
    landing = tmp / "landing"
    src.mkdir()
    landing.mkdir()
    for name in names:
        shutil.copytree(S.DEPLOY_DIR / "skills" / name, src / name)
        shutil.copytree(src / name, landing / name)

    lnd = [{"id": "t", "client": "测试客户端", "skills_dir": str(landing),
            "require": {"kind": "always"}}]

    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=None)
    check("全一致 → exit 0", rc == 0, out[-400:])
    check("全一致 → 每个技能都 MATCH", out.count("[t] MATCH") == len(names))

    # 基线 = 落点当前内容
    rec = {f"t:{n}": {"files": S.hash_tree(landing / n)} for n in names}
    base = {"schema": S.MANIFEST_SCHEMA, "deployed_at": "test", "landings": rec}

    # STALE：真源前进、落点还是旧内容
    victim = src / names[0] / "SKILL.md"
    original = victim.read_bytes()
    victim.write_bytes(original + "\n<!-- 真源前进 -->\n".encode("utf-8"))
    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=base)
    check("真源前进 → exit 1", rc == 1)
    check("真源前进 → STALE", "STALE" in out, out[-400:])
    victim.write_bytes(original)

    # DIVERGED：落点被手改，且与基线也不同
    hand = landing / names[0] / "SKILL.md"
    saved = hand.read_bytes()
    hand.write_bytes(saved + "\n<!-- 手改 -->\n".encode("utf-8"))
    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=base)
    check("落点被手改 → DIVERGED 且 exit 1", rc == 1 and "DIVERGED" in out, out[-400:])

    # UNVERIFIABLE：没有基线可对照
    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=None)
    check("无基线 + 内容不符 → UNVERIFIABLE 且 exit 1",
          rc == 1 and "UNVERIFIABLE" in out, out[-400:])

    # 旧 schema 的基线：整体作废，不许误判 STALE
    legacy = {"schema": "layout-forge-deploy-manifest/1", "landings": rec}
    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=legacy)
    check("旧 schema 基线 → 判 UNVERIFIABLE 而不是 STALE",
          rc == 1 and "UNVERIFIABLE" in out and "STALE" not in out, out[-400:])
    hand.write_bytes(saved)

    # MISSING：落点里少了这份技能
    gone = landing / names[0]
    shutil.move(str(gone), str(tmp / "gone"))
    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=base)
    check("落点少一份技能 → MISSING 且 exit 1", rc == 1 and "缺失" in out, out[-400:])
    shutil.move(str(tmp / "gone"), str(gone))

    # NOT_INSTALLED：客户端不在场
    absent = [{"id": "t", "client": "未安装客户端", "skills_dir": str(landing),
               "require": {"kind": "dir", "path": str(tmp / "not-installed")}}]
    rc, out = quiet_verify(src_root=src, landings=absent, manifest=base)
    check("客户端不在场 → NOT_INSTALLED", "NOT_INSTALLED" in out)
    check("零落点（全不在场）→ exit 1（哪怕内容全一致）", rc == 1, out[-400:])

    # 真绿要求至少一个 MATCH：在场但一个都不一致也是失败
    broken = landing / names[0]
    shutil.move(str(broken), str(tmp / "broken"))
    rc, out = quiet_verify(src_root=src, landings=lnd, manifest=None)
    check("在场但内容不符 → exit 1", rc == 1)
    shutil.move(str(tmp / "broken"), str(broken))


def main() -> int:
    print(f"真源：{S.DEPLOY_DIR / 'skills'}")
    test_judge_landing()
    test_real_profile()
    test_parse_landing_arg()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_requirement(tmp)
        test_verify_states(tmp)
    print()
    if FAILS:
        print(f"[XX] {len(FAILS)}/{CHECKS} 项失败：" + "；".join(FAILS))
        return 1
    print(f"[OK] {CHECKS} 项全通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
