# -*- coding: utf-8 -*-
"""
services/padstack_derive.py —— padstack 形态**决策**入口（规则驱动，票 #20 / ADR-0002）

一句话：
  这里决定「这颗 padstack 该长什么样」（层集 / 各层尺寸 / 命名 / 本地帧旋转），
  再把它当**纯数据**交出去；把它实例化成宿主能建的对象**不在这里**。

★ 决策的来源是规则库，不是本文件的常量
  读数 → 事实（mount / 孔形 / 孔与盘尺寸 / plated / 偏移 / paste_bottom / figure）
  → rule_engine.match_form 匹配形态 → 取该形态的 derive 规则 → 用规则里的
  表达式、层模板与命名模板展开。每个派生值都进 decisions（规则 id + 表达式 +
  参数值与出处），形态命中却没有派生规则时报 skips，**不静默产出**（ADR-0002
  D2 / D5 / D6 / D7）。

  层集不再固定：TH 长圆的六层、SMD 的三层、底面钢网的有无，都由规则的
  layers[]（可带 when 条件）决定；尺寸由 values 表达式引用参数算出；命名模板
  在数据里，格式化在 services/padstack_naming.py（ADR D6）。

★ 边界（由 eval/suites/architecture/check_architecture.py A3 守着）
  本模块登记在 CORE_MODULES：不得 import skillbridge / win32com / socket /
  subprocess / mcp_server / orchestration，也不得 import services.footprint_builder。
  即：决策入口必须能**离线调用** —— 吃 dict，吐 dict / list / str / bool。

★ fail-open
  规则库读不起来、某颗 padstack 读数不足 → 记 notes.errors / skips，其余字段原样
  回传；调用方（footprint_builder）决定怎么记日志。这是改造前
  `except Exception ... （原样继续）` 分支的同语义。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from services import padstack_naming, rule_engine

__all__ = [
    "derive_padstacks",
    "ensure_tht_padstack_layers",
    "hole_shape_of",
    "reading_from_output",
]


# ============================================================
# 决策：这颗 padstack 该长什么样
# ============================================================
def ensure_tht_padstack_layers(ps_dict: Optional[Dict[str, Any]]) -> List[str]:
    """
    TH padstack 层兜底（2026-10-12 LYDPJ 事故新增）：有 drill = 通孔件，
    必须双面铜箔（TOP+BOTTOM）+ 双面阻焊。spec 只写 TOP 系时自动镜像补齐。

    派生规则已经给出完整层集；这一层仍保留，用来兜住「形态没匹配上 / 形态还没有
    派生规则」的读数（票 #20 的 SKIP 路径），口径与改造前一致。

    :return: 补层说明列表（空 = 无需补或非 TH）
    """
    fixed: List[str] = []
    if not ps_dict or not ps_dict.get("drill"):
        return fixed
    ls = ps_dict.setdefault("layers", [])

    def _find(layer: str) -> Optional[Dict[str, Any]]:
        for _ld in ls:
            if str(_ld.get("layer", "")).upper() == layer:
                return _ld
        return None

    top = _find("TOP")
    smt = _find("SOLDERMASK_TOP")
    if top is not None and _find("BOTTOM") is None:
        bot = dict(top)
        bot["layer"] = "BOTTOM"
        bot["size"] = list(top.get("size", [0, 0]))
        ls.append(bot)
        fixed.append(f"{ps_dict.get('name')}: 补 BOTTOM(镜像 TOP)")
    if smt is not None and _find("SOLDERMASK_BOTTOM") is None:
        smb = dict(smt)
        smb["layer"] = "SOLDERMASK_BOTTOM"
        smb["size"] = list(smt.get("size", [0, 0]))
        ls.append(smb)
        fixed.append(f"{ps_dict.get('name')}: 补 SOLDERMASK_BOTTOM(镜像 TOP)")
    return fixed


def reading_from_output(solved: Dict[str, Any], mount: Optional[str] = None) -> Dict[str, Any]:
    """把求解输出（或 build_footprint 手里的同名变量）折成 `derive_padstacks` 的读数。

    存在的理由只有一个：**同一份映射只许写一遍**。改造前 build_footprint 里手写的
    `{"mount": "tht", "padstack": ..., "extra_padstacks": ..., "pins_override": ...}`
    与验收对拍脚本里的那份若各写一份，生产侧改了映射、对拍却照旧通过 —— 对拍就成了摆设。

    :param solved: 含 `padstack` / `extra_padstacks` / `pins` 的求解输出或等价变量
    :param mount: 件型；**已不作为派生口径**（票 #20 起每颗 padstack 按「有没有 drill」
                  自己判 TH/SMD，SMD 才能匹配 FORM-MASK-SMD）。保留参数只为兼容旧调用。
    """
    return {
        "mount": mount,
        "padstack": solved.get("padstack"),
        "extra_padstacks": list(solved.get("extra_padstacks") or []),
        "pins_override": list(solved.get("pins") or []),
    }


# ---- 读数 → 事实 ---------------------------------------------------------
def _dims_of(value: Any) -> Tuple[float, float]:
    """尺寸读数归一成 (x, y)：标量 = 两轴同值，两元数组 = (x, y)。"""
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("尺寸数组为空")
        return float(value[0]), float(value[-1])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"尺寸读数不可解析：{value!r}")
    return float(value), float(value)


def _top_layer(ps_dict: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    layers = [ld for ld in (ps_dict.get("layers") or []) if isinstance(ld, dict)]
    if not layers:
        raise ValueError("padstack 没有任何层")
    for layer in layers:
        if str(layer.get("layer") or "").upper() == "TOP":
            return layers, layer
    return layers, layers[0]


def hole_shape_of(drill_type: Any) -> str:
    """drill_type → 形态谓词用的孔形名（Allegro 只有四种孔形，别名归一）。

    公开给求解器用：求解器只借它 + match_form 判断「按哪一形态取 provisional 值」，
    真正的派生仍由本模块 resolve_derive 完成。
    """
    raw = str(drill_type or "").upper()
    if not raw:
        return "CIRCLE_DRILL"          # 没写孔形 = Allegro 默认圆孔
    if "RECTANGLE" in raw and ("SLOT" in raw or "DRILL" in raw):
        return "RECTANGLE_SLOT"
    if "SQUARE" in raw:
        return "SQUARE_DRILL"
    if "OBLONG" in raw or "OVAL" in raw or "SLOT" in raw:
        return "OVAL SLOT"
    if "CIRCLE" in raw or "ROUND" in raw:
        return "CIRCLE_DRILL"
    return raw


def padstack_facts(ps_dict: Dict[str, Any]) -> Dict[str, Any]:
    """一颗 padstack 的读数 → 派生事实（不改入参）。"""
    _layers, top = _top_layer(ps_dict)
    figure = str(top.get("figure") or "").upper()
    shape = padstack_naming.shape_from_figure(figure)
    facts: Dict[str, Any] = {"figure": figure, "shape": shape}
    drill = ps_dict.get("drill")
    if isinstance(drill, dict) and (drill.get("size") is not None
                                    or drill.get("diameter") is not None):
        size = drill.get("size")
        if size is None:
            size = drill.get("diameter")
        hx, hy = _dims_of(size)
        facts.update({
            "mount": "tht",
            "hole_x": hx,
            "hole_y": hy,
            "hole_long": max(hx, hy),
            "hole_short": min(hx, hy),
            "plated": bool(drill.get("plated", True)),
            "hole_shape": hole_shape_of(drill.get("drill_type")),
            "paste_bottom": bool(ps_dict.get("paste_bottom")),
        })
        offset = drill.get("offset")
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            facts["offset_x"] = float(offset[0])
            facts["offset_y"] = float(offset[1])
    else:
        px, py = _dims_of(top.get("size"))
        facts.update({
            "mount": "smd",
            "pad_x": px,
            "pad_y": py,
            "pad_long": max(px, py),
            "pad_short": min(px, py),
        })
    return facts


def _form_subject(ps_dict: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """match_form 的 subject：谓词只读 mount / 孔形 / 层名 / 名字。"""
    _layers, _top = _top_layer(ps_dict)
    return {
        "mount": facts.get("mount"),
        "name": ps_dict.get("name"),
        "drill": {"shape": facts.get("hole_shape")},
        "layers": [str(ld.get("layer") or "") for ld in (_layers or [])],
    }


def _top_size(layers: List[Dict[str, Any]]) -> Tuple[float, float]:
    for layer in layers:
        if str(layer.get("layer") or "").upper() == "TOP":
            return _dims_of(layer.get("size"))
    return _dims_of(layers[0].get("size"))


# ---- 单颗 padstack 的派生 ------------------------------------------------
def _derive_one(ps_dict: Dict[str, Any], lib: Any, index: Dict[str, Any],
                notes: Dict[str, List[str]], decisions: List[Dict[str, Any]],
                skips: List[Dict[str, Any]]) -> Optional[Tuple[str, Optional[float]]]:
    """派生一颗 padstack（就地改 ps_dict）。返回 (新名字, 旋转) 或 None（SKIP）。"""
    old_name = ps_dict.get("name")
    try:
        facts = padstack_facts(ps_dict)
    except Exception as exc:  # noqa: BLE001 —— 读数不足就不猜
        skips.append({"padstack": old_name, "reason": f"读数不足：{exc}"})
        return None

    subject = _form_subject(ps_dict, facts)
    form = rule_engine.match_form(lib, subject)
    if form is None:
        skips.append({"padstack": old_name,
                      "reason": "缺 form：读数（mount/孔形）不匹配任何形态"})
        return None
    form_id = form.get("id")
    derive_rules = (index.get(form_id) or {}).get("derive") or []
    if not derive_rules:
        skips.append({"padstack": old_name, "form": form_id,
                      "reason": f"形态 {form_id} 尚无派生规则（缺派生规则）"})
        return None

    rule = lib.rules_by_id[derive_rules[0]]
    try:
        derived = rule_engine.resolve_derive(lib, rule, form_id, facts)
    except rule_engine.DeriveError as exc:
        skips.append({"padstack": old_name, "form": form_id,
                      "rule_id": rule.get("id"), "reason": exc.message})
        return None

    derived_name = padstack_naming.format_derive_name(derived["name"])
    final_name = derived_name
    for note in derived["notes"]:
        notes["conventions"].append(note)
    if derived["name"].get("style") == "padstack_name" and old_name:
        # SMD：旧名已经表达了同一组尺寸就沿用（写法差异如悬空 D 不算变更），
        # 否则用规则派生名并明说 —— 判定侧靠尺寸反推，不解析模板（ADR D6）。
        if padstack_naming.naming_matches_dims(
                str(old_name), derived["name"].get("dims") or [],
                str(derived["name"].get("shape") or "rect")):
            final_name = str(old_name)
        else:
            notes["conventions"].append(
                f"⚠ padstack 名 `{old_name}` 与派生尺寸不符（应为 `{final_name}`）"
                f"→ 改用 `{final_name}`")
    elif derived["name"].get("style") == "th_oblong" and old_name:
        # 旧名已合主语法 → 沿用（改造前口径）；不合语法 → 用规则派生名并明说
        pad_board = _top_size(derived["layers"])
        tag = padstack_naming.th_oblong_naming_match(
            str(old_name),
            (float(facts["hole_long"]), float(facts["hole_short"])),
            pad_board,
            str(facts.get("shape") or "oblong"),
            bool(facts.get("plated", True)),
        )
        if tag == "other":
            final_name = derived_name
            notes["conventions"].append(
                f"⚠ padstack 名 `{old_name}` 不合库语法（应为 POB{{孔}}X{{孔}}HOB{{盘}}X{{盘}}）"
                f"→ 改用 `{final_name}`")
        else:
            final_name = str(old_name)

    for dec in derived["decisions"]:
        entry = dict(dec)
        entry["padstack"] = old_name
        decisions.append(entry)

    layers = [{"layer": layer.get("layer"), "figure": layer.get("figure"),
               "size": list(layer.get("size") or [])}
              for layer in derived["layers"]]
    new_ps: Dict[str, Any] = {"name": final_name, "layers": layers,
                              "drill": derived["drill"]}
    if facts.get("mount") == "tht":
        new_ps["paste_bottom"] = bool(facts.get("paste_bottom"))
    elif "drill" not in ps_dict:
        new_ps.pop("drill", None)   # 读数本来就没有 drill 键 → 产物也不带（口径不变）

    ps_dict.clear()
    ps_dict.update(new_ps)
    rotation = derived.get("rotation")
    return final_name, (float(rotation) if rotation is not None else None)


def derive_padstacks(reading: Dict[str, Any]) -> Dict[str, Any]:
    """
    把一份读数派生成「padstack 该长什么样」的纯数据计划（规则驱动）。

    :param reading: 只读读数（字段名沿用 spec / solve 输出的口径，缺省按空处理）
        mount            str         **仅有诊断意义**；件型按每颗 padstack 有无 drill 判
        padstack         dict | None 主 padstack 构造字典（无 drill = SMD）
        extra_padstacks  list[dict]  附加 padstack（FPC 固定脚等第二尺寸焊盘）
        pins_override    list[dict]  每个 pin 的 number / x / y / padstack / rotation

    ★ 读数用 `reading_from_output()` 从求解输出折出来，别在这里手搓（映射只许一份）。
    :return: 纯数据计划
        {
          "padstack":        dict | None,  派生后的主 padstack
          "extra_padstacks": list[dict],   派生后的附加 padstack
          "pins_override":   list[dict],   已按派生结果改名的 pin 引用（含旋转）
          "notes": {
            "conventions": list[str],  规则逐条说明 + 命名/旋转变更说明（调用方决定前缀）
            "tht_layers":  list[str],  通孔层兜底补了什么（空 = 无需补或非通孔）
            "errors":      list[str],  规则库/派生的失败原因（fail-open：其余原样回传）
          },
          "decisions": list[dict],  每个派生值一条：item/value/expression/rule_id/source/
                                   form/parameters（参数 id + 取值 + 出处）
          "skips":     list[dict],  形态命中但没派生的原因（缺 form / 缺派生规则 / 缺事实）
        }

    ★ 入参不会被就地修改：内部先 deepcopy。
    """
    plan: Dict[str, Any] = {
        "padstack": copy.deepcopy(reading.get("padstack")),
        "extra_padstacks": copy.deepcopy(list(reading.get("extra_padstacks") or [])),
        "pins_override": copy.deepcopy(list(reading.get("pins_override") or [])),
    }
    notes: Dict[str, List[str]] = {"conventions": [], "tht_layers": [], "errors": []}
    decisions: List[Dict[str, Any]] = []
    skips: List[Dict[str, Any]] = []

    try:
        lib = rule_engine.load_library()
        index = rule_engine.rule_index(lib)
    except Exception as exc:  # noqa: BLE001 —— 规则库不可用时原样继续（旧语义）
        notes["errors"].append(str(exc))
        lib = None

    rename: Dict[str, str] = {}
    rotations: Dict[str, float] = {}
    if lib is not None:
        all_ps = [plan["padstack"]] + list(plan["extra_padstacks"] or [])
        for ps_dict in all_ps:
            if not isinstance(ps_dict, dict):
                continue
            old_name = ps_dict.get("name")
            try:
                got = _derive_one(ps_dict, lib, index, notes, decisions, skips)
            except Exception as exc:  # noqa: BLE001 —— fail-open，原样继续
                notes["errors"].append(f"{old_name}: {exc}")
                continue
            if got is None:
                continue
            final_name, rotation = got
            if isinstance(old_name, str):
                rename[old_name] = final_name
            if rotation is not None:
                rotations[final_name] = rotation

        # ---- pin 引用改名 + 本地帧旋转（只对派生成功的 padstack）----
        for pin in plan["pins_override"]:
            if not isinstance(pin, dict):
                continue
            ref = pin.get("padstack")
            if isinstance(ref, str) and rename.get(ref, ref) != ref:
                ref = rename[ref]
                pin["padstack"] = ref
                notes["conventions"].append(
                    f"pin{pin.get('number')} padstack 引用同步改名 → {ref}")
            rotation = rotations.get(ref) if isinstance(ref, str) else None
            if rotation is not None and pin.get("rotation") in (None, 0):
                pin["rotation"] = rotation
                notes["conventions"].append(
                    f"pin{pin.get('number')} 补 rotation={rotation:g}（本地竖孔→板面横槽，金标准口径）")

    # ---- 层兜底（SKIP 路径仍要双面铜箔/阻焊；主 padstack + 每个附加）----
    fixed = ensure_tht_padstack_layers(plan["padstack"])
    for extra in plan["extra_padstacks"] or []:
        fixed += ensure_tht_padstack_layers(extra)
    notes["tht_layers"] = fixed

    return {
        "padstack": plan["padstack"],
        "extra_padstacks": plan["extra_padstacks"],
        "pins_override": plan["pins_override"],
        "notes": notes,
        "decisions": decisions,
        "skips": skips,
    }
