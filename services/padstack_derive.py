# -*- coding: utf-8 -*-
"""
services/padstack_derive.py —— padstack 形态**决策**入口（ADR-0002 D5 / D8）

一句话：
  这里决定「这颗 padstack 该长什么样」（层集 / 各层尺寸 / 命名 / 旋转），
  把结果当**纯数据**交出去；把它实例化成宿主能建的对象**不在这里**。

为什么拆（票 #16「把 padstack 的决策与实例化拆开」）
  改造前，「决策」与「实例化」混在 services/footprint_builder.py::build_footprint 里：
  1a) 约定符合化（apply_conventions）与 1b) 通孔层兜底（ensure_tht_padstack_layers）
  紧贴着一句 create_padstack(...) 调用，中间只隔几行 warning。
  后果有两个：
    - 想让后续规则引擎整体替换「决策」（ADR-0002 拆票 #17 起）就得动执行器；
    - 「这颗 padstack 本该长什么样」无法在没有 Allegro、没有通道的机器上验证。
  本模块只做搬运：算法与取值口径与改造前**逐字相同**，不引入规则库、不改判定。

★ 边界（由 eval/suites/architecture/check_architecture.py A3 守着）
  本模块登记在 CORE_MODULES：不得 import skillbridge / win32com / socket / subprocess /
  mcp_server / orchestration，也不得 import services.footprint_builder。
  即：决策入口必须能**离线调用** —— 吃 dict，吐 dict / list / str / bool，不开连接、不起进程。

★ 不改判定（票 #16 的硬约束）
  约定内容仍来自 knowledge/conventions.json（services/convention_engine.py 读它）；
  层兜底仍是 ensure_tht_padstack_layers。本模块不新增任何数值、不改任何口径。
  这些知识会收进规则集是 ADR-0002 后续票（#17 起）的事，本票只把入口切开。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from services.convention_engine import apply_conventions

__all__ = ["derive_padstacks", "ensure_tht_padstack_layers", "reading_from_output"]


# ============================================================
# 决策：这颗 padstack 该长什么样
# ============================================================
def ensure_tht_padstack_layers(ps_dict: Optional[Dict[str, Any]]) -> List[str]:
    """
    TH padstack 层兜底（2026-10-12 LYDPJ 事故新增）：有 drill = 通孔件，
    必须双面铜箔（TOP+BOTTOM）+ 双面阻焊。spec 只写 TOP 系时自动镜像补齐。

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


def reading_from_output(solved: Dict[str, Any], mount: str = "tht") -> Dict[str, Any]:
    """把求解输出（或 build_footprint 手里的同名变量）折成 `derive_padstacks` 的读数。

    存在的理由只有一个：**同一份映射只许写一遍**。改造前 build_footprint 里手写的
    `{"mount": "tht", "padstack": ..., "extra_padstacks": ..., "pins_override": ...}`
    与验收对拍脚本里的那份若各写一份，生产侧改了映射、对拍却照旧通过 —— 对拍就成了摆设。

    :param solved: 含 `padstack` / `extra_padstacks` / `pins` 的求解输出或等价变量
    :param mount: 件型；改造前 build_footprint 一律传 `"tht"`（约定引擎按该口径工作，
                  传真实 mount 会改变 SMD 产出，属 ADR-0002 后续票）
    """
    return {
        "mount": mount,
        "padstack": solved.get("padstack"),
        "extra_padstacks": list(solved.get("extra_padstacks") or []),
        "pins_override": list(solved.get("pins") or []),
    }


def derive_padstacks(reading: Dict[str, Any]) -> Dict[str, Any]:
    """
    把一份 spec 形读数派生成「padstack 该长什么样」的纯数据计划。

    :param reading: 只读读数（字段名沿用 spec / solve 输出的口径，缺省按空处理）
        mount            str         「tht」才走通孔约定（约定引擎的既有语义，不是本模块新加的规则）
        padstack         dict | None 主 padstack 构造字典（无 drill = SMD）
        extra_padstacks  list[dict]  附加 padstack（FPC 固定脚等第二尺寸焊盘）
        pins_override    list[dict]  每个 pin 的 number / x / y / padstack / rotation

    ★ 读数用 `reading_from_output()` 从求解输出折出来，别在这里手搓（映射只许一份）。
    :return: 纯数据计划
        {
          "padstack":        dict | None,  派生后的主 padstack
          "extra_padstacks": list[dict],   派生后的附加 padstack
          "pins_override":   list[dict],   已按派生结果改名的 pin 引用（含 rotation 补 90）
          "notes": {
            "conventions": list[str],  约定引擎逐条说明（调用方决定要不要加前缀）
            "tht_layers":  list[str],  通孔层兜底补了什么（空 = 无需补或非通孔）
            "errors":      list[str],  约定引擎失败原因（**fail-open**：失败时其余字段原样回传）
          }
        }

    ★ 入参不会被就地修改：内部先 deepcopy，再交给约定引擎（它自己也深拷贝一层）。
    ★ 约定引擎抛异常时**不向上抛**：把原因放进 notes.errors，padstack 原样返回。
      这是改造前 build_footprint 里那个 `except Exception ... （原样继续）` 分支的同语义，
      由调用方决定怎么记日志（执行层仍打同一条 warning）。
    """
    plan: Dict[str, Any] = {
        "padstack": copy.deepcopy(reading.get("padstack")),
        "extra_padstacks": copy.deepcopy(list(reading.get("extra_padstacks") or [])),
        "pins_override": copy.deepcopy(list(reading.get("pins_override") or [])),
    }
    notes: Dict[str, List[str]] = {"conventions": [], "tht_layers": [], "errors": []}

    # ---- 1) 约定符合化（apply_conventions：读 knowledge/conventions.json）----
    conv_input: Dict[str, Any] = {
        "mount": reading.get("mount"),
        "padstack": plan["padstack"],
        "extra_padstacks": plan["extra_padstacks"],
        "pins_override": plan["pins_override"],
    }
    try:
        derived, conv_notes = apply_conventions(conv_input)
        plan["padstack"] = derived["padstack"]
        plan["extra_padstacks"] = derived.get("extra_padstacks") or plan["extra_padstacks"]
        plan["pins_override"] = derived.get("pins_override") or plan["pins_override"]
        notes["conventions"] = list(conv_notes or [])
    except Exception as exc:  # noqa: BLE001 —— 与改造前一致：约定失败不阻断建封装
        notes["errors"].append(str(exc))

    # ---- 2) 通孔层兜底（主 padstack + 每个附加 padstack）----
    fixed = ensure_tht_padstack_layers(plan["padstack"])
    for extra in plan["extra_padstacks"] or []:
        fixed += ensure_tht_padstack_layers(extra)
    notes["tht_layers"] = fixed

    return {
        "padstack": plan["padstack"],
        "extra_padstacks": plan["extra_padstacks"],
        "pins_override": plan["pins_override"],
        "notes": notes,
    }
