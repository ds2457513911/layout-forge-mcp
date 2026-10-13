# -*- coding: utf-8 -*-
"""
services/convention_engine.py —— 构造约定引擎（2026-10-13 第 0 层落地）

★ 定位（用户 2026-10-13 架构裁定）
  check 与 create 是同一份"封装应该长什么样"知识的正反两个函数。
  本模块把知识真源收敛到 knowledge/conventions.json（由
  Tools/mine_conventions.py 从 253 件金标准挖掘 + AI 裁定产生），
  三方同源：
    - builder：apply_conventions() 按约定派生 TH padstack（spec 只声明
      孔位/pin，不再手写构造字典）——正变换
    - checker：2.7/2.8/2.9/2.10 规则读同一份约定做符合性校验——逆变换
    - 出厂自检（Phase 4）：build 后 extracta 回读，按同一份约定验证

  ★ 板面帧 vs 本地帧（挖掘实锤的关键约定）
    金标准 TH oblong padstack 一律**本地竖向**定义：
      pad OBLONG_X (短×长)、drill OBLONG_Y figureSize(短:长)，
      pin 旋转 90° 后落到板面成为横槽。
    命名用**板面尺寸序**（长轴在前）：POB1D7X0D5HOB2D3X1D1。
    本引擎对外接口一律收**板面帧**(长, 短)，内部转本地帧。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONV_PATH = ROOT / "knowledge" / "conventions.json"

# 约定文件缺失/损坏时的兜底值（= 2026-10-13 裁定值，与 conventions.json 一致）
FALLBACK = {
    "ring_standard": 0.30,       # 环宽标准（单边）
    "ring_compact": 0.20,        # 紧凑允许（高密度 type-c）
    "ring_warn_lo": 0.15,
    "ring_warn_hi": 0.40,
    "mask_expand_side": 0.05,    # 阻焊单边外扩
    "paste_top_eq_pad": True,    # 顶面钢网 = 焊盘尺寸
    "paste_bottom": False,       # 底面无钢网
}

_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Any]] = None


def load_conventions(force: bool = False) -> Dict[str, Any]:
    """读约定库（带缓存）。文件缺失/损坏 → {"fallback": True}，用内置兜底值。"""
    global _CACHE
    with _LOCK:
        if _CACHE is not None and not force:
            return _CACHE
        try:
            data = json.loads(CONV_PATH.read_text(encoding="utf-8"))
            if not isinstance(data.get("conventions"), dict):
                raise ValueError("conventions 缺失")
            data["fallback"] = False
        except Exception:
            data = {"fallback": True, "adjudicated": {}, "conventions": {}}
        _CACHE = data
        return data


def th_oblong_params() -> Dict[str, Any]:
    """TH oblong 构造参数（裁定值 + 支持度；不可用时给兜底并打 fallback 标）"""
    conv = load_conventions()
    adj = (conv.get("adjudicated") or {}).get("th_oblong_plated") or {}
    ring = (adj.get("annular_ring_mm") or {})
    mask = (adj.get("mask_expand_side_mm") or {})
    paste = (adj.get("paste_top") or {})
    return {
        "ring_standard": float(ring.get("standard", FALLBACK["ring_standard"])),
        "ring_compact": float(ring.get("compact_allowed", FALLBACK["ring_compact"])),
        "ring_warn_lo": FALLBACK["ring_warn_lo"],
        "ring_warn_hi": FALLBACK["ring_warn_hi"],
        "mask_expand_side": float(mask.get("value", FALLBACK["mask_expand_side"])),
        "paste_top_eq_pad": FALLBACK["paste_top_eq_pad"] if paste else False,
        "paste_bottom": FALLBACK["paste_bottom"],
        "fallback": conv.get("fallback", False),
        "support": {
            "ring_standard": ring.get("standard_support"),
            "ring_compact": ring.get("compact_support"),
            "mask": mask.get("support"),
            "paste": paste.get("support"),
        },
    }


# ------------------------------------------------------------------
# 命名语法（与 Tools/mine_conventions.py 同源逻辑；此处为消费侧真源）
# ------------------------------------------------------------------
def _dnum(s: str) -> float:
    return float(s.replace("D", "."))


def naming_match_th(name: str, hole_board: Tuple[float, float],
                    pad_board: Tuple[float, float], shape: str,
                    plated: bool) -> str:
    """
    判定 padstack 名是否符合库命名语法（数值比对，格式变体鲁棒）。
    hole_board/pad_board 均为板面帧 (长, 短)。
    返回标签：主语法 / NP:主语法 / NP:{孔OB}(盘省略) / other
    """
    base = re.sub(r"OS[XY][0-9D.]+$", "", (name or "").upper())
    hole = sorted([round(v, 2) for v in hole_board if v], reverse=True)
    pad = sorted([round(v, 2) for v in pad_board if v], reverse=True)
    if not hole or not pad:
        return "other"

    def eq(a: float, b: float) -> bool:
        return abs(a - b) <= 0.011

    if shape == "oblong":
        m = re.fullmatch(
            r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)H(?:OB|O)?([0-9D]+)X([0-9D]+)", base)
        if m:
            flag, hw, hh, pw, ph = m.groups()
            if (eq(_dnum(hw), hole[0]) and eq(_dnum(hh), hole[1])
                    and eq(_dnum(pw), pad[0]) and eq(_dnum(ph), pad[1])):
                tag = "P{孔OB}H{盘OB}"
                return tag if plated else "NP:" + tag
            return "other"
        m = re.fullmatch(r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)", base)
        if m:
            flag, hw, hh = m.groups()
            if eq(_dnum(hw), hole[0]) and eq(_dnum(hh), hole[1]):
                return "NP:{孔OB}(盘省略)" if not plated else "other"
        return "other"
    if shape == "circle":
        m = re.fullmatch(r"(NP)?P?C([0-9D]+)H(?:OB|C)?([0-9D]+)", base)
        if m:
            flag, h, p = m.groups()
            if eq(_dnum(h), hole[0]) and eq(_dnum(p), pad[0]):
                tag = "PC{孔}HC{盘}"
                return tag if plated else "NP:" + tag
        return "other"
    return "other"


def derive_th_name(hole_board: Tuple[float, float],
                   pad_board: Tuple[float, float], plated: bool = True) -> str:
    """按主语法派生 padstack 名（板面帧，长轴在前，1 位小数去尾零）"""

    def enc(v: float) -> str:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s.replace(".", "D")

    hl, hs = sorted([round(v, 2) for v in hole_board], reverse=True)
    pl, ps = sorted([round(v, 2) for v in pad_board], reverse=True)
    name = f"POB{enc(hl)}X{enc(hs)}HOB{enc(pl)}X{enc(ps)}"
    return name if plated else f"NP{name}"


# ------------------------------------------------------------------
# builder 侧：按约定派生 TH oblong padstack（正变换）
# ------------------------------------------------------------------
def _oblong_hole_type() -> str:
    """TH oblong 的 holeType（约定库 slot_type → SKILL 符号；默认 OVAL_SLOT）。

    ★ 约定库 26/26 金标准都是 'OVAL SLOT'；SKILL 端合法符号是 'OVAL_SLOT。
      'OBLONG_DRILL 是非法值会被静默回落 CIRCLE_DRILL（2026-10-13 实锤）。
    """
    conv = load_conventions()
    hist = ((conv.get("conventions") or {}).get("th_oblong") or {}).get("slot_type") or []
    for val, _n in hist:
        if "OVAL" in str(val).upper():
            return "OVAL_SLOT"
    return "OVAL_SLOT"


def derive_th_oblong_padstack(hole_board: Tuple[float, float],
                              pad_board: Optional[Tuple[float, float]] = None,
                              plated: bool = True,
                              name: Optional[str] = None,
                              slot_offset: Optional[Tuple[float, float]] = None,
                              paste_bottom: bool = False,
                              ) -> Tuple[Dict[str, Any], List[str]]:
    """
    从孔的板面尺寸 (长, 短) 派生符合库约定的 TH oblong padstack。

    返回 (ps_dict, notes)。ps_dict 与 spec padstack 字典同构（builder 直接消费）：
      layers 为**本地竖向帧**（金标准口径），drill 带 figure=OBLONG_Y。
    pad_board 缺省时按标准环宽派生；显式给 pad 时做环宽约定校验。
    paste_bottom=True = spec 显式覆盖（库 25/26 底面无钢网；个别家族金标准
    带 BPM=焊盘尺寸，如 LYDPJ/audio 族——须 spec 声明，不走默认）。
    """
    p = th_oblong_params()
    notes: List[str] = []
    hl, hs = sorted([float(v) for v in hole_board], reverse=True)

    if pad_board is None:
        pl, ps = hl + 2 * p["ring_standard"], hs + 2 * p["ring_standard"]
        notes.append(f"焊盘按标准环宽 {p['ring_standard']} 派生：{pl}x{ps}")
    else:
        pl, ps = sorted([float(v) for v in pad_board], reverse=True)
        ring_x = (pl - hl) / 2
        ring_y = (ps - hs) / 2
        for tag, r in (("X", ring_x), ("Y", ring_y)):
            lo, hi = p["ring_warn_lo"], p["ring_warn_hi"]
            if r < lo or r > hi:
                notes.append(f"⚠ 环宽{tag}={r:.2f} 超出约定范围 [{lo},{hi}]（金标准"
                             f" {p['support']['ring_standard']} 件标准 0.3）——需人工确认")
            elif abs(r - p["ring_standard"]) > 1e-9:
                notes.append(f"环宽{tag}={r:.2f}（非标准 0.3，紧凑允许 "
                             f"{p['ring_compact']}，高密度件口径）")

    me = p["mask_expand_side"]
    # 本地竖向帧：pad (短, 长)
    pad_local = (round(ps, 4), round(pl, 4))
    mask_local = (round(ps + 2 * me, 4), round(pl + 2 * me, 4))
    drill_local = (round(hs, 4), round(hl, 4))

    layers = [
        {"layer": "TOP", "figure": "OBLONG_X", "size": list(pad_local)},
        # ★ 层名必须用 API 名 DEFAULT_INTERNAL（db/extracta 视图显示为
        #   internal_pad_def）。直接传 "internal_pad_def" 会被 no_check
        #   静默忽略 → 内层焊盘缺失（2026-10-13 compare_extracta 首跑实锤）。
        {"layer": "DEFAULT_INTERNAL", "figure": "OBLONG_X", "size": list(pad_local)},
        {"layer": "BOTTOM", "figure": "OBLONG_X", "size": list(pad_local)},
        {"layer": "SOLDERMASK_TOP", "figure": "OBLONG_X", "size": list(mask_local)},
        {"layer": "SOLDERMASK_BOTTOM", "figure": "OBLONG_X", "size": list(mask_local)},
        {"layer": "PASTEMASK_TOP", "figure": "OBLONG_X", "size": list(pad_local)},
    ]
    if paste_bottom:
        layers.append({"layer": "PASTEMASK_BOTTOM", "figure": "OBLONG_X",
                       "size": list(pad_local)})
        notes.append("spec 显式声明底面钢网（PASTEMASK_BOTTOM=焊盘尺寸）——"
                     "与库 25/26「底面无」惯例不同，属金标准家族例外，已放行")
    drill = {
        "size": list(drill_local),          # 本地竖向帧（X宽 Y长）
        # ★ 2026-10-13 工程师否决第 4 课：oblong 槽孔必须是 OVAL_SLOT
        #   （'OBLONG_DRILL 是非法 holeType，被静默回落 CIRCLE_DRILL，
        #   Editor 显示 Thru Pin）。约定库 slot_type='OVAL SLOT'×26 支撑。
        "drill_type": _oblong_hole_type(),
        "figure": "OBLONG_Y",
        "plated": bool(plated),
    }
    if slot_offset:
        drill["offset"] = list(slot_offset)

    final_name = name or derive_th_name((hl, hs), (pl, ps), plated)
    ps_name = naming_match_th(final_name, (hl, hs), (pl, ps), "oblong", plated)
    if ps_name == "other":
        fixed = derive_th_name((hl, hs), (pl, ps), plated)
        notes.append(f"⚠ padstack 名 `{final_name}` 不合库语法（应为 "
                     f"POB{{孔}}X{{孔}}HOB{{盘}}X{{盘}}）→ 改用 `{fixed}`")
        final_name = fixed

    return (
        {"name": final_name, "layers": layers, "drill": drill},
        notes,
    )


def apply_conventions(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    builder 入口：对 spec 做约定符合化预处理（正变换）。
    只处理 mount=tht 且孔为 oblong 的主/附加 padstack；其余原样透传。
    - 用 spec 的孔尺寸 + （可选）焊盘尺寸重建 padstack 本地字典
    - 为使用 oblong TH padstack 的 pin 补 rotation=90（板面横槽口径）
    返回 (spec', notes)。
    """
    notes: List[str] = []
    if str(spec.get("mount", "")).lower() != "tht":
        return spec, notes
    spec = json.loads(json.dumps(spec))  # 深拷贝，不污染调用方

    main = spec.get("padstack") or {}
    extras = spec.get("extra_padstacks") or []
    oblong_names = set()
    rename_map = {}          # 旧名 → 新名（同步改写 pin 引用）

    def _one(ps: Dict[str, Any], pad_board=None) -> None:
        drill = ps.get("drill") or {}
        size = drill.get("size") or []
        # oblong 槽孔两写法都接：OBLONG_DRILL（旧，非法会被回落）与
        # OVAL_SLOT（正确）；其余（circle 等）不归本引擎
        _dt = str(drill.get("drill_type", "")).upper()
        if ("OBLONG" not in _dt and "SLOT" not in _dt) or len(size) != 2:
            return
        offset = drill.get("offset")
        hole_board = (max(size), min(size))
        old_name = str(ps.get("name", ""))
        derived, ns = derive_th_oblong_padstack(
            hole_board,
            pad_board=tuple(pad_board) if pad_board else None,
            plated=bool(drill.get("plated", True)),
            name=ps.get("name"),
            slot_offset=tuple(offset) if offset else None,
            paste_bottom=bool(ps.get("paste_bottom")
                              or spec.get("paste_bottom")),
        )
        notes.extend(ns)
        oblong_names.add(derived["name"].upper())
        if old_name and old_name.upper() != derived["name"].upper():
            rename_map[old_name.upper()] = derived["name"]
        ps.clear()
        ps.update(derived)

    pad_size = ((spec.get("pad") or {}).get("size") or None)
    if main:
        _one(main, pad_size)
    for e in extras:
        _one(e, pad_size)

    # 横槽口径：用 oblong TH padstack 的 pin 一律旋转 90°；改名则同步引用
    for pin in spec.get("pins_override") or []:
        ref = str(pin.get("padstack", "")).upper()
        if ref in rename_map:
            pin["padstack"] = rename_map[ref]
            ref = pin["padstack"].upper()
            notes.append(f"pin{pin.get('number')} padstack 引用同步改名 → {ref}")
        if ref in oblong_names:
            if pin.get("rotation") in (None, 0):
                pin["rotation"] = 90
                notes.append(f"pin{pin.get('number')} 补 rotation=90"
                             "（本地竖孔→板面横槽，金标准口径）")
    return spec, notes


