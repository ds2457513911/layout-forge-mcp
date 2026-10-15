#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
behavior 行为夹具 —— 「SKILL 的 S 系硬规则真的可执行吗」考卷（L3 编排与控制层）。

为什么需要它（L5 度量与评测层 → 考 L3）
    `allegro-footprint-check` 的 S 系硬规则是**写在文档里的约束**：
      · S1 目标消歧：目录里有多份 .dra / 多份 PDF → **禁止默认拿第一个就跑**，必须列候选让用户确认；
      · S2 禁复用旧报告：本次结论只能来自本次运行，目录里的 `checklist_report_*.json`
        是历史产物（**不是证据**）；
      · S4 先探通道：凡是要触 Allegro 的工具，先探通道；探针失败就如实说「未连接」，
        禁止盲调后把超时误读成「工具挂了」。
    文档约束靠「模型自觉」是守不住的（违反了也看不出来）。本套件用**可控的临时目录**把这些
    场景造出来，逼工具层表态：该报 `ambiguous` 就不能给一个「我猜的」答案，该说没通道就不能沉默。

判据（B 段）
    | 夹具 | 场景 | 通过条件 |
    |--|--|--|
    | B1 | S1 多份 .dra / 多份 PDF | `classify_folder` 返回 `ambiguous_dra`/`ambiguous_pdf` + `action=confirm`，
    |    |                              | 且 `dra_files`/`pdf_files` **列出全部候选**（不许只回一个） |
    | B2 | S2 目录里有历史报告 | 旧报告必须落进 `noise_files`（**不算证据**）；目录里只有旧报告时必须 `stop` |
    | B3 | S1 噪声（`AUTOSAVE.dra`） | 噪声不计入候选：1 真 + 1 AUTOSAVE → 必须判 `ok`，AUTOSAVE 落 `noise_files` |
    | B4 | S4 通道先探 | `services.footprint_builder.inspect_environment()` 返回结构化体检；
    |    |                 | `connected=False` 时 `error` 必须非空（**不许沉默**），且不得抛异常 |
    | B5 | L3 状态机 | `playbook.json` 满足硬约定（每条规则都有检测方式 + 后果类别 + 证据字段）；
    |    |            | 步骤引用的 gate 都存在；一对 PDF/.dra + 通道正常 → 放行到 `extract`；
    |    |            | 歧义 → `confirm` 且**停在 preflight**；通道不通 → `stop` + `CHANNEL_NOT_READY`；
    |    |            | 已删除/未知的 `rule_id` 必须被点名（不许静默计入统计） |

★ 三条纪律
    1. **不用真实库做夹具**：全部在系统 TEMP 里现场造临时目录（跑完即删），不碰 `Data/`。
    2. **不静默跳过**：B4 需要装了 `skillbridge` 的解释器；找不到就报 ERROR（含修复提示），
       而不是「没依赖所以算通过」。
    3. **夹具只读生产代码**：本套件 import `orchestration.preflight` / `orchestration.pipeline` /
       `services.footprint_builder`，绝不修改它们；失败就是生产代码的失败。
       B5 用**假探针**喂通道状态，因此不依赖 Allegro 是否在跑。

用法
    python eval/suites/behavior/check_behavior.py            # 跑全部夹具
    python eval/suites/behavior/check_behavior.py --json
    python eval/suites/behavior/check_behavior.py --python <带 skillbridge 的解释器>

退出码：0 = 无 ERROR；1 = 有 ERROR。
纯 stdlib（B4 用子进程跑，所以本文件本身不需要依赖）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # layout-forge-mcp/
LAYOUT = ROOT.parent                                # Layout/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 找「带 skillbridge 的解释器」的顺序：环境变量 → 缓存区不受限副本 → 兄弟仓 venv → 当前解释器
UNCONFINED_PY = os.environ.get(
    "LAYOUT_FORGE_UNCONFINED_PY",
    str(Path.home() / "WorkBuddy/Layout/.workbuddy/cache/tools/venv-unconfined/Scripts/python.exe"),
)
SIBLING_PY = str(LAYOUT / "layout-check-mcp/.venv/Scripts/python.exe")

