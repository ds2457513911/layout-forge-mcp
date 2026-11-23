# -*- coding: utf-8 -*-
"""R6 · 规则库与判定引擎（票 #17「规则库骨架 + 阻焊一条走通判定」的验收）

判据（R6.1–R6.14）：

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

  R6.7 覆盖率两类缺口（票 #21）—— 静态缺口（有形态但无规则）来自 form 表 ×
        规则表差集，静态就能算；运行时缺口（无形态）只有跑输入才知道；两类
        分开列。未覆盖形态的输入必须 SKIP 并写明原因，不得判 PASS；finding
        带方向标签与规则集版本。
        对应 AC：「报告能回答哪些形态没有规则、哪些输入连形态都匹配不上，两类
        分开」「未覆盖形态的输入记 SKIP 并带原因，不判 PASS」。

  R6.8 报告字段（票 #21）—— 首屏有规则集版本 + 哈希；覆盖率表与两类缺口行
        在首屏；缺新字段的旧 numeric_result 仍能渲染；JSON 报告两个生产方共用
        同一份定义，新旧报告都能被 observability.stats 解析。
        对应 AC：「规则集版本与哈希出现在报告首屏，历史报告解析不因新增字段失败」。

  R6.9 哈希随改动变（票 #21）—— 改参数区间 / 改规则文案 / 改版本号任一处，
        整库哈希都必须变；报告里的哈希与覆盖率里的 rule_set_hash 同源。
        对应 AC：「规则集哈希随任一条规则或参数改动而变化」。

  R6.10 覆盖率与语料对账（票 #21）—— 每形态 support 与 conventions 的裁定
        支撑件数一致；冻结夹具上 coverage 的逐形态命中与独立谓词计数相等、
        合计 = 有焊盘的 pin 数；语料里出现过的形态都在形态表里，无形态的
        样本数也对得上。对应 AC：「覆盖率数字可与语料统计（各形态件数）对上」。

  R6.11 参数单一定义（票 #19）—— 阻焊外扩只有一个参数定义，区间只在参数级
        写一次，三个形态各一组，组里不带区间。
        对应 AC：「同一事实只有一处定义」。

  R6.12 一处改动两侧同步（票 #19）—— 改组里的典型值，check 侧与 create 侧
        同时跟着变；只改一侧不成立。

  R6.13 迁移映射表（票 #19）—— 每个旧阈值键、每条旧检查器规则各有一个归属，
        台账渲染出的文档与 docs 里的逐字节相同。

  R6.14 回滚演练（票 #19）—— 照 rollback_recipe 把库展开回旧形态后，用同一批
        夹具重判，结果与旧 2.2 状态一致。

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
import importlib.util
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
MIGRATION = ROOT / "knowledge" / "migrations" / "rules_v1.json"
MIGRATION_DOC = ROOT / "docs" / "迁移映射表-规则集v1.md"
MASK_PARAM = "PARAM-MASK-EXPAND"
MASK_FORMS = ("FORM-MASK-TH-OBLONG", "FORM-MASK-TH-CIRCLE", "FORM-MASK-SMD")


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
        # 票 #19 起参数按形态分组：出处写在每个形态组上（参数级不再有 source）
        p = dst / "parameters.json"
        data = _load_json(p)
        data["items"][0]["groups"][0].pop("source", None)
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
        # 同上：典型值在形态组上
        p = dst / "parameters.json"
        data = _load_json(p)
        data["items"][0]["groups"][0].pop("value", None)
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
    # 票 #19：三形态收成一个参数，支持件数按形态组取
    mask_param = lib.parameters_by_id["PARAM-MASK-EXPAND"]
    support_oblong = next(g["support"] for g in mask_param["groups"]
                          if g["form"] == "FORM-MASK-TH-OBLONG")
    support_circle = next(g["support"] for g in mask_param["groups"]
                          if g["form"] == "FORM-MASK-TH-CIRCLE")

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

    # 有形态但还没有规则 → SKIP + 缺规则（覆盖率缺口，不能判 PASS）
    sub_no_rule = {"id": "4", "mount": "tht",
                   "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                   "drill": {"shape": "SQUARE_DRILL"}, "name": "X", "pad_count": 2}
    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_no_rule, {"expand_x": 0.08, "expand_y": 0.08})
    c.check(d["status"] == "SKIP" and "缺规则" in str(d.get("detail", ""))
            and d.get("form") == "FORM-TH-DRILL-OTHER",
            "R6.3 有形态但无规则 → SKIP 且写明缺规则",
            json.dumps(d, ensure_ascii=False)[:260])

    # 真·无形态可匹配 → SKIP + 缺 form
    sub_unknown = {"id": "5", "mount": "tht",
                   "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                   "drill": {"shape": "TRIANGLE_DRILL"}, "name": "X", "pad_count": 2}
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

    # 临时库把参数区间收窄到 [0.2,0.3]（各组典型值同步改 0.25）
    # → 同一 payload 必须转 WARN
    tmp = Path(tempfile.mkdtemp(prefix="r6_wide_"))
    try:
        _write_lib(tmp, lambda d: None)
        p = tmp / "parameters.json"
        data = _load_json(p)
        for obj in data["items"]:
            if obj["id"] == "PARAM-MASK-EXPAND":
                obj["allowed"] = [0.2, 0.3]
                for grp in obj.get("groups") or []:
                    grp["value"] = 0.25
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



# ---------------------------------------------------------------- R6.7
def check_coverage(c: Checker, RE, lib) -> None:
    cov = RE.coverage(lib, [])
    c.check(cov.get("schema") == "layout-forge/coverage@1",
            "R6.7 覆盖率段有 schema 标识", str(cov.get("schema")))
    gaps = cov.get("gaps") or {}
    c.check(gaps.get("forms_without_rule") == ["FORM-TH-DRILL-OTHER"],
            "R6.7 静态缺口 = 有形态但无规则（form 表 × 规则表差集）",
            json.dumps(gaps.get("forms_without_rule"), ensure_ascii=False))
    c.check(gaps.get("inputs_without_form") == [],
            "R6.7 没跑输入时不报无形态（运行时才知道）",
            json.dumps(gaps.get("inputs_without_form"), ensure_ascii=False)[:200])
    c.check(sorted(gaps.keys()) == ["forms_without_rule", "inputs_without_form"],
            "R6.7 两类缺口分开列（键不同、含义不同）", json.dumps(sorted(gaps.keys())))

    sub_other = {"id": "a", "mount": "tht",
                 "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                 "drill": {"shape": "SQUARE_DRILL"}, "name": "X", "pad_count": 1}
    sub_smd = {"id": "b", "mount": "smd", "layers": ["ETCH/TOP", "PIN/SOLDERMASK_TOP"],
               "drill": None, "name": "R0D2", "pad_count": 1}
    sub_unknown = {"id": "c", "mount": "tht",
                   "layers": ["DRILL", "ETCH/TOP", "PIN/SOLDERMASK_TOP"],
                   "drill": {"shape": "TRIANGLE_DRILL"}, "name": "X", "pad_count": 1}
    cov = RE.coverage(lib, [sub_smd, sub_other, sub_unknown])
    c.check(cov["total"] == {"subjects": 3, "matched": 2, "unmatched": 1},
            "R6.7 运行时合计：命中 2、无形态 1", json.dumps(cov["total"]))
    forms = {r["form"]: r for r in cov["forms"]}
    other = forms.get("FORM-TH-DRILL-OTHER") or {}
    c.check(other.get("hits") == 1 and other.get("status") == "uncovered",
            "R6.7 未覆盖形态在覆盖率里可见（hits 1 / status uncovered）",
            json.dumps(other, ensure_ascii=False)[:240])
    # 当前规则只有 assert 方向（derive 留给后续票），所以有判定规则的形态是 assert_only
    c.check(forms.get("FORM-MASK-SMD", {}).get("status") == "assert_only",
            "R6.7 只有判定规则的形态 status=assert_only",
            str(forms.get("FORM-MASK-SMD", {}).get("status")))
    c.check("forms_without_derive_rule" not in cov["gaps"],
            "R6.7 缺口只列规格要求的两类（派生侧留到 #20，不混进缺口）",
            json.dumps(sorted(cov["gaps"])))
    wo = cov["gaps"]["inputs_without_form"]
    c.check(len(wo) == 1 and "TRIANGLE_DRILL" in json.dumps(wo, ensure_ascii=False),
            "R6.7 无形态的输入列进运行时缺口并带原因",
            json.dumps(wo, ensure_ascii=False)[:260])
    c.check(bool(wo[0].get("reason")) and wo[0].get("count") == 1,
            "R6.7 无形态缺口带原因与计数", json.dumps(wo[0], ensure_ascii=False)[:200])

    d = RE.judge(lib, "RULE-MASK-EXPAND", sub_other, {"expand_x": 0.08, "expand_y": 0.08})
    c.check(d["status"] == "SKIP" and "缺规则" in str(d.get("detail", "")),
            "R6.7 未覆盖形态的输入 → SKIP（不判 PASS）",
            json.dumps(d, ensure_ascii=False)[:240])
    c.check(d.get("direction") == "assert" and d.get("rule_set_version") == lib.version,
            "R6.7 SKIP finding 也带方向与规则集版本",
            f"{d.get('direction')} / {d.get('rule_set_version')}")
    d2 = RE.judge(lib, "RULE-MASK-EXPAND", sub_smd, {"expand_x": 0.08, "expand_y": 0.08})
    c.check(d2.get("direction") == "assert" and d2.get("rule_set_version") == lib.version
            and d2.get("rule_set_hash") == lib.fingerprint,
            "R6.7 PASS finding 带方向、版本、哈希（同源）",
            f"{d2.get('direction')} / {d2.get('rule_set_version')} / {str(d2.get('rule_set_hash'))[:12]}")
    d3 = RE.judge(lib, "RULE-MASK-EXPAND", sub_unknown, {"expand_x": 0.08, "expand_y": 0.08})
    c.check(d3["status"] == "SKIP" and "缺 form" in str(d3.get("detail", "")),
            "R6.7 真无形态 → SKIP 且写明缺 form",
            json.dumps(d3, ensure_ascii=False)[:240])

    info = RE.rule_set_info(lib)
    c.check(info.get("version") == lib.version and info.get("hash") == lib.fingerprint
            and info.get("counts", {}).get("forms", 0) >= 4,
            "R6.7 rule_set_info 带版本/哈希/对象计数", json.dumps(info, ensure_ascii=False)[:220])


# ---------------------------------------------------------------- R6.8
def check_report_fields(c: Checker, RE) -> None:
    from services import rule_checker as RC
    from services import report_renderer as RR

    os.environ.pop("LAYOUT_FORGE_RULES_DIR", None)
    RE.reset_cache()
    lib = RE.load_library()
    res = RC.run_numeric_checks(_smd_payload(), None, RC.DEFAULT_RULES)
    c.check(bool(res.get("rule_set")) and res["rule_set"].get("version") == lib.version
            and res["rule_set"].get("hash") == lib.fingerprint,
            "R6.8 numeric_result 带规则集版本与哈希")
    c.check((res.get("coverage") or {}).get("total", {}).get("subjects") == 1,
            "R6.8 numeric_result 带覆盖率段",
            json.dumps((res.get("coverage") or {}).get("total"), ensure_ascii=False))

    md = RR.render_markdown(res)
    head = md.split("| 大项", 1)[0]
    c.check(f"规则集 v{lib.version}" in head and lib.fingerprint[:12] in head,
            "R6.8 报告首屏有规则集版本 + 哈希",
            head.replace("\n", " / ")[:200])
    c.check("| 形态 |" in head and "缺口·有形态但无规则" in head
            and "缺口·无形态的输入" in head,
            "R6.8 首屏有覆盖率表与两类缺口行",
            head.replace("\n", " / ")[:260])
    c.check("支撑（焊盘）" in head and "195 焊盘" in head,
            "R6.8 覆盖率写明支撑单位（焊盘），不与「多少件封装」混读",
            head.replace("\n", " / ")[:260])
    c.check("缺口·有形态但无派生规则" not in md,
            "R6.8 不输出规格没要求的派生缺口行")

    skip_res = {"conclusion": "REVIEW_REQUIRED", "summary": {"total": 1},
                "items": [{"id": "2.2", "name": "阻焊开窗外扩", "status": "NA",
                           "detail": "1 个 pin 通过；2 个 pin 未判定（SKIP，不判 PASS）："
                                     "缺规则：形态 FORM-TH-DRILL-OTHER 尚无判定规则"}]}
    skip_md = RR.render_markdown(skip_res)
    c.check("未判定（SKIP，不判 PASS）" in skip_md
            and "缺规则：形态 FORM-TH-DRILL-OTHER" in skip_md,
            "R6.8 未判定原因写进 Markdown（读者不必翻 JSON）",
            skip_md[-220:].replace("\n", " / "))
    c.check("| 阻焊开窗外扩 |" in skip_md and "1 个 pin 通过" in skip_md,
            "R6.8 混合场景保留「通过」的那部分信息")

    old = {"conclusion": "PASS", "summary": {"total": 0}, "items": []}
    old_md = RR.render_markdown(old)
    c.check("规则集 v" not in old_md and "缺口·" not in old_md,
            "R6.8 缺新字段的旧结果仍能渲染（整块跳过）",
            old_md[:120].replace("\n", " / "))

    rep = RR.build_checklist_report(res)
    c.check(rep.get("report_version") == "3.1"
            and {"rule_set", "coverage", "items", "conclusion"} <= set(rep),
            "R6.8 JSON 报告 3.1 且含 rule_set/coverage",
            str(rep.get("report_version")))
    c.check(RR.build_checklist_report(old).get("rule_set") is None,
            "R6.8 旧结果生成报告时不伪造规则集")

    for rel in ("mcp_server/tools.py", "Tools/cleanup_redundant.py"):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        c.check("build_checklist_report" in txt,
                f"R6.8 {rel} 共用同一份报告定义")

    tmp = Path(tempfile.mkdtemp(prefix="r6_stats_"))
    try:
        old_rep = {"report_version": "3.0", "generated_at": "2026-11-01T00:00:00",
                   "dra_file": "old.dra", "symbol_name": "OLD", "conclusion": "PASS",
                   "summary": {"total": 1}, "items": [{"id": "2.2", "status": "PASS",
                                                      "name": "阻焊"}],
                   "failed": [], "warned": []}
        _dump_json(tmp / "checklist_report_old.json", old_rep)
        _dump_json(tmp / "checklist_report_new.json", dict(rep, dra_file="new.dra"))
        from observability import stats
        got = stats.rule_stats(log_dir=str(tmp))
        c.check(got.get("reports") == 2 and got.get("items") == 1 + len(rep.get("items") or []),
                "R6.8 新旧报告都能被 stats 解析（新增字段不炸历史解析）",
                json.dumps(got, ensure_ascii=False)[:220])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- R6.9
def check_hash_sensitivity(c: Checker, RE) -> None:
    base = RE.load_library(RULES_DIR)
    cases = [
        ("参数区间", "parameters.json", lambda d: d["items"][0].__setitem__("allowed", [0.05, 0.2])),
        ("规则文案", "rules.json", lambda d: d["items"][0].__setitem__("display", "改过的文案")),
        ("版本号", "library.json", lambda d: d.__setitem__("version", "1.0.1")),
    ]
    for label, fname, mutate in cases:
        tmp = Path(tempfile.mkdtemp(prefix="r6_hash_"))
        try:
            _write_lib(tmp, lambda d: None)
            fp = tmp / fname
            data = _load_json(fp)
            mutate(data)
            _dump_json(fp, data)
            lib = RE.load_library(tmp)
            c.check(lib.fingerprint != base.fingerprint,
                    f"R6.9 改{label} → 整库哈希变化",
                    f"{base.fingerprint[:12]} → {lib.fingerprint[:12]}")
            cov = RE.coverage(lib, [])
            c.check(cov.get("rule_set_hash") == lib.fingerprint,
                    f"R6.9 改{label}后覆盖率哈希与新库同源")
        except Exception as exc:                            # noqa: BLE001
            c.check(False, f"R6.9 改{label}后仍应能加载", f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- R6.10
def _expected_form(pin: dict):
    pads = [p for p in pin.get("pads") or [] if p.get("layer")]
    layers = [p.get("layer") for p in pads]
    if not pads:
        return None
    if "DRILL" not in layers:
        return "FORM-MASK-SMD"
    drill = next((p for p in pads if p.get("layer") == "DRILL"), None)
    shape = (drill or {}).get("hole_type")
    if shape == "OVAL SLOT":
        return "FORM-MASK-TH-OBLONG"
    if shape == "CIRCLE_DRILL":
        return "FORM-MASK-TH-CIRCLE"
    if shape in ("SQUARE_DRILL", "RECTANGLE_SLOT", "RECTANGLE SLOT"):
        return "FORM-TH-DRILL-OTHER"
    return None


def check_corpus_reconcile(c: Checker, RE, lib, base: dict) -> None:
    from collections import Counter
    from services import rule_checker as RC

    shadow_path = BASELINE.parent / "shadow_2_2_253.json"
    shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
    rows = shadow.get("rows") or []
    corpus_forms = set()
    for row in rows:
        corpus_forms.update(row.get("forms") or [])
    lib_forms = {f["id"] for f in lib.forms}
    c.check(corpus_forms <= lib_forms,
            "R6.10 语料里出现过的形态都在形态表里",
            f"缺 {sorted(corpus_forms - lib_forms)}")
    no_form = sum(1 for row in rows if not row.get("forms"))
    c.check(len(rows) == 253 and no_form == 19,
            "R6.10 语料 253 件中 19 件无形态（无形态类别的真实样本）",
            f"{len(rows)} 件 / 无形态 {no_form}")

    conv = json.loads((ROOT / "knowledge" / "conventions.json").read_text(encoding="utf-8"))
    adj = conv.get("adjudicated") or {}
    # conventions 里记了支撑件数的形态必须与规则库一致（SMD 的支撑记录在规则库来源里）
    want = {
        "FORM-MASK-TH-OBLONG": adj["th_oblong_plated"]["mask_expand_side_mm"]["support"],
        "FORM-MASK-TH-CIRCLE": adj["th_circle_plated"]["mask_expand_side_mm"]["support"],
    }
    cov = RE.coverage(lib, [])
    got = {r["form"]: r["support"] for r in cov["forms"]}
    c.check(all(got.get(k) == v for k, v in want.items()),
            "R6.10 每形态语料支撑件数与 conventions 裁定一致",
            json.dumps({k: (got.get(k), want[k]) for k in want}, ensure_ascii=False))
    c.check(got.get("FORM-MASK-SMD", 0) > 0,
            "R6.10 SMD 也有语料支撑件数（来自规则库来源，不是空占位）",
            str(got.get("FORM-MASK-SMD")))

    # 语料统计（各形态件数）与覆盖率表能对上：语料出现的每个形态都有覆盖行
    corpus_count = Counter()
    for row in rows:
        for fid in row.get("forms") or []:
            corpus_count[fid] += 1
    c.check(set(corpus_count) <= {r["form"] for r in cov["forms"]},
            "R6.10 语料里每个形态都能在覆盖率表里找到对应行",
            json.dumps(dict(corpus_count), ensure_ascii=False))
    c.check(dict(corpus_count) == {"FORM-MASK-SMD": 217, "FORM-MASK-TH-CIRCLE": 23,
                                   "FORM-MASK-TH-OBLONG": 15},
            "R6.10 语料件数（按件）SMD 217 / TH-CIRCLE 23 / TH-OBLONG 15",
            json.dumps(dict(corpus_count), ensure_ascii=False))
    c.check({k: got[k] for k in want} == {"FORM-MASK-TH-OBLONG": 22, "FORM-MASK-TH-CIRCLE": 9}
            and got.get("FORM-MASK-SMD") == 195,
            "R6.10 支撑是焊盘口径（22 / 9 / 195），与件数口径分开记",
            json.dumps(got, ensure_ascii=False))

    subjects = []
    expected = Counter()
    for fx in base.get("fixtures") or []:
        for pin in fx.get("pins") or []:
            if not pin.get("pads"):
                continue
            subjects.append(RC._pin_rule_subject(pin))
            expected[_expected_form(pin)] += 1
    cov = RE.coverage(lib, subjects)
    actual = Counter({r["form"]: r["hits"] for r in cov["forms"]})
    c.check(actual == expected,
            "R6.10 冻结夹具上 coverage 逐形态命中 == 独立谓词计数",
            json.dumps({"actual": dict(actual), "expected": dict(expected)}, ensure_ascii=False))
    c.check(sum(actual.values()) == len(subjects) == cov["total"]["matched"],
            "R6.10 覆盖率合计 == 有焊盘的 pin 数",
            f"{sum(actual.values())} / {len(subjects)} / {cov['total']['matched']}")


# ---------------------------------------------------------------- R6.11
def check_single_definition(c, RE, lib) -> None:
    """票 #19：阻焊外扩只有一个参数定义，按三种形态分组。"""
    hits = [p for p in lib.parameters_by_id.values()
            if p.get("field") == "mask_expand_side"]
    c.check(len(hits) == 1,
            "R6.11 阻焊外扩只有一个参数定义",
            "、".join(str(p.get("id")) for p in hits))
    par = hits[0] if hits else {}
    c.check(par.get("id") == MASK_PARAM,
            "R6.11 合并后的参数 id 是 PARAM-MASK-EXPAND", str(par.get("id")))
    groups = RE.parameter_groups(par)
    c.check([g.get("form") for g in groups] == list(MASK_FORMS),
            "R6.11 三个形态组各一份（顺序 = forms.json 判定顺序）",
            str([g.get("form") for g in groups]))
    vals = {g.get("form"): g.get("value") for g in groups}
    c.check(vals == {"FORM-MASK-TH-OBLONG": 0.05, "FORM-MASK-TH-CIRCLE": 0.05,
                     "FORM-MASK-SMD": 0.08},
            "R6.11 形态典型值 0.05 / 0.05 / 0.08", str(vals))
    c.check(par.get("allowed") == [0.05, 0.13],
            "R6.11 允许区间只在参数级写一次", str(par.get("allowed")))
    c.check(not any("allowed" in g for g in groups),
            "R6.11 组里不带区间（三形态共用同一区间）",
            str([g.get("form") for g in groups if "allowed" in g]))
    views = {f: RE.resolve_parameter(par, f) for f in MASK_FORMS}
    c.check(all(v is not None for v in views.values()),
            "R6.11 三形态都能解析出参数视图",
            str([f for f, v in views.items() if v is None]))
    c.check(all(v and v.get("allowed") == par.get("allowed") for v in views.values()),
            "R6.11 解析视图共用参数级区间",
            str([(f, (v or {}).get("allowed")) for f, v in views.items()]))
    c.check((views.get("FORM-MASK-TH-OBLONG") or {}).get("value") == 0.05
            and (views.get("FORM-MASK-SMD") or {}).get("value") == 0.08,
            "R6.11 解析视图按形态取到不同典型值（TH 0.05 / SMD 0.08）")
    c.check(all(v and "groups" not in v for v in views.values()),
            "R6.11 解析视图已摊平（消费方不接触 groups）")

    def dup(dst):
        p = dst / "parameters.json"
        data = _load_json(p)
        twin = copy.deepcopy(next(o for o in data["items"] if o["id"] == MASK_PARAM))
        twin["id"] = "PARAM-MASK-EXPAND-TWIN"
        twin["groups"] = [g for g in twin["groups"] if g["form"] == "FORM-MASK-SMD"]
        data["items"].append(twin)
        _dump_json(p, data)

    tmp = Path(tempfile.mkdtemp(prefix="r6_single_"))
    try:
        _write_lib(tmp, dup)
        try:
            RE.load_library(tmp)
            c.check(False, "R6.11 同 (field, form) 第二份定义被拒",
                    "加载竟然成功——第二份真源复活了")
        except RE.RuleLibraryError as exc:
            msg = str(exc)
            c.check("第二份拷贝" in msg and "parameters.json" in msg
                    and "FORM-MASK-SMD" in msg,
                    "R6.11 同 (field, form) 第二份定义被拒（报出对象与字段）",
                    msg.splitlines()[0] if msg else "")
        except Exception as exc:                              # noqa: BLE001
            c.check(False, "R6.11 同 (field, form) 第二份定义被拒",
                    f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- R6.12
def check_single_change_two_sides(c, RE) -> None:
    """票 #19：改规则库一处，派生与判定同时跟着变；旧阈值文件不再影响派生。"""
    from services import convention_engine as CE

    lib_dir = Path(tempfile.mkdtemp(prefix="r6_both_"))
    conv_dir = Path(tempfile.mkdtemp(prefix="r6_conv_"))
    old_env = os.environ.get("LAYOUT_FORGE_RULES_DIR")
    old_conv = CE.CONV_PATH
    try:
        _write_lib(lib_dir, lambda d: None)
        p = lib_dir / "parameters.json"
        data = _load_json(p)
        for obj in data["items"]:
            if obj["id"] != MASK_PARAM:
                continue
            obj["allowed"] = [0.055, 0.07]
            for grp in obj["groups"]:
                grp["value"] = {"FORM-MASK-TH-OBLONG": 0.06,
                                "FORM-MASK-TH-CIRCLE": 0.06,
                                "FORM-MASK-SMD": 0.065}[grp["form"]]
        _dump_json(p, data)

        # 旧阈值文件里那份 mask 值故意改成 0.09（迁移前是 0.05）：派生必须无视它
        conv = json.loads(json.dumps(CE.load_conventions(force=True)))
        conv["adjudicated"]["th_oblong_plated"]["mask_expand_side_mm"]["value"] = 0.09
        conv_path = conv_dir / "conventions.json"
        conv_path.write_text(json.dumps(conv, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        CE.CONV_PATH = conv_path
        CE._CACHE = None
        CE._CACHE_STAMP = None

        os.environ["LAYOUT_FORGE_RULES_DIR"] = str(lib_dir)
        RE.reset_cache()
        lib2 = RE.load_library(lib_dir)
        params = CE.th_oblong_params()
        c.check(abs(float(params["mask_expand_side"]) - 0.06) < 1e-9,
                "R6.12 派生侧读到库里的形态组值（0.06，不是旧文件的 0.09）",
                str(params.get("mask_expand_side")))
        c.check(params.get("mask_source") == "SRC-CONV-ADJUDICATED",
                "R6.12 派生结果带出处", str(params.get("mask_source")))
        c.check((params.get("support") or {}).get("mask") == 22,
                "R6.12 派生结果带支持件数", str(params.get("support")))

        ps, _notes = CE.derive_th_oblong_padstack((1.9, 0.7), (2.3, 1.1))
        layer = {lay["layer"]: lay["size"] for lay in ps["layers"]}
        pad, mask = layer["TOP"], layer["SOLDERMASK_TOP"]
        c.check(abs(mask[0] - pad[0] - 0.12) < 1e-9
                and abs(mask[1] - pad[1] - 0.12) < 1e-9,
                "R6.12 派生出的阻焊层 = 焊盘 + 2×0.06",
                f"pad={pad} mask={mask}")

        subj = {"mount": "tht", "drill": {"shape": "OVAL SLOT"}}
        v6 = RE.judge(lib2, "RULE-MASK-EXPAND", subj, {"expand_x": 0.06, "expand_y": 0.06})
        c.check(v6.get("status") == "PASS",
                "R6.12 判定侧同一次改动即生效（0.06 落在新区间 → PASS）",
                str(v6.get("detail")))
        v9 = RE.judge(lib2, "RULE-MASK-EXPAND", subj, {"expand_x": 0.09, "expand_y": 0.09})
        c.check(v9.get("status") == "WARN",
                "R6.12 判定侧按新区间判（0.09 迁移前 PASS，现在 WARN）",
                str(v9.get("status")) + " / " + str(v9.get("detail")))
    finally:
        if old_env is None:
            os.environ.pop("LAYOUT_FORGE_RULES_DIR", None)
        else:
            os.environ["LAYOUT_FORGE_RULES_DIR"] = old_env
        CE.CONV_PATH = old_conv
        CE._CACHE = None
        CE._CACHE_STAMP = None
        RE.reset_cache()
        shutil.rmtree(lib_dir, ignore_errors=True)
        shutil.rmtree(conv_dir, ignore_errors=True)


# ---------------------------------------------------------------- R6.13
def _leaf_keys(obj, prefix="") -> list:
    if isinstance(obj, dict):
        if not obj:
            return [prefix]
        out = []
        for k, v in obj.items():
            out.extend(_leaf_keys(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return [prefix]


def _legacy_rule_ids() -> list:
    """扫 services/rule_checker.py 的 AST，取规则 id 全集。

    规则 id 有两个产出口：
      - _item("X.Y", ...)：未迁移的旧规则（含 0 占位，0 不登记）；
      - _finding_item("X.Y", ...)：票 #18 起由规则库 finding 聚合的 2.8/2.9/2.10。
    两个都收，迁移映射表才能覆盖检查器实际产出的全部规则 id。
    """
    src = (ROOT / "services" / "rule_checker.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ids = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("_item", "_finding_item") and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value[:1].isdigit() and arg.value != "0":
                    ids.add(arg.value)
    return sorted(ids)


def check_migration_table(c, RE, lib) -> None:
    """票 #19：映射表覆盖每个旧键与每条旧规则，唯一归属，文档与渲染一致。"""
    c.check(MIGRATION.is_file(), "R6.13 映射表 JSON 存在", str(MIGRATION))
    c.check(MIGRATION_DOC.is_file(), "R6.13 审阅文档存在", str(MIGRATION_DOC))
    if not (MIGRATION.is_file() and MIGRATION_DOC.is_file()):
        return
    doc = _load_json(MIGRATION)
    entries = doc.get("entries") or []
    c.check(doc.get("schema") == "layout-forge/rules/migration@1",
            "R6.13 schema 声明", str(doc.get("schema")))

    key_rows = [e for e in entries if e.get("kind") == "threshold_key"]
    rule_rows = [e for e in entries if e.get("kind") == "rule"]
    got_keys = [str(e.get("legacy", "")).split("#", 1)[-1] for e in key_rows]
    c.check(len(got_keys) == len(set(got_keys)),
            "R6.13 旧阈值文件每个键只登记一次（唯一归属）",
            f"{len(got_keys)} 行 / {len(set(got_keys))} 个不同键")
    want_keys = set(_leaf_keys(_load_json(LEGACY_RULES)))
    c.check(set(got_keys) == want_keys,
            "R6.13 旧阈值文件的键被映射表全覆盖",
            f"漏={sorted(want_keys - set(got_keys))} 多={sorted(set(got_keys) - want_keys)}")

    got_ids = [str(e.get("legacy", "")).split("#", 1)[-1] for e in rule_rows]
    c.check(len(got_ids) == len(set(got_ids)),
            "R6.13 旧检查器每条规则只登记一次（唯一落点）",
            f"{len(got_ids)} 行 / {len(set(got_ids))} 个不同 id")
    want_ids = set(_legacy_rule_ids())
    c.check(set(got_ids) == want_ids,
            "R6.13 旧检查器的每条规则都能在映射表里找到落点",
            f"漏={sorted(want_ids - set(got_ids))} 多={sorted(set(got_ids) - want_ids)}")

    bad = []
    for e in entries:
        tgt = e.get("target") or {}
        ttype, tid = tgt.get("type"), tgt.get("id")
        if ttype == "parameter":
            if tid not in lib.parameters_by_id:
                bad.append(str(e.get("legacy")) + " → 参数 " + str(tid) + " 不存在")
        elif ttype == "rule":
            if tid not in lib.rules_by_id:
                bad.append(str(e.get("legacy")) + " → 规则 " + str(tid) + " 不存在")
        elif ttype == "legacy":
            if not e.get("reason") or not e.get("rollback"):
                bad.append(str(e.get("legacy")) + " 保留 legacy 但没写原因/回滚方式")
        else:
            bad.append(str(e.get("legacy")) + " 归属类型非法 " + repr(ttype))
    c.check(not bad,
            "R6.13 每条归属指向真实对象；legacy 行写了原因与回滚方式",
            "；".join(bad[:6]))

    by_legacy = {str(e.get("legacy")): e for e in entries}
    par = lib.parameters_by_id.get(MASK_PARAM) or {}
    grp_smd = next((g for g in RE.parameter_groups(par)
                    if g.get("form") == "FORM-MASK-SMD"), {})
    typ = by_legacy.get("checker_rules.json#pad.soldermask_expand_typical_mm") or {}
    c.check(grp_smd.get("value") == typ.get("legacy_value"),
            "R6.13 旧典型值迁到 SMD 组的 value",
            "旧 " + str(typ.get("legacy_value")) + " → 库 " + str(grp_smd.get("value")))
    lo = by_legacy.get("checker_rules.json#pad.soldermask_expand_min_mm") or {}
    hi = by_legacy.get("checker_rules.json#pad.soldermask_expand_max_mm") or {}
    c.check(par.get("allowed") == [lo.get("legacy_value"), hi.get("legacy_value")],
            "R6.13 旧 min/max 迁到参数级 allowed[0]/allowed[1]",
            "旧 [" + str(lo.get("legacy_value")) + ", " + str(hi.get("legacy_value"))
            + "] → 库 " + str(par.get("allowed")))
    rule22 = by_legacy.get("rule_checker.py#2.2") or {}
    c.check((rule22.get("target") or {}).get("id") == "RULE-MASK-EXPAND"
            and (lib.rules_by_id.get("RULE-MASK-EXPAND") or {}).get("use") == [MASK_PARAM],
            "R6.13 旧 2.2 的落点是引用合并参数的 RULE-MASK-EXPAND",
            str((lib.rules_by_id.get("RULE-MASK-EXPAND") or {}).get("use")))

    spec = importlib.util.spec_from_file_location(
        "render_migration_table", ROOT / "Tools" / "render_migration_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rendered = mod.render(doc).replace("@@BT@@", chr(96)).encode("utf-8")
    c.check(rendered == MIGRATION_DOC.read_bytes(),
            "R6.13 审阅文档与渲染器输出逐字节一致（防手抄漂移）",
            f"渲染 {len(rendered)} 字节 / 文档 {MIGRATION_DOC.stat().st_size} 字节")


# ---------------------------------------------------------------- R6.14
def check_rollback_drill(c, RE, base) -> None:
    """票 #19：照 rollback_recipe 反向展开，判定结果与迁移前一致。"""
    from services import rule_checker as RC

    recipe = (_load_json(MIGRATION) if MIGRATION.is_file() else {}).get("rollback_recipe") or {}
    steps = recipe.get("steps") or []
    expand = next((s for s in steps if s.get("op") == "expand_parameter"), {})
    rewrite = next((s for s in steps if s.get("op") == "rewrite_rule"), {})
    c.check(bool(expand.get("to")) and bool(rewrite.get("use")),
            "R6.14 回滚配方可执行（展开参数 + 改写规则 use）",
            str(len(expand.get("to") or [])) + " 个旧参数 / use=" + str(rewrite.get("use")))
    note = str(recipe.get("note") or "")
    c.check("convention_engine" in note,
            "R6.14 回滚配方写明代码侧配套步骤（convention_engine 回读旧文件）",
            note[:80])

    tmp = Path(tempfile.mkdtemp(prefix="r6_rollback_"))
    old_env = os.environ.get("LAYOUT_FORGE_RULES_DIR")
    try:
        _write_lib(tmp, lambda d: None)
        pp = tmp / "parameters.json"
        pdata = _load_json(pp)
        par = next((o for o in pdata["items"] if o["id"] == MASK_PARAM), None)
        if not par:
            c.check(False, "R6.14 回滚演练可开始（库里有合并参数）", "缺 PARAM-MASK-EXPAND")
            return
        keep = {k: par[k] for k in ("field", "keys", "allowed", "eps", "unit") if k in par}
        expanded = []
        for tgt in expand.get("to") or []:
            grp = next((g for g in par.get("groups") or []
                         if g.get("form") == tgt.get("form")), None)
            if grp is None:
                continue
            new = dict(keep)
            new["id"] = tgt["id"]
            new["form"] = tgt["form"]
            new["value"] = grp["value"]
            new["support"] = grp["support"]
            new["source"] = grp["source"]
            if par.get("note"):
                new["note"] = par["note"]
            expanded.append(new)
        pdata["items"] = [o for o in pdata["items"] if o["id"] != MASK_PARAM] + expanded
        _dump_json(pp, pdata)

        rp = tmp / "rules.json"
        rdata = _load_json(rp)
        rule = next((o for o in rdata["items"]
                     if o["id"] == (rewrite.get("rule") or "RULE-MASK-EXPAND")), None)
        if rule is not None:
            rule["use"] = list(rewrite.get("use") or [])
        _dump_json(rp, rdata)

        merged_lib = RE.load_library(RULES_DIR)
        os.environ["LAYOUT_FORGE_RULES_DIR"] = str(tmp)
        RE.reset_cache()
        rollback_lib = RE.load_library(tmp)

        diff = []
        for form in MASK_FORMS:
            a = RE.resolve_parameter(merged_lib.parameters_by_id[MASK_PARAM], form) or {}
            tid = next((t["id"] for t in expand.get("to") or []
                        if t.get("form") == form), None)
            b = RE.resolve_parameter(rollback_lib.parameters_by_id.get(tid, {}), form) or {}
            for field in ("field", "keys", "allowed", "eps", "unit",
                          "value", "support", "source", "form"):
                if a.get(field) != b.get(field):
                    diff.append(f"{form}.{field}: {a.get(field)!r} → {b.get(field)!r}")
        c.check(not diff, "R6.14 回滚后三形态视图与合并库逐字段相等",
                "；".join(diff))

        for fx in base.get("fixtures") or []:
            item = _item_22(RC.check_pad_size({"pins": fx.get("pins") or []},
                                              None, RC.DEFAULT_RULES))
            got, want = item.get("status"), fx.get("status22")
            c.check(got == want,
                    "R6.14 回滚后 " + str(fx.get("file")) + " 2.2 状态与迁移前一致（旧 " + str(want) + "）",
                    f"回滚库 {got}")

    finally:
        if old_env is None:
            os.environ.pop("LAYOUT_FORGE_RULES_DIR", None)
        else:
            os.environ["LAYOUT_FORGE_RULES_DIR"] = old_env
        RE.reset_cache()
        shutil.rmtree(tmp, ignore_errors=True)


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
    check_single_definition(c, RE, lib)
    check_single_change_two_sides(c, RE)
    check_migration_table(c, RE, lib)
    check_rollback_drill(c, RE, base)
    check_coverage(c, RE, lib)
    check_report_fields(c, RE)
    check_hash_sensitivity(c, RE)
    check_corpus_reconcile(c, RE, lib, base)

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