# ------------------------------------------------------------------
# checker / 自检侧：约定符合性校验（逆变换）
# ------------------------------------------------------------------
def check_pin_conventions(pin: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """
    对单个 pin 的 extracta 事实做约定校验。
    pin.pads 的 size 已是板面帧（extracta_reader 按 pin 旋转换算过）。
    返回 [(level, rule_id, msg)]，level ∈ FAIL/WARN/NA-msg。
    """
    out: List[Tuple[str, str, str]] = []
    pads = [pd for pd in (pin.get("pads") or []) if isinstance(pd, dict)]
    name = str(pin.get("name") or "")

    def _find(suffix: str) -> Optional[Dict[str, Any]]:
        for pd in pads:
            lay = str(pd.get("layer", "")).upper()
            if lay == suffix or lay.endswith("/" + suffix):
                return pd
        return None

    etch = _find("ETCH/TOP")
    drill = _find("DRILL")
    is_np = name.startswith("NP")
    if not etch or not drill:
        return out
    hole_fig = str(drill.get("figure_name", "")).upper()
    if "OBLONG" not in hole_fig and "OB" not in name.upper():
        return out                      # 非 oblong 不走本规则（circle 另论）
    if is_np:
        out.append(("NA", "2.8", f"padstack {name} 非金属化机械孔，环宽豁免"))
        return out
    pw, ph = (etch.get("size") or [0, 0])
    hw, hh = (drill.get("size") or [0, 0])
    if hw <= 0 or hh <= 0:
        out.append(("FAIL", "2.7", f"DRILL 实义为零（孔未钻/被静默零化）"))
        return out
    # ★ 2.7c holeType 校验（2026-10-13 工程师否决第 4 课）：oblong 孔必须是
    #   OVAL SLOT 槽孔；'OBLONG_DRILL 被静默回落 CIRCLE_DRILL（Thru Pin）。
    ht = str(drill.get("hole_type") or "").upper().replace("_", " ")
    if ht and "OVAL SLOT" not in ht:
        out.append(("FAIL", "2.7",
                    f"oblong 孔 holeType={drill.get('hole_type')}——应建成 "
                    f"Slot（OVAL_SLOT + slotSize），当前 Editor 显示 Thru Pin"))
        return out
    p = th_oblong_params()
    rx, ry = (pw - hw) / 2, (ph - hh) / 2
    lo, hi = p["ring_warn_lo"], p["ring_warn_hi"]
    std, comp = p["ring_standard"], p["ring_compact"]
    for tag, r in (("X", rx), ("Y", ry)):
        if comp - 1e-9 <= r <= std + 1e-9:
            continue                    # [0.2, 0.3] PASS
        if lo <= r < comp or std < r <= hi:
            out.append(("WARN", "2.8",
                        f"环宽{tag}={r:.2f} 非标准 0.3（紧凑允许 0.2，"
                        f"约定范围 [{lo},{hi}]）"))
        else:
            out.append(("FAIL", "2.8",
                        f"环宽{tag}={r:.2f} 超出约定范围 [{lo},{hi}]"))
    # paste 策略
    tpm = _find("PASTEMASK/TOP") or _find("PASTEMASK_TOP")
    bpm = _find("PASTEMASK/BOTTOM") or _find("PASTEMASK_BOTTOM")
    if p["paste_top_eq_pad"] and tpm is None:
        out.append(("WARN", "2.10", "TH 件缺顶面钢网（约定 TPM=焊盘尺寸）"))
    if tpm is not None:
        ts = tpm.get("size") or [0, 0]
        if abs(ts[0] - pw) > 0.01 or abs(ts[1] - ph) > 0.01:
            out.append(("WARN", "2.10",
                        f"顶面钢网 {ts[0]}x{ts[1]} ≠ 焊盘 {pw}x{ph}"))
    if p["paste_bottom"] is False and bpm is not None:
        out.append(("WARN", "2.10", "TH 件不应有底面钢网"))
    return out


def check_padstack_naming(pin: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """命名语法（WARN 级）。返回 (rule_id, msg) 或 None。"""
    name = str(pin.get("name") or "")
    if not name:
        return None
    pads = [pd for pd in (pin.get("pads") or []) if isinstance(pd, dict)]
    etch = next((pd for pd in pads
                 if str(pd.get("layer", "")).upper().endswith("ETCH/TOP")), None)
    drill = next((pd for pd in pads
                  if str(pd.get("layer", "")).upper().endswith("DRILL")), None)
    if not etch or not drill:
        return None
    pw, ph = (etch.get("size") or [0, 0])
    hw, hh = (drill.get("size") or [0, 0])
    if hw <= 0 or hh <= 0:
        return None
    plated = not name.upper().startswith("NP")
    hole_fig = str(drill.get("figure_name", "")).upper()
    shape = "oblong" if "OBLONG" in hole_fig else (
        "circle" if "CIRCLE" in hole_fig else "other")
    if shape == "other":
        return None
    tag = naming_match_th(name, (max(hw, hh), min(hw, hh)),
                          (max(pw, ph), min(pw, ph)), shape, plated)
    if tag == "other":
        return ("2.9", f"padstack 名 `{name}` 不合库命名语法"
                       "（TH oblong: POB{孔}X{孔}HOB{盘}X{盘}，长轴在前）")
    return None


# ------------------------------------------------------------------
# 出厂自检（Phase 4，2026-10-13）：build 完 extracta 回读，按约定校验
# ------------------------------------------------------------------
def verify_built_footprint(dra_path: str) -> Dict[str, Any]:
    """
    对刚建出的 .dra 做 extracta 约定自检（check/create 同一份约定）。
    返回 {"ok": bool|None, "issues": [(level, msg)], "is_th": bool, "pin_count": n}
    ok=None 表示 extracta 读不了（Allegro 占用等）——调用方降级为警告不阻断。
    """
    from services.extracta_reader import read_via_extracta
    res = read_via_extracta(dra_path)
    if res.get("error"):
        return {"ok": None, "error": res["error"], "issues": []}
    pins = res.get("pins") or []
    all_layers = set()
    for p in pins:
        for pd in (p.get("pads") or []):
            if isinstance(pd, dict):
                all_layers.add(str(pd.get("layer", "")).upper())
    is_th = bool(all_layers & {"DRILL", "ETCH/BOTTOM"})
    issues: List[Tuple[str, str]] = []
    for p in pins:
        num = p.get("number")
        pd_list = [pd for pd in (p.get("pads") or []) if isinstance(pd, dict)]
        has_drill = any(str(pd.get("layer", "")).upper().endswith("DRILL")
                        for pd in pd_list)
        has_bottom = any(str(pd.get("layer", "")).upper().endswith("ETCH/BOTTOM")
                         for pd in pd_list)
        is_np = str(p.get("name", "")).upper().startswith("NP")
        # 双面铜都在却没有孔 = 铁定断孔（LYDPJ 事故形态）；
        # 单面铜（SMD 脚）与 NP 机械孔无钻孔是合法形态，不误杀
        if has_bottom and not has_drill and not is_np:
            issues.append(("FAIL", f"pin{num}: 无 DRILL 实义（双面铜却无孔——"
                                   "孔未钻/被静默零化）"))
        for lvl, rid, msg in check_pin_conventions(p):
            issues.append((lvl, f"pin{num}: [{rid}] {msg}"))
        nm = check_padstack_naming(p)
        if nm:
            issues.append(("WARN", f"pin{num}: [{nm[0]}] {nm[1]}"))
    fails = [m for lvl, m in issues if lvl == "FAIL"]
    return {"ok": len(fails) == 0, "issues": issues,
            "is_th": is_th, "pin_count": len(pins)}
