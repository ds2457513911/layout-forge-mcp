# -*- coding: utf-8 -*-
"""
R5 · padstack 决策入口对拍（票 #16「把 padstack 的决策与实例化拆开」的验收）

判据（R5.1–R5.4）：
  R5.1 模块纯净性：services/padstack_derive.py 只许 import 纯模块 ——
       不得出现 footprint_builder（宿主执行器）/ skillbridge / win32com / socket /
       subprocess / requests / urllib / mcp_server / orchestration 等。
       判据理由：AC「决策入口不再依赖宿主对象，可离线调用并返回纯数据结构」。
       （与 eval/suites/architecture A3 的 FORBIDDEN_CORE 同精神；这里额外禁止
        footprint_builder，因为决策入口反过来依赖执行器就等于没拆开。）
  R5.2 求解输出对拍：8 份 knowledge/specs，GS.solve(spec) 的**每个字段**与
       eval/baselines/padstack_derive.json 逐字段相等（失败件连错误串一起冻）。
  R5.3 派生输出对拍：derive_padstacks(读数) 的 padstack / extra_padstacks /
       pins_override / notes 与基线逐字段相等；且不就地修改入参（决策入口只读）。
  R5.4 调用方口径：services/footprint_builder.py 仍按改造前的原话把 notes 折成
       warnings（两条文案 + 两个键名）。守的是「补层了却不再报」这类静默行为变化。
  R5.5 通孔补层分支：8 份 spec 的 tht_layers 全为空（都不漏 BOTTOM），这条分支自己
       永远进不了对拍 —— 用一个合成读数（TH 件只给 TOP + SOLDERMASK_TOP）直接守它。

基线来历：eval/baselines/padstack_derive.json 由票 #16 改造前的 HEAD 行为冻结
（现场用 build_footprint 内联的 1a/1b 逻辑逐行复制跑出来的），键名已与新入口对齐，
取值一字未改。

★ 本脚本**故意不提供 --update**：它守的是「拆分没改行为」，而重冻基线就等于承认
  行为变了 —— 那不是本票能自己做的决定。要重冻请改 check_derive 的判据或另起一票，
  并在 commit 里写明为什么行为该变（与 eval/README.md「变红先改事实」同一条纪律）。

跑法（项目根，离线、不需要 Allegro）：
  python eval/suites/geometry_regression/check_derive.py
  python eval/suites/geometry_regression/check_derive.py --json   # 机器可读
"""
from __future__ import annotations

import argparse
import ast
import copy
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

BASELINE = ROOT / "eval" / "baselines" / "padstack_derive.json"
SPECS_DIR = ROOT / "knowledge" / "specs"
DERIVE_MODULE = ROOT / "services" / "padstack_derive.py"

