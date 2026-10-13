# -*- coding: utf-8 -*-
"""
selfcheck_conventions.py —— 约定链路完整性自检（第 0 层防回归）

不需要 Allegro、不需要联网。堵死「约定被学到 → 约定被消费」链路上的
两个代码级断点（2026-10-13 LYDPJ holeType 事故，工程师 4 次否决复盘）：

  [A] 数据管道契约 —— extracta 解析器不得丢约定消费所需的字段
      背景：_parse_pads_file 曾丢弃 DRILL 行 holeType 列（r[19]），
      导致检查规则永远查不到 holeType，Thru Pin 错误封装全链路绿灯。
      做法：用金标准真实 extracta 输出片段（Tools/testdata/）跑真实
      解析器，断言每个受管字段都进了解析结果。

  [B] 约定消费覆盖率 —— conventions.json 里每个受管键必须有已接线的消费点
      背景：slot_type=OVAL SLOT×26 早被收割进约定库，但
      derive_th_oblong_padstack 的 drill_type 硬编码 OBLONG_DRILL，
      「存了没接」→ builder 照错参数建。
      做法：WIRING 清单（键 → 源码消费点正则）双向断言：
        B1 conventions.json 的受管键不得脱离清单（新键必须先登记再接线）
        B2 清单里每条 [文件, 正则] 必须真实命中

用法：
    ./.venv/Scripts/python.exe Tools/selfcheck_conventions.py
退出码：0=全过 / 1=有失败
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FIXTURE = os.path.join(_HERE, "testdata", "extracta_fixture_pads.txt")


# ============================================================
# 框架（与 selfcheck_geometry 同款极简 PASS/FAIL）
# ============================================================
class R:
    def __init__(self):
        self.n_pass = 0
        self.fails = []

    def check(self, ok: bool, desc: str, detail: str = "") -> None:
        if ok:
            self.n_pass += 1
        else:
            self.fails.append(f"{desc}" + (f" —— {detail}" if detail else ""))
            print(f"  ✗ {desc}" + (f" —— {detail}" if detail else ""))


# ============================================================
# [A] 数据管道契约
# ============================================================
# 约定消费所需的 DRILL 行字段（与 rule_checker 2.7c / convention_engine 对齐）
REQUIRED_DRILL_FIELDS = ("hole_type", "figure_name", "size", "layer")
EXPECTED_HOLE_TYPE = "OVAL SLOT"        # 金标准 th_oblong 唯一口径
EXPECTED_FIGURE = "OBLONG_Y"


def section_a(r: R) -> None:
    print("[A] 数据管道契约（extracta 解析不丢受管字段）")
    from services.extracta_reader import _parse_pads_file
    from pathlib import Path

    pads = _parse_pads_file(Path(FIXTURE))

    drill_entries = [
        pd for entries in pads.values() for pd in entries
        if str(pd.get("layer", "")).upper().endswith("DRILL")
    ]
    r.check(bool(drill_entries), "A1 夹具解析出 DRILL 条目")
    if not drill_entries:
        return

    d = drill_entries[0]
    for f in REQUIRED_DRILL_FIELDS:
        r.check(f in d, f"A2 DRILL 条目含字段 `{f}`",
                f"现有字段={sorted(d.keys())}")
    r.check(d.get("hole_type") == EXPECTED_HOLE_TYPE,
            f"A3 hole_type == '{EXPECTED_HOLE_TYPE}'（槽孔口径）",
            f"实际={d.get('hole_type')!r}")
    r.check(str(d.get("figure_name", "")).upper() == EXPECTED_FIGURE,
            f"A4 figure_name == '{EXPECTED_FIGURE}'（本地竖向）",
            f"实际={d.get('figure_name')!r}")
    w, h = d.get("size") or (0, 0)
    r.check(w > 0 and h > 0, "A5 DRILL 尺寸非零（实义存在）",
            f"size={d.get('size')}")

    # 2026-10-13 新增透传字段（miner 复用 reader 解析后依赖的完整字段集）
    r.check(d.get("drill_char") == "OA",
            "A8 drill_char == 'OA'（钻头表字符透传）",
            f"实际={d.get('drill_char')!r}")
    dd = d.get("drill_diameter")
    r.check(dd is not None and abs(dd - 0.5) < 0.01,
            "A9 drill_diameter ≈ 0.5（孔径透传，此前被局部变量丢弃）",
            f"实际={dd!r}")
    r.check(d.get("plated") == "P",
            "A10 plated == 'P'（金属化透传，NP 判定依赖）",
            f"实际={d.get('plated')!r}")

    # A11 NPTH 退化行（2026-10-13 收割回归实锤后新增）：figureSize 全零、
    # 仅 drillDiameter 有值的真实非金属化孔，不得被普通行零尺寸过滤丢弃。
    npth = [pd for entries in pads.values() for pd in entries
            if pd.get("drill_diameter") and pd.get("drill_diameter") > 0
            and (pd.get("size") or [0, 0])[0] == 0]
    r.check(bool(npth), "A11 figureSize 全零的 NPTH DRILL 行不被丢弃",
            "reader 对 DRILL 行误用普通行零尺寸过滤，mount 将误判 smd")
    if npth:
        n = npth[0]
        r.check(abs(n["drill_diameter"] - 3.0) < 0.01 and n.get("plated") == "N",
                "A12 NPTH 行 drill_diameter=3.0 / plated=N 透传",
                f"实际 dia={n.get('drill_diameter')} plated={n.get('plated')!r}")

    # A13 SHAPE 型焊盘（2026-10-13 建模裁定）：PADSHAPENAME（官方列 r12 /
    # 解析后 r[11]）必须透传——SHAPE 焊盘轮廓几何 extracta 不给顶点，
    # shape_name 是唯一可辨识特征，checker 0.2 显名与 miner facts 依赖它。
    shape_entries = [pd for entries in pads.values() for pd in entries
                     if pd.get("shape_name")]
    r.check(bool(shape_entries), "A13 SHAPE 型焊盘 shape_name 透传",
            "PADSHAPENAME 列被丢弃，0.2 显名规则与挖掘 facts 将失效")
    if shape_entries:
        r.check(shape_entries[0].get("shape_name") == "JOPEN25X30_S-L-P"
                and shape_entries[0].get("figure_name", "").upper() == "SHAPE",
                "A13b shape_name=JOPEN25X30_S-L-P 且 figure=SHAPE",
                f"实际={shape_entries[0]!r:.200}")

    # 透传断言：pin pads 组装（pads_attached）必须把 hole_type 带出去。
    # pads_attached 内嵌在 read_via_extracta（需 Allegro，不可离线跑），
    # 这里用源码契约断言兜底：组装处必须显式透传 hole_type。
    src = open(os.path.join(_ROOT, "services", "extracta_reader.py"),
               encoding="utf-8").read()
    r.check('"hole_type": pd["hole_type"]' in src,
            "A6 read_via_extracta 组装处透传 hole_type",
            "pads_attached 构造里丢了透传，2.7c 将查不到字段")
    r.check('"hole_type"' in src and "r[19]" in src,
            "A7 解析器提取 holeType 列（r[19]）")
    # 2026-10-13 SHAPE 建模：组装处透传 + checker 0.2 消费点 + builder 守卫
    r.check('"shape_name": pd["shape_name"]' in src,
            "A14 read_via_extracta 组装处透传 shape_name",
            "pads_attached 构造丢了透传，0.2 将查不到字段")
    checker_src = open(os.path.join(_ROOT, "services", "rule_checker.py"),
                       encoding="utf-8").read()
    r.check("def check_shape_pads" in checker_src
            and "check_shape_pads(footprint_data)" in checker_src,
            "A15 checker 0.2 SHAPE 显名规则已接线",
            "SHAPE 焊盘将回到静默近似")
    gs_src = open(os.path.join(_ROOT, "services", "geometry_solver.py"),
                  encoding="utf-8").read()
    r.check("guard_spec_keys(spec)" in gs_src and "SPEC_KNOWN_KEYS" in gs_src,
            "A16 builder spec 未知键守卫已接线",
            "spec 未知键回到静默丢弃")


# ============================================================
# [B] 约定消费覆盖率
# ============================================================
# 受管键 → 必须命中的消费点。★ 新增 conventions.json 键时必须先在这里登记，
# 并在对应源码真正消费——登记了没消费（B2）或消费了没登记（B1）都算 FAIL。
WIRING = {
    "th_oblong.slot_type": [
        ("services/convention_engine.py", r"OVAL_SLOT"),
        ("services/rule_checker.py", r"holeType"),
    ],
    "th_oblong.annular_ring_mm(standard)": [
        ("services/convention_engine.py", r"ring_standard"),
        ("services/rule_checker.py", r"2\.8"),
    ],
    "th_oblong.mask_expand_side_mm": [
        ("services/convention_engine.py", r"mask_expand_side"),
    ],
    "th_oblong.paste_top": [
        ("services/convention_engine.py", r"paste_top_eq_pad|paste_top"),
    ],
    "th_oblong.naming(P{孔}H{盘})": [
        ("services/convention_engine.py", r"naming_match_th"),
        ("services/rule_checker.py", r"check_padstack_naming"),
    ],
    "th_oblong.rotation": [
        ("services/convention_engine.py", r"pin\[\"rotation\"\] = 90|rotation.*90"),
    ],
    "adjudicated.th_oblong_plated": [
        ("services/convention_engine.py", r"th_oblong_plated"),
    ],
    "th_oblong.holeType校验(2.7c)": [
        ("services/rule_checker.py", r"2\.7c|TH 钻孔 holeType"),
        ("services/extracta_reader.py", r"hole_type"),
    ],
}

# ★ 已知未接线欠账（诚实清单）：这些约定块已收割进库、但 builder/checker
#   尚无消费点（engine 目前只处理 th_oblong）。新增消费点后应从此清单移出、
#   登记进 WIRING。静默欠账 = 假学习；显式欠账 = 待办。
KNOWN_UNWIRED = {
    "adjudicated.smd",              # SMD padstack 构造约定未接 engine
    "adjudicated.th_circle_plated", # 圆孔 TH 构造约定未接 engine
    "adjudicated.th_other",         # 异形 TH 构造约定未接 engine
    "th_circle",                    # 圆孔 TH 约定组（同上，整组未消费）
    "th_other",                     # 异形 TH 约定组（同上，整组未消费）
    "smd",                          # SMD 约定组（同上，整组未消费）
    "th_oblong.drill_char",         # drillChar 仅聚合可见（report-only）：
                                    # 设计级钻头表字符，随图纸变（audio6 空/if、
                                    # 圆孔 H），不作为 padstack 构造约定强制
}

# conventions.json 里允许存在、但明确不属于消费链路的键（B1 白名单）
NON_CONSUMED_OK = {"schema", "mined_at", "corpus", "column_guard"}


def _conv_keys(obj, prefix=""):
    """展平 conventions.json 的键路径（只取 dict 第一/二层受管块）。"""
    out = set()
    for k, v in obj.items():
        if k in NON_CONSUMED_OK:
            continue
        p = f"{prefix}{k}"
        out.add(p)
        if isinstance(v, dict):
            out |= {f"{p}.{kk}" for kk in v}
    return out


def section_b(r: R) -> None:
    print("[B] 约定消费覆盖率（受管键 ↔ 源码消费点双向断言）")
    conv = json.load(open(os.path.join(_ROOT, "knowledge", "conventions.json"),
                          encoding="utf-8"))
    groups = conv.get("conventions") or {}
    adj = conv.get("adjudicated") or {}

    # B0: 关键受管块必须存在（约定库被误删/改 schema 时爆红）
    r.check("th_oblong" in groups, "B0 conventions.json 含 th_oblong 组")
    r.check(bool(adj), "B0 conventions.json 含 adjudicated 裁定块")

    # B2: 清单每条 [文件, 正则] 必须命中
    src_cache: dict = {}
    for key, points in WIRING.items():
        for rel, pattern in points:
            if rel not in src_cache:
                src_cache[rel] = open(os.path.join(_ROOT, *rel.split("/")),
                                      encoding="utf-8").read()
            r.check(re.search(pattern, src_cache[rel]) is not None,
                    f"B2 `{key}` 在 {rel} 有消费点",
                    f"正则 {pattern!r} 未命中——约定收了没接线")

    # B1: conventions 受管键必须登记在 WIRING（防新键漏接线）
    managed = set()
    for g, body in groups.items():
        managed.add(g)
        if isinstance(body, dict):
            for k in body:
                managed.add(f"{g}.{k}")
    for k in adj:
        managed.add(f"adjudicated.{k}")
    registered = {k.split("(")[0] for k in WIRING}
    # 语义键（有行为后果的）必须登记或列入显式欠账
    semantic_prefixes = ("th_oblong.slot_type", "th_oblong.naming",
                         "th_oblong.rotation", "adjudicated.")
    must = {m for m in managed if m.startswith(semantic_prefixes)}
    must -= KNOWN_UNWIRED
    r.check(not (must - registered),
            "B1 conventions 语义键全部登记在 WIRING 清单",
            f"未登记: {sorted(must - registered)}")
    if KNOWN_UNWIRED & managed:
        print("  · 欠账（显式登记 KNOWN_UNWIRED，待接线）:")
        for m in sorted(KNOWN_UNWIRED & managed):
            print(f"    - {m}")
    # 未知键提醒（不在 WIRING 也不在欠账清单的新约定键——新键先登记再接线）
    known = set(registered) | KNOWN_UNWIRED
    unknown = ({f"adjudicated.{k}" for k in adj} | set(groups)) - known
    # 已被 WIRING 前缀覆盖的组（如 th_oblong.*）不算未知
    unknown = {u for u in unknown
               if not any(r == u or r.startswith(u + ".") for r in registered)}
    if unknown:
        print(f"  · ⚠ 未登记的新约定键: {sorted(unknown)}（若有行为语义请登记 WIRING）")


# ============================================================
def main() -> int:
    r = R()
    section_a(r)
    print()
    section_b(r)
    total = r.n_pass + len(r.fails)
    print("=" * 60)
    if r.fails:
        print(f"  结果: {r.n_pass}/{total} 通过, {len(r.fails)} 失败 ✗")
        for f in r.fails:
            print(f"    - {f}")
        return 1
    print(f"  结果: {total}/{total} 全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