CHANNEL_SNIPPET = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
try:
    from services import footprint_builder as fb
except Exception as exc:                     # 依赖缺失也是「通道没准备好」的一种
    print(json.dumps({"import_error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)
try:
    res = fb.inspect_environment()
    print(json.dumps({"result": res}, ensure_ascii=False, default=str))
except Exception as exc:
    print(json.dumps({"raised": f"{type(exc).__name__}: {exc}"}))
"""


# ---------- 夹具脚手架 ----------
def _mk(tmp: Path, *names: str) -> Path:
    d = tmp / names[0]
    d.mkdir(parents=True, exist_ok=True)
    for n in names[1:]:
        (d / n).write_bytes(b"")           # 内容无关：预检只看名字与后缀
    return d


def _classify(folder: Path) -> dict:
    from orchestration import preflight as pf
    return pf.classify_folder(str(folder))


def _pick_interpreter(explicit: str | None) -> tuple[str | None, list[str]]:
    """返回 (可用解释器, 尝试过的清单)。"""
    tried = []
    cands = [explicit] if explicit else [UNCONFINED_PY, SIBLING_PY, sys.executable]
    for cand in cands:
        if not cand or not Path(cand).is_file():
            tried.append(f"{cand}（不存在）")
            continue
        try:
            p = subprocess.run([cand, "-c", "import skillbridge"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except Exception as exc:                       # noqa: BLE001
            tried.append(f"{cand}（无法执行：{exc}）")
            continue
        if p.returncode == 0:
            return cand, tried
        tried.append(f"{cand}（import skillbridge 失败）")
    return None, tried


# ---------- 夹具 B1–B4 ----------
def b1_ambiguous(tmp: Path) -> dict:
    """S1：多份 .dra / 多份 PDF 必须列候选 + confirm，不许默默选一个。"""
    errs, cases = [], []
    d1 = _mk(tmp, "b1_dra", "spec.pdf", "a.dra", "b.dra")
    d2 = _mk(tmp, "b1_pdf", "one.dra", "spec_a.pdf", "spec_b.pdf")
    for label, folder, want in (("多份 .dra", d1, "ambiguous_dra"), ("多份 PDF", d2, "ambiguous_pdf")):
        r = _classify(folder)
        cand = r.get("dra_files") if want == "ambiguous_dra" else r.get("pdf_files")
        cases.append({"dir": label, "verdict": r.get("verdict"), "action": r.get("action"),
                      "candidates": cand})
        if r.get("verdict") != want:
            errs.append(f"B1 [{label}] 期望 verdict={want}，实际 {r.get('verdict')}")
        if r.get("action") != "confirm":
            errs.append(f"B1 [{label}] 期望 action=confirm（先问用户），实际 {r.get('action')}")
        if not cand or len(cand) < 2:
            errs.append(f"B1 [{label}] 候选没列全：{cand}（★ 只回一个 = 默认拿第一个就跑）")
    return {"name": "B1", "rule": "S1 目标消歧", "cases": cases, "errors": errs}


def b2_stale_report(tmp: Path) -> dict:
    """S2：目录里的历史 checklist_report_*.json 不算证据，且只有它时必须 stop。"""
    errs, cases = [], []
    d1 = _mk(tmp, "b2_with_pair", "spec.pdf", "real.dra", "checklist_report_20260101_120000.json")
    r1 = _classify(d1)
    cases.append({"dir": "有一对 PDF/.dra + 旧报告", "verdict": r1.get("verdict"),
                  "noise_files": r1.get("noise_files")})
    if r1.get("verdict") != "ok":
        errs.append(f"B2 有一对 PDF/.dra 时应判 ok，实际 {r1.get('verdict')}")
    if "checklist_report_20260101_120000.json" not in (r1.get("noise_files") or []):
        errs.append("B2 旧报告没被标成噪声 → 会被当成本次证据（S2 的物证没落地）")

    d2 = _mk(tmp, "b2_only_report", "checklist_report_20260101_120000.json", "allegro.jrl")
    r2 = _classify(d2)
    cases.append({"dir": "只有旧报告", "verdict": r2.get("verdict"), "action": r2.get("action"),
                  "noise_files": r2.get("noise_files")})
    if r2.get("verdict") not in ("missing_pdf", "missing_dra", "empty"):
        errs.append(f"B2 只有旧报告时必须判缺料并 stop，实际 {r2.get('verdict')}")
    if r2.get("action") != "stop":
        errs.append(f"B2 只有旧报告时 action 应为 stop，实际 {r2.get('action')}")
    return {"name": "B2", "rule": "S2 禁复用旧报告", "cases": cases, "errors": errs}


def b3_autosave(tmp: Path) -> dict:
    """S1 噪声：AUTOSAVE.dra 不能算候选（金标准库真发生过 5 例）。"""
    errs, cases = [], []
    d = _mk(tmp, "b3_autosave", "spec.pdf", "real.dra", "AUTOSAVE.dra")
    r = _classify(d)
    cases.append({"dir": "1 真 + 1 AUTOSAVE", "verdict": r.get("verdict"),
                  "dra_files": r.get("dra_files"), "noise_files": r.get("noise_files")})
    if r.get("verdict") != "ok":
        errs.append(f"B3 去掉 AUTOSAVE 后应剩唯一 .dra、判 ok，实际 {r.get('verdict')}")
    if (r.get("dra_files") or []) != ["real.dra"]:
        errs.append(f"B3 候选应只剩 real.dra，实际 {r.get('dra_files')}")
    if "AUTOSAVE.dra" not in (r.get("noise_files") or []):
        errs.append("B3 AUTOSAVE.dra 没被标成噪声（会被取成「目录里第一个 .dra」）")
    return {"name": "B3", "rule": "S1 噪声剔除", "cases": cases, "errors": errs}


def b4_channel(explicit_py: str | None) -> dict:
    """S4：先探通道；结果必须结构化，连不上时 error 必须非空，且不许抛异常。"""
    errs, cases = [], []
    py, tried = _pick_interpreter(explicit_py)
    if py is None:
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases,
                "errors": ["B4 找不到带 skillbridge 的解释器 → 无法验证「先探通道」（**不静默跳过**）。"
                           "修复：重跑那条 robocopy 重建缓存区 venv-unconfined，或用 --python 指定。"
                           f" 试过：{'；'.join(tried)}"]}
    p = subprocess.run([py, "-c", CHANNEL_SNIPPET, str(ROOT)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = (p.stdout or "").strip().splitlines()
    payload = {}
    if out:
        try:
            payload = json.loads(out[-1])
        except Exception:                                   # noqa: BLE001
            payload = {}
    cases.append({"python": py, "rc": p.returncode,
                  "import_error": payload.get("import_error"),
                  "raised": payload.get("raised"),
                  "result_keys": sorted((payload.get("result") or {}).keys())})
    if payload.get("import_error"):
        errs.append(f"B4 通道体检 import 失败：{payload['import_error']}（依赖缺失也要以明确错误暴露）")
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}
    if payload.get("raised"):
        errs.append(f"B4 inspect_environment() 抛异常：{payload['raised']}（探针必须 self-contained）")
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}
    res = payload.get("result") or {}
    if not isinstance(res, dict) or not res:
        errs.append(f"B4 体检没返回结构化结果：{out[-1] if out else '(无输出)'}")
        return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}
    if not isinstance(res.get("connected"), bool):
        errs.append(f"B4 缺 `connected` 布尔字段：{sorted(res.keys())}")
    if res.get("connected") is False and not str(res.get("error") or "").strip():
        errs.append("B4 connected=False 但 error 为空 → 模型会把「没通道」误读成「工具挂了」")
    if res.get("connected") is True and res.get("units") not in ("mm", "mil", "unknown"):
        errs.append(f"B4 connected=True 时 units 取值异常：{res.get('units')!r}")
    cases[-1]["verdict"] = "channel-up" if res.get("connected") else "channel-down-explicit"
    return {"name": "B4", "rule": "S4 先探通道", "cases": cases, "errors": errs}


# ---------- 夹具 B5 ----------
def b5_pipeline(tmp: Path) -> dict:
    """L3 状态机：playbook 硬约定 + 步骤放行/拦停的判定（不碰 Allegro，喂假探针）。

    这一夹具守的是「编排层自己」：规则表缺「检测方式/后果类别」、步骤引用了不存在的 gate、
    歧义时还继续往下取数、通道没探就放行 —— 任何一条都该在这里变红。
    """
    from orchestration import pipeline as pl

    errs, cases = [], []

    # 1) playbook 硬约定 + 步骤表自洽
    problems = pl.validate_playbook()
    cases.append({"check": "playbook 硬约定", "problems": problems})
    if problems:
        errs.append("B5 playbook 违反硬约定（每条规则都必须有检测方式 + 后果类别 + 证据字段）："
                    + "；".join(problems))

    known = {str(g["id"]) for g in pl.load_playbook().get("gates") or []}
    referenced = {gid for s in pl.STEPS for gid in (s.pre + s.post)}
    dangling = sorted(referenced - known)
    outside = sorted(known - referenced)
    cases.append({"check": "步骤引用的 gate 都在 playbook 里", "dangling": dangling,
                  "不在 pipeline 步骤里（由套件/部署侧守）": outside})
    if dangling:
        errs.append(f"B5 步骤引用了 playbook 里不存在的 gate：{dangling}")
    names = tuple(s.name for s in pl.STEPS)
    if names != pl.STEP_NAMES:
        errs.append(f"B5 步骤名与 STEP_NAMES 不一致：{names} vs {pl.STEP_NAMES}")

    # 2) 一对 PDF/.dra + 通道正常 → 放行到 extract
    up = lambda: {"connected": True, "units": "millimeters"}          # noqa: E731
    d_ok = _mk(tmp, "b5_ok", "spec.pdf", "real.dra")
    r_ok = pl.run(d_ok, env_probe=up)
    cases.append({"check": "一对 PDF/.dra + 通道正常", "action": r_ok.action, "next_step": r_ok.next_step})
    if r_ok.action != "proceed" or r_ok.next_step != pl.STEP_EXTRACT:
        errs.append(f"B5 正常输入应放行到 extract，实际 action={r_ok.action} next_step={r_ok.next_step}")

    # 3) 歧义 → confirm 且不许继续往下走（“拿不准还继续取数”正是要防的）
    d_amb = _mk(tmp, "b5_ambiguous", "real.dra", "a.pdf", "b.pdf")
    r_amb = pl.run(d_amb, env_probe=up)
    ran = [s.name for s in r_amb.steps if s.status in ("done", "needs-confirm")]
    fails = [g.gate_id for s in r_amb.steps for g in s.gates if not g.ok]
    cases.append({"check": "歧义输入", "action": r_amb.action, "已执行步骤": ran,
                  "未通过的 gate": fails})
    if r_amb.action != "confirm":
        errs.append(f"B5 歧义输入应 confirm（问用户），实际 {r_amb.action}")
    if ran != [pl.STEP_PREFLIGHT]:
        errs.append(f"B5 歧义时必须停在 preflight（后续步骤一律 pending），实际已执行 {ran}")
    if "G-ambiguous-src" not in fails:
        errs.append("B5 歧义时 G-ambiguous-src 必须判失败（否则等于默认拿了第一个）")

    # 4) 通道没探通 → stop + 明确失败码（不静默跳过）
    down = lambda: {"connected": False, "error": "CDSROOT 未配置"}     # noqa: E731
    r_down = pl.run(d_ok, env_probe=down)
    codes = [e.get("error_code") for e in r_down.events]
    cases.append({"check": "通道不可用", "action": r_down.action, "error_codes": codes})
    if r_down.action != "stop":
        errs.append(f"B5 通道不可用必须 stop，实际 {r_down.action}")
    if pl.ERR_CHANNEL_NOT_READY not in codes:
        errs.append(f"B5 通道不可用应给出 {pl.ERR_CHANNEL_NOT_READY}，实际 {codes}")

    # 5) 结论归类：引用了已删除的 rule_id 或被代码遗忘的 rule_id 都必须点名
    cls = pl.summarize_findings([{"rule_id": "2.7"}, {"rule_id": "6.5"}, {"rule_id": "9.9"}])
    cases.append({"check": "结论归类", "silent": cls["silent"],
                  "stale": cls["stale_rule_ids"], "unknown": cls["unknown_rule_ids"]})
    if "6.5" not in cls["stale_rule_ids"]:
        errs.append("B5 6.5 是已删除的 rule_id（rule_checker.py:1329），案例再引用它必须被点名")
    if "9.9" not in cls["unknown_rule_ids"]:
        errs.append("B5 代码里没有的 rule_id 不许静默计入统计")
    if "2.7" not in cls["silent"]:
        errs.append("B5 2.7 的后果类别是 silent，归类没按 playbook 走")
    return {"name": "B5", "rule": "L3 状态机 + playbook 硬约定", "cases": cases, "errors": errs}


# ---------- 主流程 ----------
def run(explicit_py: str | None = None) -> dict:
    results, errors = [], []
    with tempfile.TemporaryDirectory(prefix="behavior_fixtures_") as tmpdir:
        tmp = Path(tmpdir)
        for fn in (b1_ambiguous(tmp), b2_stale_report(tmp), b3_autosave(tmp), b5_pipeline(tmp)):
            results.append(fn)
            errors += fn["errors"]
    b4 = b4_channel(explicit_py)          # 子进程，自己管临时文件
    results.append(b4)
    errors += b4["errors"]
    return {"fixtures": results, "errors": errors,
            "error_count": len(errors),
            "fixture_count": len(results),
            "passed": sum(1 for r in results if not r["errors"])}


def _render(rep: dict) -> None:
    print("=" * 74)
    print("behavior 行为夹具（L3 规则可执行性）")
    print("=" * 74)
    for r in rep["fixtures"]:
        mark = "✓" if not r["errors"] else "✗"
        print(f"\n{mark} {r['name']}  {r['rule']}")
        for c in r["cases"]:
            print("   ", json.dumps(c, ensure_ascii=False)[:300])
        for e in r["errors"]:
            print("    [X]", e)
    print("\n" + "-" * 74)
    print(f"夹具 {rep['passed']}/{rep['fixture_count']} 通过；ERROR {rep['error_count']} 项")
    if not rep["errors"]:
        print("[✓] 全部通过（S1 歧义可见 / S2 旧报告不算证据 / S4 通道先探 / L3 状态机放行与拦停）")


def main(argv: list[str] | None = None) -> int:
    # 中文 Windows 的默认 stdout 是 GBK：打印 ✓ 会 UnicodeEncodeError，
    # 让「全部通过」变成栈回溯（评测脚本看起来像套件坏了）。自己保证 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="behavior 行为夹具（S1/S2/S4 硬规则可执行性）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--python", default=None, help="带 skillbridge 的解释器（B4 用）")
    args = ap.parse_args(argv)

    rep = run(args.python)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _render(rep)
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