def _forbidden_from_architecture():
    """内核禁入清单**从 A3 借**，不在这里再抄一遍。

    LAYERS.md 的纪律：判定逻辑调同一个函数，不要抄常量（抄常量迟早分叉 —— A3 加了新
    禁令，R5 这边不会知道）。A3 的 FORBIDDEN_CORE 是「顶层模块 → 理由」的字典。
    """
    import importlib.util

    path = ROOT / "eval" / "suites" / "architecture" / "check_architecture.py"
    spec = importlib.util.spec_from_file_location("_arch_for_r5", path)
    if spec is None or spec.loader is None:      # pragma: no cover - 文件在 git 里
        raise RuntimeError(f"读不了 A3 判据：{path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.FORBIDDEN_CORE)


# 决策入口不许碰的东西：A3 那套 + 它额外不该依赖的 **执行器/通道** 子模块
# （A3 只禁顶层包名，抓不到 services.footprint_builder 这种「层内反向依赖」）
FORBIDDEN_IMPORTS = _forbidden_from_architecture()
FORBIDDEN_SERVICES = {"footprint_builder", "skillbridge_writer", "channel_guard"}


class Checker:
    def __init__(self) -> None:
        self.n_pass = 0
        self.fails: List[str] = []
        self.items: List[Dict[str, Any]] = []

    def check(self, ok: bool, desc: str, detail: str = "") -> None:
        self.items.append({"desc": desc, "ok": bool(ok), "detail": detail})
        if ok:
            self.n_pass += 1
        else:
            self.fails.append(f"{desc}{('：' + detail) if detail else ''}")


def _imports_of(path: Path) -> List[tuple]:
    """AST 扫全部 import（含函数体内），返回 [(top, full, lineno)]。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name.split(".")[0], a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相对 import：只可能是同级 services 模块
                mod = node.module or ""
                top = (mod.split(".")[0] or "services")
                out.append((top, "." * node.level + mod, node.lineno))
                continue
            mod = node.module or ""
            out.append((mod.split(".")[0], mod, node.lineno))
    return out


def check_module_purity(c: Checker) -> None:
    if not DERIVE_MODULE.is_file():
        c.check(False, "R5.1 决策入口存在", f"缺 {DERIVE_MODULE.relative_to(ROOT)}")
        return
    bad = []
    for top, full, line in _imports_of(DERIVE_MODULE):
        if top in FORBIDDEN_IMPORTS:
            bad.append(f"{full}:{line}")
        elif top == "services" and full.split(".")[-1] in FORBIDDEN_SERVICES:
            bad.append(f"{full}:{line}")
    c.check(not bad, "R5.1 决策入口只依赖纯模块（无宿主 / 通道 / 网络）", "、".join(bad))


def check_caller_warnings(c: Checker) -> None:
    """R5.4：拆分后调用方仍按**改造前的原话**把决策 notes 折成 warnings。

    这条守的是一个真踩过的坑：把 1a/1b 搬进决策入口时，很容易连「补层了要说一声」
    一起搬丢 —— 8 份 spec 恰好都不触发补层（tht_layers 全空），所以 R5.2/R5.3 的
    逐字段对拍抓不到，报告里会静默少一条提示。这里用源码证据兜住两条文案与两个键名。
    """
    builder = ROOT / "services" / "footprint_builder.py"
    if not builder.is_file():
        c.check(False, "R5.4 调用方 warnings 口径不变", "找不到 services/footprint_builder.py")
        return
    text = builder.read_text(encoding="utf-8")
    need = [
        '"约定引擎: "',
        '"TH padstack 自动补层（spec 漏 BOTTOM，已镜像 TOP 兜底）: "',
        '_notes["conventions"]',
        '_notes["tht_layers"]',
    ]
    missing = [s for s in need if s not in text]
    c.check(not missing, "R5.4 调用方 warnings 口径不变（两条原话 + 两个键名）",
            "源码里搜不到：" + "、".join(missing))


def check_tht_topup(c: Checker) -> None:
    """R5.5：通孔补层分支搬进决策入口后仍然生效。

    8 份参数档都是「层写全了」或非通孔件，`tht_layers` 全为空 —— 也就是说这条分支
    在对拍里零覆盖：搬丢、搬坏都不会让 R5.2/R5.3 变红。这里用一份合成的 TH 读数
    （只给 TOP + SOLDERMASK_TOP）直接验证「补两层 + 报两条」，与改造前的原话对齐。
    """
    from services.padstack_derive import derive_padstacks

    synth = {
        "mount": "tht",
        "padstack": {
            "name": "R5SYNTH",
            "drill": {"size": 0.3},
            "layers": [
                {"layer": "TOP", "size": [1.0, 1.0]},
                {"layer": "SOLDERMASK_TOP", "size": [1.1, 1.1]},
            ],
        },
        "extra_padstacks": [],
        "pins_override": [],
    }
    out = derive_padstacks(copy.deepcopy(synth))
    layers = [str(ld.get("layer", "")).upper() for ld in (out["padstack"] or {}).get("layers", [])]
    notes = out["notes"]["tht_layers"]
    want = [
        "R5SYNTH: 补 BOTTOM(镜像 TOP)",
        "R5SYNTH: 补 SOLDERMASK_BOTTOM(镜像 TOP)",
    ]
    ok = (
        "BOTTOM" in layers
        and "SOLDERMASK_BOTTOM" in layers
        and notes == want
        and synth["padstack"]["layers"] == [
            {"layer": "TOP", "size": [1.0, 1.0]},
            {"layer": "SOLDERMASK_TOP", "size": [1.1, 1.1]},
        ]          # 入参不该被就地补层
    )
    c.check(ok, "R5.5 通孔补层分支仍生效（合成读数；8 份 spec 覆盖不到）",
            f"layers={layers} notes={notes}")


def _spec_paths() -> List[Path]:
    return [Path(p) for p in sorted(glob.glob(str(SPECS_DIR / "*.json")))
            if not os.path.basename(p).startswith("_")]


def _diff(path: str, expected: Any, actual: Any, out: List[str]) -> None:
    """把两棵结构的差异压成少量可读行（截断到 6 条，避免刷屏）。"""
    if expected == actual:
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                out.append(f"{path}.{key} 多出")
            elif key not in actual:
                out.append(f"{path}.{key} 丢失")
            else:
                _diff(f"{path}.{key}", expected[key], actual[key], out)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append(f"{path} 长度 {len(expected)} → {len(actual)}")
        else:
            for i, (e, a) in enumerate(zip(expected, actual)):
                _diff(f"{path}[{i}]", e, a, out)
                if len(out) >= 6:
                    return
    else:
        out.append(f"{path}: 基线 {expected!r} → 实际 {actual!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="R5 padstack 决策入口对拍")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    c = Checker()
    check_module_purity(c)
    check_caller_warnings(c)
    check_tht_topup(c)

    if not BASELINE.is_file():
        print(f"缺基线：{BASELINE}")
        print(f"结果: 0/{len(c.items)}")
        return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    base_specs = base.get("specs") or {}

    from services import geometry_solver as GS
    from services.padstack_derive import derive_padstacks, reading_from_output

    for path in _spec_paths():
        name = path.name
        expect = base_specs.get(name)
        if expect is None:
            c.check(False, f"R5 基线覆盖 {name}", "基线里没有这份 spec")
            continue

        record = json.loads(path.read_text(encoding="utf-8"))
        spec = record.get("spec")
        if not isinstance(spec, dict):
            c.check(False, f"R5 读数可读 {name}", "spec 库记录里没有 spec 段")
            continue

        # ---- R5.2 求解输出逐字段 ----
        solved = GS.solve(copy.deepcopy(spec))
        diffs: List[str] = []
        _diff("solve", expect.get("solve"), solved, diffs)
        c.check(not diffs, f"R5.2 求解输出不变 {name}", "；".join(diffs[:6]))

        # ---- R5.3 派生输出逐字段 + 入参不变 ----
        # 读数映射与生产侧**同一个函数**（services/padstack_derive.reading_from_output），
        # 否则生产侧改了映射、这里照旧通过，对拍就成了摆设。
        reading = reading_from_output(solved)
        frozen_reading = json.dumps(reading, ensure_ascii=False, sort_keys=True)
        derived = derive_padstacks(reading)
        got = {
            "padstack": derived.get("padstack"),
            "extra_padstacks": derived.get("extra_padstacks"),
            "pins_override": derived.get("pins_override"),
            "notes": {
                "conventions": (derived.get("notes") or {}).get("conventions"),
                "tht_layers": (derived.get("notes") or {}).get("tht_layers"),
            },
        }
        # 基线里 extra_padstacks / pins_override 可能是 None —— 那是改造前「透传求解结果」
        # 的旧变量状态（求解读数里没有这个键）。决策入口统一回 list（None ≡ 无附加
        # padstack），两者下游等价（调用方一律 `for x in (extras or [])`）。这里只做这一处
        # 同义归一，其余字段逐字比。
        expect_derive = copy.deepcopy(expect.get("derive"))
        if isinstance(expect_derive, dict):
            for _k in ("extra_padstacks", "pins_override"):
                if expect_derive.get(_k) is None:
                    expect_derive[_k] = []
        diffs = []
        _diff("derive", expect_derive, got, diffs)
        if json.dumps(reading, ensure_ascii=False, sort_keys=True) != frozen_reading:
            diffs.append("决策入口就地修改了入参（决策应只读）")
        if (derived.get("notes") or {}).get("errors"):
            diffs.append(f"notes.errors 非空：{derived['notes']['errors']}")
        c.check(not diffs, f"R5.3 派生输出不变 {name}", "；".join(diffs[:6]))

    total = len(c.items)
    if args.json:
        print(json.dumps({"passed": c.n_pass, "total": total, "items": c.items},
                         ensure_ascii=False, indent=2))
    else:
        for f in c.fails:
            print(f"[FAIL] {f}")
        for it in c.items:
            print(("  ok  " if it["ok"] else "  FAIL") + "  " + it["desc"])
    print(f"结果: {c.n_pass}/{total}")
    return 0 if c.n_pass == total else 1


if __name__ == "__main__":
    sys.exit(main())
