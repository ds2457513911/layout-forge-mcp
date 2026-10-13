# -*- coding: utf-8 -*-
"""
Tools/mine_conventions.py —— 第 0 层：构造规约收割器（2026-10-13）

★ 解决什么问题
  三层漏斗的学习对象是"规则检查输出"，规则没覆盖的构造事实
  （drill 实义、环宽、mask 外扩、paste 策略、padstack 命名语法）
  从未被学习。本脚本直接对金标准库做全量结构化收割，把这些事实
  变成带支持度计数的约定库 conventions.json —— 作为 check/create
  双侧共用的唯一真源（用户 2026-10-13 裁定：一份约定，两任务继承）。

方法（确定性，0 AI token）：
  1. 扫描金标准库全部 *.dra（Layout\\Data\\PCB_Footprint）
  2. 每件跑一次 extracta（cwd=专用临时目录，不污染项目根），
     原始 pads.txt 归档到 Tools/log/conventions_raw/（补字段免重跑）
  3. 解析单一真源 = services/extracta_reader._parse_pads_file
     （2026-10-13 改造：删除本脚本内联 DRILL 解析——它与 reader 漂移、
     五字段写死漏 drillChar，是 drillChar 断供的根因）
  4. 逐 padstack 提取：层集/figure/尺寸/drill 实义/plated/槽型/drillChar，
     推导环宽、mask 外扩、paste 策略（padstack 本地坐标系）
  5. 按 mount × 孔形聚合，输出每个约定值的支持度计数与例外清单
  6. 命名语法：由尺寸反推候选名，与实际名比对得语法命中率
  7. 列守卫：pads.txt 27 列全普查，"未消费且未豁免"的非空列显名——
     新字段/(schema 漂移)不可能再静默丢失

用法（项目根或任意目录）：
    .venv\\Scripts\\python.exe Tools\\mine_conventions.py [目录]
    .venv\\Scripts\\python.exe Tools\\mine_conventions.py --reaggregate

输出：
    knowledge/conventions.json           机器可读约定库（真源）
    docs/conventions_report.md           人读报告
    Tools/log/conventions_padstacks.json 逐 padstack 原始事实（裁定用）
    Tools/log/conventions_raw/*.pads.txt 原始 dump 归档（列守卫+离线补收）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.cdsroot_locator import find_cdsroot, extracta_path  # noqa: E402
from services.extracta_reader import (  # noqa: E402
    _parse_pads_file,
    _parse_pins_file,
    _read_extracta_header,
)

DEFAULT_SRC = ROOT.parent / "Data" / "PCB_Footprint"
OUT_JSON = ROOT / "knowledge" / "conventions.json"
OUT_MD = ROOT / "docs" / "conventions_report.md"
OUT_RAW = ROOT / "Tools" / "log" / "conventions_padstacks.json"
RAW_DUMP_DIR = ROOT / "Tools" / "log" / "conventions_raw"

MIL2MM = 0.0254

# ------------------------------------------------------------------
# 第 2 层 AI 裁定结论（2026-10-13，Phase 2）
# 裁定对象是"构造参数"而非规则阈值；每条附支持度，可被后续复盘推翻。
# ------------------------------------------------------------------
ADJUDICATED = {
    "th_oblong_plated": {
        "annular_ring_mm": {
            "standard": 0.3, "standard_support": 16,
            "compact_allowed": 0.2, "compact_support": 6,
            "compact_context": "高密度 type_c24（8d34/8d35/9d17）空间不足时收窄",
            "rule_mapping": "ring∈[0.2,0.3]→PASS；[0.15,0.2)∪(0.3,0.4]→WARN；其余→FAIL；NP 件→NA",
        },
        "mask_expand_side_mm": {"value": 0.05, "support": 22},
        "paste_top": {"policy": "=焊盘尺寸", "support": 23},
        "paste_bottom": {"policy": "无", "support": 25},
        "drill_figure": {"value": "OBLONG_Y(本地竖向)", "support": 26,
                         "note": "板面横槽 = 本地竖孔 + pin 旋转 90°；槽型 OVAL SLOT 26/26"},
        "naming": {"grammar": "P OB{孔长}X{孔短} H OB{盘长}X{盘短}", "support": 23,
                   "variants": "NP 前缀=非金属化（盘段可省）；H 后 OB 可省（HO 连写）；OS{X|Y}{off}=槽偏移",
                   "rule_level": "WARN（金标准库自身存在 3 件 legacy 变体，不升级 FAIL）"},
    },
    "th_circle_plated": {
        "annular_ring_mm": {"note": "离散（0/0.15/0.2/0.325），样本 12 件不足",
                            "rule_mapping": "只查下限 ≥0.15 WARN，不设标准值"},
        "mask_expand_side_mm": {"value": 0.05, "support": 9},
        "naming": {"grammar": "PC{孔}HC{盘}", "support": 8,
                   "variants": "NPC{孔}=非金属化（机械孔，盘可省或=占位）"},
    },
    "th_other": {
        "exempt": "SQUARE/HEXAGON/OCTAGON/CROSS 多为安装/定位孔，N plated 占多数——环宽规则 NA",
    },
    "smd": {
        "layers": {"dominant": "TOP+~TSM+~TPM（无 BOTTOM/无 BPM）", "support": 344},
        "naming": {"grammar": "弱约定（R{w}X{h}/SMD_R*/ob*/c* 多套并存），暂不强制",
                   "rule_mapping": "不建命名规则，待后续单独挖掘"},
    },
}


# ------------------------------------------------------------------
# 单件读取：跑一次 extracta，返回 (units, pins_raw, padstacks)
# ★ 解析单一真源 = extracta_reader._parse_pads_file（勿再内联解析：
#   2026-10-13 前的双份实现曾漂移漏掉 drillChar——drillChar 断供根因）。
# padstacks: {name: {"rows": {layer: (figure, w, h)},
#                    "drill": {...} or None, "plated": str, "slot_type": str}}
# ------------------------------------------------------------------
def read_padstacks(dra: Path, exe: Path, cdsroot: Path, view: Path):
    tmp_dir = Path(tempfile.mkdtemp(prefix="mine_"))
    outs = [tmp_dir / n for n in ("pins.txt", "pads.txt", "geom.txt", "refdes.txt")]
    env = os.environ.copy()
    env["CDSROOT"] = str(cdsroot)
    env["PATH"] = f"{cdsroot}\\tools\\bin;{cdsroot}\\tools\\pcb\\bin;" + env.get("PATH", "")
    try:
        subprocess.run(
            [str(exe), str(dra), str(view)] + [str(o) for o in outs],
            env=env, cwd=str(tmp_dir),          # ★ cwd 必设：extracta 会落 extract.log
            capture_output=True, timeout=120,
        )
        if not outs[0].is_file() or outs[0].read_text(encoding="utf-8", errors="replace").strip() == "":
            return None, [], {}
        # ★ 原始 pads.txt 归档（2026-10-13）：tmp 用完即删曾致证据丢失；
        #   留档后补收任何字段可离线重算（--reaggregate），列守卫也吃这份。
        RAW_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(outs[1], RAW_DUMP_DIR / f"{dra.stem}.pads.txt")
        header = _read_extracta_header(outs[0])
        pins = _parse_pins_file(outs[0])
        pads_by_name = _parse_pads_file(outs[1])
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    scale = MIL2MM if header.get("units") == "mil" else 1.0
    padstacks: dict = {}
    for name, entries in pads_by_name.items():
        ps = {"rows": {}, "drill": None, "plated": "", "slot_type": ""}
        for e in entries:
            if e["layer"] == "DRILL":
                fig_w, fig_h = e["size"]    # DRILL 行 size 语义 = figureSize [W,H]
                ps["drill"] = {
                    "diameter": (e.get("drill_diameter") or 0.0) * scale,
                    "fig_w": fig_w * scale, "fig_h": fig_h * scale,
                    "figure": e.get("figure_name", ""),
                    "slot_type": e.get("hole_type", ""),
                    "drill_char": e.get("drill_char", ""),
                }
                ps["plated"] = (e.get("plated") or "").strip()
                ps["slot_type"] = ps["drill"]["slot_type"]
                continue
            ps["rows"][e["layer"]] = (e["figure_name"],
                                      e["size"][0] * scale, e["size"][1] * scale)

        # mount 判定：有实义 drill（任一尺寸>0 或 槽型≠NONE）→ TH。
        # ★ reader 跳过零尺寸行：全零 DRILL（静默零化产物）不会进 drill，
        #   判 smd——与旧内联解析"drill 存在但全零"的判定结果等价。
        d = ps["drill"]
        if d and (d["diameter"] > 0 or d["fig_w"] > 0 or d["fig_h"] > 0
                  or d["slot_type"] not in ("", "NONE")):
            ps["mount"] = "th"
        else:
            ps["mount"] = "smd"
        padstacks[name] = ps
    return header.get("units"), pins, padstacks


def hole_dims(drill: dict) -> tuple:
    """孔的本地帧 (w, h)。oblong 用 figureSize，圆孔 w=h=diameter。"""
    if not drill:
        return None
    fig = (drill.get("figure") or "").upper()
    if "CIRCLE" in fig:
        return (drill["diameter"], drill["diameter"])
    return (drill.get("fig_w") or 0.0, drill.get("fig_h") or 0.0)


def name_encoding(x: float) -> str:
    """1.7 → '1D7'；2.05 → '2D05'；尾零去掉（2.0 → '2'）"""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", "D")


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def _dnum(s: str) -> float:
    """'1D7' / '2D05' / '2' → 数值（容差比对用）"""
    return float(s.replace("D", "."))


def naming_match_th(f) -> str:
    """TH 命名语法判定（2026-10-13 挖掘修正版）。

    主语法：P OB{孔}X{孔} H OB{盘}X{盘}   （长轴在前 = max 先，数值比对）
      - 例：POB1D7X0D5HOB2D3X1D1（板面尺寸序，本地竖向件 name 仍按板面）
      - 变体：NP 前缀 = 非金属化；H 后 OB 可省（HO 连写）；
              NP 件盘=0.1 占位时可省盘段；尾缀 OS{X|Y}{off} = 槽偏移
    circle：PC{孔}H[OB]{盘}
    """
    nm = f["name"].upper()
    base = re.sub(r"OS[XY][0-9D.]+$", "", nm)          # 剥槽偏移尾缀
    if f["mount"] != "th":
        return "smd(未归纳)"
    hole_pair = sorted([v for v in (f["hole_w"], f["hole_h"]) if v], reverse=True)
    pad_pair = sorted([v for v in (f["pad_w"], f["pad_h"]) if v], reverse=True)
    if not hole_pair or not pad_pair:
        return "other"
    # 语料尺寸带截断误差（0.5999/1.2499…），名字用圆整标称值 → round(2)
    hole_pair = [round(v, 2) for v in hole_pair]
    pad_pair = [round(v, 2) for v in pad_pair]
    plated = (f.get("plated") or "").upper() == "P"
    is_oblong = "OBLONG" in (f["hole_figure"] or "").upper()
    is_circle = "CIRCLE" in (f["hole_figure"] or "").upper()

    def eq(a: float, b: float) -> bool:
        return abs(a - b) <= 0.011

    if is_oblong:
        m = re.fullmatch(
            r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)H(?:OB|O)?([0-9D]+)X([0-9D]+)", base)
        if m:
            np_flag, hw, hh, pw, ph = m.groups()
            ok = (eq(_dnum(hw), hole_pair[0]) and eq(_dnum(hh), hole_pair[1])
                  and eq(_dnum(pw), pad_pair[0]) and eq(_dnum(ph), pad_pair[1]))
            if ok:
                tag = "P{孔OB}H{盘OB}"
                return tag if plated else "NP:" + tag
            return "other"
        m = re.fullmatch(r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)", base)  # 盘段省略
        if m:
            np_flag, hw, hh = m.groups()
            if eq(_dnum(hw), hole_pair[0]) and eq(_dnum(hh), hole_pair[1]):
                return "NP:{孔OB}(盘省略)" if not plated else "other"
        return "other"
    if is_circle:
        d = round(f.get("hole_diameter") or 0, 2)
        m = re.fullmatch(r"(NP)?P?C([0-9D]+)H(?:OB|C)?([0-9D]+)", base)
        if m:
            np_flag, h, p = m.groups()
            if eq(_dnum(h), d) and eq(_dnum(p), pad_pair[0]):
                tag = "PC{孔}HC{盘}"
                return tag if plated else "NP:" + tag
        return "other"
    return "other"


# ------------------------------------------------------------------
# 列守卫（2026-10-13）：pads.txt 全列普查 vs 消费/豁免登记
# 目标：任何"未被消费也未被豁免"的非空列必须显名——drillChar 式的
#       静默丢失在架构上不再可能。列位以原始行（含 S/A 标记列）计。
# 原始 27 列（A! 头行给名；DRILL 行挪用部分列位，括号注 DRILL 语义）：
#   r0 标记(S/A) | r1 PAD_NAME | r2 REC_NUMBER | r3 LAYER(~DRILL)
#   r4 FIXFLAG | r5 VIAFLAG | r6 PADSHAPE1(DRILL: drillDiameter)
#   r7 PADWIDTH(DRILL: figureSizeW) | r8 PADHGHT(DRILL: figureSizeH)
#   r9 PADXOFF | r10 PADYOFF | r11 PADFLASH(DRILL: figure 名)
#   r12 PADSHAPENAME(DRILL: plated P/N) | r13 TRELSHAPE1(DRILL: drillChar)
#   r14~r19 TREL*(负片热焊盘) | r20 APADSHAPE1(DRILL: holeType)
#   r21~r26 APAD*(负片隔离盘)
# ------------------------------------------------------------------
GUARD_CONSUMED = {
    "LAYER": {"r1", "r3", "r6", "r7", "r8", "r9", "r10"},
    "DRILL": {"r1", "r3", "r6", "r7", "r8", "r11", "r12", "r13", "r20"},
}
_GUARD_TREL = "负片热焊盘/隔离盘家族（豁免：检查范围不含负片连接定义）"
GUARD_EXEMPT = {
    "LAYER": {
        "r0": "行标记", "r2": "记录序号", "r4": "FIXFLAG 标志位", "r5": "VIAFLAG 标志位",
        "r11": "PADFLASH flash 型焊盘引用标志（当前消费链不建模 flash 焊盘，显式豁免）",
        "r12": "PADSHAPENAME flash 型焊盘引用名（语料 106 行有值；不建模 flash 焊盘，显式豁免待后续裁定）",
        "r13": "TRELSHAPE1 " + _GUARD_TREL, "r14": "TRELWIDTH " + _GUARD_TREL,
        "r15": "TRELHGHT " + _GUARD_TREL, "r16": "TRELXOFF " + _GUARD_TREL,
        "r17": "TRELYOFF " + _GUARD_TREL, "r18": "TRELFLASH " + _GUARD_TREL,
        "r19": "TRELSHAPENAME " + _GUARD_TREL,
        "r20": "APADSHAPE1 " + _GUARD_TREL, "r21": "APADWIDTH " + _GUARD_TREL,
        "r22": "APADHGHT " + _GUARD_TREL, "r23": "APADXOFF " + _GUARD_TREL,
        "r24": "APADYOFF " + _GUARD_TREL, "r25": "APADFLASH " + _GUARD_TREL,
        "r26": "APADSHAPENAME " + _GUARD_TREL,
    },
    "DRILL": {
        "r0": "行标记", "r2": "记录序号", "r4": "FIXFLAG 标志位", "r5": "VIAFLAG 标志位",
        "r9": "XOFF（drill 行恒 0）", "r10": "YOFF（drill 行恒 0）",
        "r14": "TRELWIDTH " + _GUARD_TREL, "r15": "TRELHGHT " + _GUARD_TREL,
        "r16": "TRELXOFF " + _GUARD_TREL, "r17": "TRELYOFF " + _GUARD_TREL,
        "r18": "TRELFLASH " + _GUARD_TREL, "r19": "TRELSHAPENAME " + _GUARD_TREL,
        "r21": "APADWIDTH " + _GUARD_TREL, "r22": "APADHGHT " + _GUARD_TREL,
        "r23": "APADXOFF " + _GUARD_TREL, "r24": "APADYOFF " + _GUARD_TREL,
        "r25": "APADFLASH " + _GUARD_TREL, "r26": "APADSHAPENAME " + _GUARD_TREL,
    },
}
GUARD_EXPECTED_HEADER = [
    "PAD_NAME", "REC_NUMBER", "LAYER", "FIXFLAG", "VIAFLAG",
    "PADSHAPE1", "PADWIDTH", "PADHGHT", "PADXOFF", "PADYOFF",
    "PADFLASH", "PADSHAPENAME", "TRELSHAPE1", "TRELWIDTH", "TRELHGHT",
    "TRELXOFF", "TRELYOFF", "TRELFLASH", "TRELSHAPENAME",
    "APADSHAPE1", "APADWIDTH", "APADHGHT", "APADXOFF", "APADYOFF",
    "APADFLASH", "APADSHAPENAME",
]


def column_guard(dump_dir: Path) -> dict:
    """普查 dump_dir 下全部 *.pads.txt 的每一列。

    产出三块：
      unconsumed_unexempt —— ★ 未消费且未豁免的非空列（新字段/schema 漂移，
                              必须人工裁定后纳入消费或豁免登记）
      exempt_nonempty     —— 已豁免且确实有值的列（知悉性列出）
      header_drift        —— A! 头行列名/列数与预期不符（Cadence 版本漂移）
    """
    stats: dict = defaultdict(Counter)   # kind -> r[i] -> 非空计数
    examples: dict = {}
    n_files = 0
    drift = []
    for dump in sorted(dump_dir.glob("*.pads.txt")):
        try:
            lines = dump.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        n_files += 1
        for line in lines:
            line = line.rstrip()
            if line.startswith("A!"):
                header_cols = [x for x in line[2:].split("!") if x]
                if header_cols != GUARD_EXPECTED_HEADER:
                    drift.append({"file": dump.name, "columns": header_cols})
                continue
            if not line.startswith("S!"):
                continue
            r = line.split("!")            # 含标记列，r[0]='S'
            kind = "DRILL" if len(r) > 3 and r[3] == "~DRILL" else "LAYER"
            for i, v in enumerate(r):
                if v.strip():
                    stats[kind][i] += 1
                    examples.setdefault((kind, i), v[:40])
    findings, known = [], []
    for kind in ("DRILL", "LAYER"):
        consumed = GUARD_CONSUMED[kind]
        exempt = GUARD_EXEMPT[kind]
        for i in sorted(stats[kind]):
            key = f"r{i}"
            if key in consumed:
                continue
            item = {"column": key, "nonempty": stats[kind][i],
                    "example": examples.get((kind, i), "")}
            if key in exempt:
                item["exempt_reason"] = exempt[key]
                known.append(item)
            else:
                findings.append(item)
    return {"files": n_files, "unconsumed_unexempt": findings,
            "exempt_nonempty": known, "header_drift": drift}


def aggregate(facts: list, errors: list, src: Path) -> dict:
    def group_key(f):
        if f["mount"] == "th":
            fig = (f["hole_figure"] or "NONE").upper()
            shape = "circle" if "CIRCLE" in fig else (
                "oblong" if "OBLONG" in fig else "other")
        else:
            shape = "none"
        return (f["mount"], shape)

    groups = defaultdict(list)
    for f in facts:
        groups[group_key(f)].append(f)

    def hist(items, key, nd=3):
        c = Counter()
        for it in items:
            v = it.get(key)
            if v is None:
                continue
            if isinstance(v, (int, float)):
                v = round(float(v), nd)
            c[v] += 1
        return c.most_common()

    def layer_hist(items):
        c = Counter()
        for it in items:
            c["+".join(it["layers"])] += 1
        return c.most_common()

    conventions = {}
    for gkey, items in sorted(groups.items()):
        mount, shape = gkey
        gname = f"{mount.lower()}_{shape}" if mount == "th" else mount.lower()
        used = [f for f in items if f["pin_count"] > 0] or items  # 只统计被 pin 引用的
        conv = {
            "sample_count": len(items),
            "used_count": len(used),
            "layers": layer_hist(used),
            "hole_figure": hist(used, "hole_figure"),
            "slot_type": hist(used, "hole_slot_type"),
            "plated": hist(used, "plated"),
        }
        if mount == "th":
            conv["ring_x"] = hist(used, "ring_x")
            conv["ring_y"] = hist(used, "ring_y")
            conv["mask_expand_x"] = hist(used, "mask_expand_x")
            conv["mask_expand_y"] = hist(used, "mask_expand_y")
            # drillChar 仅聚合可见（信息性）：设计级钻头表字符，随图纸变，
            # 不作为 padstack 构造约定强制（WIRING 登记为 report-only）。
            conv["drill_char"] = hist(used, "drill_char")
            conv["paste_top_size_eq_pad"] = sum(
                1 for f in used if f["paste_top"]
                and abs(f["paste_top"][0] - (f["pad_w"] or 0)) < 0.01
                and abs(f["paste_top"][1] - (f["pad_h"] or 0)) < 0.01)
            conv["paste_top_present"] = sum(1 for f in used if f["paste_top"])
            conv["paste_bottom_present"] = sum(1 for f in used if f["paste_bottom"])
            rotc = Counter()
            for f in used:
                for r, n in (f.get("rotations") or {}).items():
                    rotc[r] += n
            conv["rotation"] = rotc.most_common()  # pin 旋转角分布概览
        # ---------- 命名语法（2026-10-13 挖掘修正版，见 naming_match_th） ----------
        gram = Counter()
        if mount == "th":
            for f in used:
                gram[naming_match_th(f)] += 1
            conv["naming"] = gram.most_common()
        conventions[gname] = conv

    # ---------- 列守卫（读归档 dump，两种模式都能跑） ----------
    guard = column_guard(RAW_DUMP_DIR)

    # ---------- 输出 ----------
    try:
        dra_count = len(list(Path(src).glob("*.dra")))
    except Exception:
        dra_count = 0
    out = {
        "schema": "layout-forge-conventions/1",
        "mined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": {"root": str(src), "dra_count": dra_count,
                   "error_count": len(errors)},
        "adjudicated": ADJUDICATED,
        "column_guard": guard,
        "conventions": conventions,
    }
    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # 人读报告
    md = [f"# 构造规约挖掘报告（第 0 层）", "",
          f"- 挖掘时间：{out['mined_at']}",
          f"- 语料库：`{src}`（{dra_count} 件 .dra，失败 {len(errors)}）",
          f"- 机器真源：`knowledge/conventions.json`；逐件事实：`Tools/log/conventions_padstacks.json`",
          "", "## 约定总表", ""]
    for gname, conv in conventions.items():
        md.append(f"## {gname}（样例 {conv['sample_count']}，被 pin 引用 {conv['used_count']}）")
        md.append("")
        md.append("| 约定项 | 取值 → 支持度 |")
        md.append("|--|--|")
        for key in ("layers", "hole_figure", "slot_type", "plated", "drill_char",
                    "ring_x", "ring_y", "mask_expand_x", "mask_expand_y"):
            if key in conv:
                vals = ", ".join(f"{k}×{v}" for k, v in conv[key][:5])
                md.append(f"| {key} | {vals} |")
        if "ring_x" in conv:
            md.append(f"| paste_top=pad尺寸 | {conv['paste_top_size_eq_pad']}/{conv['paste_top_present']}（有 paste 的件中） |")
            md.append(f"| paste_bottom 存在 | {conv['paste_bottom_present']}/{conv['used_count']} |")
        if conv.get("naming"):
            md.append(f"| 命名语法 | {conv['naming']} |")
        md.append("")
    if guard["unconsumed_unexempt"] or guard["header_drift"]:
        md.append("## ★ 列守卫：未消费且未豁免（需人工裁定）")
        md.append("")
        for it in guard["unconsumed_unexempt"]:
            md.append(f"- `{it['column']}` 非空×{it['nonempty']}，例：`{it['example']}`")
        for d in guard["header_drift"]:
            md.append(f"- ★ 头行漂移 `{d['file']}`：{d['columns']}")
        md.append("")
    md.append("## 列守卫：已豁免且实际有值（知悉项）")
    md.append("")
    for it in guard["exempt_nonempty"]:
        md.append(f"- `{it['column']}` 非空×{it['nonempty']} —— {it['exempt_reason']}")
    md.append("")
    if errors:
        md.append("## 读取失败清单")
        md.append("")
        for e in errors:
            md.append(f"- `{e['file']}`: {e['error']}")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    if guard["unconsumed_unexempt"] or guard["header_drift"]:
        print(f"[mine] ★ 列守卫：{len(guard['unconsumed_unexempt'])} 个未消费未豁免列"
              f" + {len(guard['header_drift'])} 头行漂移 —— 见报告，需人工裁定")
    print(f"[mine] ✓ {OUT_JSON}")
    print(f"[mine] ✓ {OUT_MD}")
    return out


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else DEFAULT_SRC

    if "--reaggregate" in sys.argv:
        # 离线重算：不再跑 extracta，直接用已落盘的逐件事实重聚合（秒级）
        if not OUT_RAW.is_file():
            print(f"[mine] ✗ 无 {OUT_RAW}，请先完整跑一次")
            return 1
        facts = json.loads(OUT_RAW.read_text(encoding="utf-8"))
        print(f"[mine] reaggregate: {len(facts)} facts")
        out = aggregate(facts, [], src)
        print(f"[mine] ✓ {OUT_RAW}（未变动）")
        return 0

    dras = sorted(src.glob("*.dra"))
    print(f"[mine] 语料库: {src}  *.dra = {len(dras)}")
    cdsroot = find_cdsroot()
    if cdsroot is None:
        print("[mine] ✗ 未找到 CDSROOT")
        return 1
    exe = extracta_path(cdsroot)
    view = ROOT / "services" / "extracta_views" / "footprint_views.txt"

    # 事实收集（全部转 mm）
    padstack_facts = []      # 逐 padstack 原始事实（落盘供裁定）
    errors = []
    for i, dra in enumerate(dras, 1):
        try:
            units, pins, padstacks = read_padstacks(dra, exe, cdsroot, view)
        except Exception as e:  # noqa: BLE001
            errors.append({"file": dra.name, "error": f"{type(e).__name__}: {e}"})
            continue
        if padstacks is None:
            errors.append({"file": dra.name, "error": "extracta 输出为空"})
            continue
        if i % 25 == 0:
            print(f"[mine] {i}/{len(dras)}")
        # pin→padstack 使用计数 + 旋转
        use = defaultdict(lambda: {"count": 0, "rotations": Counter()})
        for p in pins:
            nm = p.get("pad_stack_name", "")
            if nm in padstacks:
                rot = p.get("rotation") or 0.0
                use[nm]["count"] += 1
                use[nm]["rotations"][round(rot) % 180] += 1
        for name, ps in padstacks.items():
            # ★ 层键 = reader 映射后的层名（_PAD_LAYER_MAP）：
            #   ETCH/TOP、PIN/SOLDERMASK_TOP、PIN/PASTEMASK_TOP、PIN/PASTEMASK_BOTTOM
            top = ps["rows"].get("ETCH/TOP")
            tsm = ps["rows"].get("PIN/SOLDERMASK_TOP")
            tpm = ps["rows"].get("PIN/PASTEMASK_TOP")
            bpm = ps["rows"].get("PIN/PASTEMASK_BOTTOM")
            drill = ps["drill"] if ps["mount"] == "th" else None
            hole = hole_dims(drill)
            fact = {
                "file": dra.name,
                "name": name,
                "mount": ps["mount"],
                "pad_figure": top[0] if top else "",
                "pad_w": round(top[1], 4) if top else None,
                "pad_h": round(top[2], 4) if top else None,
                "hole_figure": drill["figure"] if drill else "",
                "hole_slot_type": drill["slot_type"] if drill else "",
                "hole_w": round(hole[0], 4) if hole else None,
                "hole_h": round(hole[1], 4) if hole else None,
                "hole_diameter": round(drill["diameter"], 4) if drill else None,
                "drill_char": drill.get("drill_char", "") if drill else "",
                "plated": ps["plated"],
                "mask_w": round(tsm[1], 4) if tsm else None,
                "mask_h": round(tsm[2], 4) if tsm else None,
                "paste_top": (round(tpm[1], 4), round(tpm[2], 4)) if tpm else None,
                "paste_bottom": (round(bpm[1], 4), round(bpm[2], 4)) if bpm else None,
                "layers": sorted(ps["rows"].keys()) + (["DRILL"] if drill else []),
                "pin_count": use.get(name, {}).get("count", 0),
                "rotations": dict(use.get(name, {}).get("rotations", {})),
            }
            if top and hole and all(v > 0 for v in (top[1], top[2], hole[0], hole[1])):
                fact["ring_x"] = round((top[1] - hole[0]) / 2, 4)
                fact["ring_y"] = round((top[2] - hole[1]) / 2, 4)
            if top and tsm:
                fact["mask_expand_x"] = round((tsm[1] - top[1]) / 2, 4)
                fact["mask_expand_y"] = round((tsm[2] - top[2]) / 2, 4)
            padstack_facts.append(fact)

    print(f"[mine] 收割完成: padstack {len(padstack_facts)} 个, 失败 {len(errors)} 件")
    OUT_RAW.parent.mkdir(parents=True, exist_ok=True)
    OUT_RAW.write_text(json.dumps(padstack_facts, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"[mine] ✓ {OUT_RAW}")

    aggregate(padstack_facts, errors, src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
