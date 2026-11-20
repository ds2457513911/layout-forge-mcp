# -*- coding: utf-8 -*-
"""R6 · 规则库与判定引擎（票 #17「规则库骨架 + 阻焊一条走通判定」的验收）

判据（R6.1–R6.6）：

  R6.1 加载与确定性 —— knowledge/rules 的四类对象（form / parameter / rule /
       source）能整体加载，算出整库哈希；同输入两次加载 → 指纹相同、canonical
       输出逐字节相同；同一 subject 判两次 → 决策逐字节相同。
       对应 AC：「合法规则库可加载并算出哈希，同输入两次运行输出逐字节相同」。

  R6.2 加载即整体失败 —— 未知谓词 / 缺出处 / 参数引用不存在 / id 重复 /
       缺必填字段 / 未知断言种类，六类各造一个坏库：必须抛 RuleLibraryError，
       且 errors 里逐条带 file + path（指向具体对象与字段）。绝不半载。
       对应 AC：「未知谓词、缺出处、参数引用不存在时加载失败并报出具体对象与字段」。

  R6.3 判定三态 —— 阻焊规则在三种形态（通孔长圆 / 通孔圆 / SMD）上给出
       PASS / WARN；观测值缺失 → SKIP 且写明「输入 UNKNOWN」；形态匹配不上
       → SKIP 且写明「缺 form」。SKIP 不是通过。
       对应规格三态：满足 / 违反 / SKIP（SKIP 必须写明缺什么）。

  R6.4 走新库 —— 用 LAYOUT_FORGE_RULES_DIR 指到一份临时库（把 SMD 的
       allowed 改成 [0.2, 0.3]），同一份真实 payload 的 2.2 必须从 PASS 变成
       WARN；finding 带 rule_id / source_ref / support / rule_set_hash。
       对应 AC：「阻焊判定走新库，finding 带规则 id、出处与支持件数」。

  R6.5 状态一致 —— 冻结夹具（真实 .dra 的 pins 段 + 改动前的旧 2.2 状态）
       逐件跑新路径，2.2 状态必须与旧判定逐件相等。
       对应 AC：「该条判定与旧 2.2 在同一批封装上状态一致」。

  R6.6 旧阈值文件只读 —— knowledge/rules/checker_rules.json 的 sha256 等于
       基线冻结值；模块纯度（services/rule_engine.py 不碰宿主/网络/进程）。
       对应 AC：「旧阈值文件保持只读，未被改写」。

基线来历：eval/baselines/rule_engine.json 由一次性脚本（未进仓）
在**改造前**的 HEAD 上冻结（当时 2.2 还是内联阈值判定），fixtures 里的
status22 就是那份旧判定的原话。

跑法（项目根，离线、不需要 Allegro）：
  python eval/suites/geometry_regression/check_rule_engine.py
  python eval/suites/geometry_regression/check_rule_engine.py --json
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

BASELINE = ROOT / "eval" / "baselines" / "rule_engine.json"
RULES_DIR = ROOT / "knowledge" / "rules"
ENGINE_MODULE = ROOT / "services" / "rule_engine.py"
LEGACY_RULES = RULES_DIR / "checker_rules.json"


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


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_lib(dst: Path, mutate) -> Path:
    """把 knowledge/rules 拷到临时目录，交给 mutate 改坏一处，返回库目录。"""
    for src in RULES_DIR.glob("*.json"):
        shutil.copy2(src, dst / src.name)
    mutate(dst)
    return dst


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _item_22(items: list) -> dict:
    """按 id 取 2.2，避免用列表下标（前面插项就测错对象）。"""
    return next(i for i in items if i.get("id") == "2.2")


def _dump_json(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- R6.1
def check_load_and_determinism(c: Checker, RE) -> Any:
    lib = RE.load_library(RULES_DIR)
    fp = getattr(lib, "fingerprint", None)
    c.check(isinstance(fp, str) and len(fp) == 64,
            "R6.1 整库哈希是 64 位 hex", f"实际 {fp!r}")
    RE.reset_cache()          # 清缓存，逼第二次从磁盘重新加载
    lib2 = RE.load_library(RULES_DIR)
    c.check(lib2.fingerprint == fp, "R6.1 两次加载指纹相同", f"{fp} vs {lib2.fingerprint}")
    c.check(RE.canonical_json(lib.dump()) == RE.canonical_json(lib2.dump()),
            "R6.1 canonical 输出逐字节相同")

    # 「两次运行」按两个独立进程验：各自从磁盘加载，stdout 逐字节比
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    probe = (
        "import sys; sys.path.insert(0, %r);"
        "from services import rule_engine as RE;"
        "lib = RE.load_library(%r);"
        "sys.stdout.write(RE.canonical_json({'fingerprint': lib.fingerprint, 'library': lib.dump()}))"
    ) % (str(ROOT), str(RULES_DIR))
    runs = [subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8", env=env)
            for _ in range(2)]
    c.check(all(r.returncode == 0 for r in runs), "R6.1 两次独立进程加载均成功",
            "；".join((r.stderr or "").strip()[-300:] for r in runs if r.returncode))
    c.check(bool(runs[0].stdout) and runs[0].stdout == runs[1].stdout,
            "R6.1 两次独立进程输出逐字节相同",
            f"{len(runs[0].stdout)} vs {len(runs[1].stdout)} 字节")

    subject = {"id": "1", "mount": "smd", "layers": ["ETCH/TOP", "PIN/SOLDERMASK_TOP"],
               "drill": None, "name": "R0D2X0D6", "pad_count": 8}
    observed = {"expand_x": 0.08, "expand_y": 0.08}
    d1 = RE.judge(lib, "RULE-MASK-EXPAND", subject, observed)
    d2 = RE.judge(lib, "RULE-MASK-EXPAND", subject, observed)
    c.check(RE.canonical_json(d1) == RE.canonical_json(d2),
            "R6.1 同一 subject 判两次决策逐字节相同")
    c.check(d1["status"] == "PASS" and d1["rule_id"] == "RULE-MASK-EXPAND",
            "R6.1 SMD 典型值 0.08 → PASS", json.dumps(d1, ensure_ascii=False)[:300])
    c.check(d1["form"] == "FORM-MASK-SMD", "R6.1 形态归属 = FORM-MASK-SMD",
            str(d1.get("form")))
    c.check(d1.get("source") and d1.get("support"),
            "R6.1 决策带出处与支持件数",
            f"source={d1.get('source')!r} support={d1.get('support')!r}")
    return lib


# ---------------------------------------------------------------- R6.2
def check_load_failures(c: Checker, RE) -> None:
    def forms_mutate(dst: Path) -> None:
        p = dst / "forms.json"
        data = _load_json(p)
        data["items"][0]["when"]["all"][0]["predicate"] = "no_such_predicate"
        _dump_json(p, data)

    def source_mutate(dst: Path) -> None:
        p = dst / "parameters.json"
        data = _load_json(p)
        data["items"][0].pop("source", None)
        _dump_json(p, data)

    def param_ref_mutate(dst: Path) -> None:
        p = dst / "rules.json"
        data = _load_json(p)
        data["items"][0]["use"][0] = "PARAM-DOES-NOT-EXIST"
        _dump_json(p, data)

    def dup_mutate(dst: Path) -> None:
        p = dst / "sources.json"
        data = _load_json(p)
        data["items"].append(copy.deepcopy(data["items"][0]))
        _dump_json(p, data)

    def field_mutate(dst: Path) -> None:
        p = dst / "parameters.json"
        data = _load_json(p)
        data["items"][0].pop("value", None)
        _dump_json(p, data)

    def assert_mutate(dst: Path) -> None:
        p = dst / "rules.json"
        data = _load_json(p)
        data["items"][0]["assert"][0]["kind"] = "no_such_assert"
        _dump_json(p, data)

    cases = [
        ("未知谓词", forms_mutate, "no_such_predicate", "forms.json"),
        ("缺出处", source_mutate, "source", "parameters.json"),
        ("参数引用不存在", param_ref_mutate, "PARAM-DOES-NOT-EXIST", "rules.json"),
        ("id 重复", dup_mutate, "SRC-", "sources.json"),
        ("缺必填字段", field_mutate, "value", "parameters.json"),
        ("未知断言种类", assert_mutate, "no_such_assert", "rules.json"),
    ]
    for label, mutate, needle, expect_file in cases:
        tmp = Path(tempfile.mkdtemp(prefix="r6_bad_"))
        try:
            d = _write_lib(tmp, mutate)
            try:
                RE.load_library(d)
                c.check(False, f"R6.2 {label} → 必须拒绝加载", "竟然加载成功")
            except RE.RuleLibraryError as exc:
                errs = getattr(exc, "errors", None) or []
                hit = [e for e in errs if str(e.get("file", "")) == expect_file]
                txt = json.dumps(errs, ensure_ascii=False)
                c.check(bool(hit) and needle in txt
                        and all(e.get("path") for e in errs),
                        f"R6.2 {label} → 报出文件/对象/字段",
                        txt[:300])
            except Exception as exc:                       # noqa: BLE001
                c.check(False, f"R6.2 {label} → 抛的是 RuleLibraryError",
                        f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # 错误信息要带对象 id，库大时不用人去数 items[i]
    tmp = Path(tempfile.mkdtemp(prefix="r6_objid_"))
    try:
        d = _write_lib(tmp, source_mutate)
        try:
            RE.load_library(d)
            c.check(False, "R6.2 缺出处 → 错误带对象 id", "竟然加载成功")
        except RE.RuleLibraryError as exc:
            txt = json.dumps(getattr(exc, "errors", []), ensure_ascii=False)
            c.check("PARAM-MASK-EXPAND" in txt,
                    "R6.2 缺出处 → 错误带对象 id", txt[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- R6.3
def check_three_states(c: Checker, RE, lib) -> None:
    sub_th_oblong = {"id": "1", "mount": "tht",
                     "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                     "drill": {"shape": "OVAL SLOT"}, "name": "POB1D7X0D5HOB2D3X1D1",
                     "pad_count": 4}
    sub_th_circle = {"id": "2", "mount": "tht",
                     "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                     "drill": {"shape": "CIRCLE_DRILL"}, "name": "PC1D0HC2D0",
                     "pad_count": 2}
    sub_smd = {"id": "3", "mount": "smd", "layers": ["ETCH/TOP", "PIN/SOLDERMASK_TOP"],
               "drill": None, "name": "R0D2X0D6", "pad_count": 8}
    support_oblong = lib.parameters_by_id["PARAM-MASK-EXPAND-TH-OBLONG"]["support"]
    support_circle = lib.parameters_by_id["PARAM-MASK-EXPAND-TH-CIRCLE"]["support"]

    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_th_oblong, {"expand_x": 0.05, "expand_y": 0.05})
    c.check(d["form"] == "FORM-MASK-TH-OBLONG" and d["status"] == "PASS"
            and d["support"] == support_oblong,
            f"R6.3 通孔长圆 0.05 → PASS，支持件数 {support_oblong}", json.dumps(d, ensure_ascii=False)[:260])

    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_th_circle, {"expand_x": 0.05, "expand_y": 0.05})
    c.check(d["form"] == "FORM-MASK-TH-CIRCLE" and d["status"] == "PASS"
            and d["support"] == support_circle,
            f"R6.3 通孔圆 0.05 → PASS，支持件数 {support_circle}", json.dumps(d, ensure_ascii=False)[:260])

    # 边界：allowed=[0.05,0.13] + eps 0.005 → 0.134 PASS、0.14 WARN
    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_smd, {"expand_x": 0.134, "expand_y": 0.134})
    c.check(d["status"] == "PASS", "R6.3 边界 0.134（eps 内）→ PASS",
            json.dumps(d, ensure_ascii=False)[:260])
    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_smd, {"expand_x": 0.14, "expand_y": 0.08})
    c.check(d["status"] == "WARN" and d.get("severity") == "WARN",
            "R6.3 越界 0.14 → WARN（severity 来自映射表）",
            json.dumps(d, ensure_ascii=False)[:260])

    # 观测缺失 → SKIP + 输入 UNKNOWN
    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_smd, {"expand_x": 0.08})
    c.check(d["status"] == "SKIP" and "UNKNOWN" in str(d.get("detail", "")),
            "R6.3 观测缺失 → SKIP 且写明输入 UNKNOWN",
            json.dumps(d, ensure_ascii=False)[:260])

    # 形态匹配不上 → SKIP + 缺 form
    sub_unknown = {"id": "4", "mount": "tht",
                   "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                   "drill": {"shape": "SQUARE_DRILL"}, "name": "X", "pad_count": 2}
    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_unknown, {"expand_x": 0.08, "expand_y": 0.08})
    c.check(d["status"] == "SKIP" and "缺 form" in str(d.get("detail", "")),
            "R6.3 无形态可匹配 → SKIP 且写明缺 form",
            json.dumps(d, ensure_ascii=False)[:260])


# ---------------------------------------------------------------- R6.4
def _smd_payload() -> dict:
    """一份最小但真实的 SMD payload（形状取自 NB_QFN56 的首个 pin）。"""
    return {"pins": [{
        "number": "1", "name": "R0D2X0D6", "rotation": 0.0,
        "bbox": [[0.0, 0.0], [0.2, 0.6]],
        "pads": [
            {"layer": "ETCH/TOP", "figure_name": "RECTANGLE", "size": [0.2, 0.6],
             "bbox": [[0.0, 0.0], [0.2, 0.6]]},
            {"layer": "PIN/SOLDERMASK_TOP", "figure_name": "RECTANGLE", "size": [0.3, 0.7],
             "bbox": [[0.0, 0.0], [0.3, 0.7]]},
        ],
    }]}


def check_wired_to_new_library(c: Checker, RE) -> None:
    from services import rule_checker as RC

    os.environ.pop("LAYOUT_FORGE_RULES_DIR", None)
    RE.reset_cache()
    item = _item_22(RC.check_pad_size(_smd_payload(), None, RC.DEFAULT_RULES))
    c.check(item["id"] == "2.2" and item["status"] == "PASS",
            "R6.4 基本件 2.2 → PASS", json.dumps(item, ensure_ascii=False)[:300])
    c.check(item.get("rule_id") == "RULE-MASK-EXPAND",
            "R6.4 finding 带 rule_id", str(item.get("rule_id")))
    c.check(bool(item.get("source_ref")) and item.get("support") is not None,
            "R6.4 finding 带出处与支持件数",
            f"source_ref={item.get('source_ref')!r} support={item.get('support')!r}")
    c.check(item.get("rule_source") == "SRC-ADR-0002",
            "R6.4 finding 带规则出处（不是只有参数出处）", str(item.get("rule_source")))
    c.check(bool(item.get("support_by_form")),
            "R6.4 finding 带按形态分组的支持件数", str(item.get("support_by_form")))
    c.check(bool(item.get("rule_set_hash")),
            "R6.4 finding 带整库哈希", str(item.get("rule_set_hash")))

    mixed = _smd_payload()
    mixed["pins"].append({
        "number": "2", "name": "POB1D7X0D5HOB2D3X1D1", "rotation": 0.0,
        "bbox": [[0.0, 0.0], [1.2, 2.4]],
        "pads": [
            {"layer": "ETCH/TOP", "figure_name": "OBLONG", "size": [1.1, 2.3],
             "bbox": [[0.0, 0.0], [1.1, 2.3]]},
            {"layer": "PIN/SOLDERMASK_TOP", "figure_name": "OBLONG", "size": [1.2, 2.4],
             "bbox": [[0.0, 0.0], [1.2, 2.4]]},
            {"layer": "DRILL", "figure_name": "OBLONG_Y", "size": [0.7, 1.9],
             "hole_type": "OVAL SLOT"},
        ],
    })
    item_m = _item_22(RC.check_pad_size(mixed, None, RC.DEFAULT_RULES))
    c.check(set(item_m.get("support_by_form") or {}) == {
                "FORM-MASK-TH-OBLONG", "FORM-MASK-SMD"}
            and item_m.get("support") == sum(item_m["support_by_form"].values()),
            "R6.4 混合形态 finding 按形态分组且 support 为合计",
            json.dumps(item_m.get("support_by_form"), ensure_ascii=False))

    # 临时库把 SMD 的 allowed 收窄到 [0.2,0.3] → 同一 payload 必须转 WARN
    tmp = Path(tempfile.mkdtemp(prefix="r6_wide_"))
    try:
        _write_lib(tmp, lambda d: None)
        p = tmp / "parameters.json"
        data = _load_json(p)
        for obj in data["items"]:
            if obj["id"] == "PARAM-MASK-EXPAND-SMD":
                obj["allowed"] = [0.2, 0.3]
                obj["value"] = 0.25
        _dump_json(p, data)
        os.environ["LAYOUT_FORGE_RULES_DIR"] = str(tmp)
        RE.reset_cache()
        item2 = _item_22(RC.check_pad_size(_smd_payload(), None, RC.DEFAULT_RULES))
        c.check(item2["status"] == "WARN",
                "R6.4 改库即改判定（证明 2.2 走新库，不是读死阈值）",
                f"状态 {item2['status']}，rule={item2.get('rule')!r}")
    finally:
        os.environ.pop("LAYOUT_FORGE_RULES_DIR", None)
        RE.reset_cache()
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- R6.5
def check_status_parity(c: Checker, RE, base: dict) -> None:
    from services import rule_checker as RC

    fixtures = base.get("fixtures") or []
    c.check(bool(fixtures), "R6.5 基线里有冻结夹具", f"{len(fixtures)} 件")
    for fx in fixtures:
        name = fx.get("file")
        payload = {"pins": fx.get("pins") or []}
        item = _item_22(RC.check_pad_size(payload, None, RC.DEFAULT_RULES))
        got, want = item.get("status"), fx.get("status22")
        c.check(got == want, f"R6.5 {name} 2.2 状态一致（旧 {want}）",
                f"新 {got}；detail={item.get('detail')!r}")
        forms = item.get("forms")
        c.check(forms == fx.get("forms"), f"R6.5 {name} 形态归属一致",
                f"基线 {fx.get('forms')} → 实际 {forms}")

    shadow_path = BASELINE.parent / "shadow_2_2_253.json"
    try:
        shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        shadow = {}
    summ = shadow.get("summary") or {}
    c.check(summ.get("files") == 253 and summ.get("judged") == 235
            and summ.get("diff") == 0 and len(shadow.get("rows") or []) == 253,
            "R6.5 253 件 shadow 记录在案（差异 0）",
            f"files={summ.get('files')} judged={summ.get('judged')} "
            f"diff={summ.get('diff')} rows={len(shadow.get('rows') or [])}")


# ---------------------------------------------------------------- R6.6
def check_frozen_legacy_and_purity(c: Checker, base: dict) -> None:
    want = base.get("legacy_rules_sha256")
    got = _sha256_file(LEGACY_RULES) if LEGACY_RULES.is_file() else None
    c.check(got == want, "R6.6 旧阈值文件只读（sha256 未变）",
            f"基线 {want} → 实际 {got}")

    tree = ast.parse(ENGINE_MODULE.read_text(encoding="utf-8"), filename=str(ENGINE_MODULE))
    banned = {"skillbridge", "win32com", "pythoncom", "socket", "requests", "urllib",
              "http", "fitz", "pymupdf", "pdfplumber", "PIL", "fastmcp", "mcp_server",
              "orchestration", "subprocess"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in banned:
                    bad.append(f"{a.name}:{node.lineno}")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mod = node.module or ""
            if mod.split(".")[0] in banned:
                bad.append(f"{mod}:{node.lineno}")
    c.check(not bad, "R6.6 引擎模块纯净（不碰宿主/网络/进程）", "、".join(bad))


def main() -> int:
    ap = argparse.ArgumentParser(description="R6 规则库与判定引擎")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    c = Checker()
    if not BASELINE.is_file():
        print(f"缺基线：{BASELINE}")
        print("结果: 0/1")
        return 1
    base = json.loads(BASELINE.read_text(encoding="utf-8"))

    try:
        from services import rule_engine as RE
    except Exception as exc:                               # noqa: BLE001
        print(f"[FAIL] services/rule_engine.py 导入失败：{type(exc).__name__}: {exc}")
        print("结果: 0/1")
        return 1

    lib = check_load_and_determinism(c, RE)
    check_load_failures(c, RE)
    check_three_states(c, RE, lib)
    check_wired_to_new_library(c, RE)
    check_status_parity(c, RE, base)
    check_frozen_legacy_and_purity(c, base)

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
