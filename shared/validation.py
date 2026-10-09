# -*- coding: utf-8 -*-
"""
shared/validation.py —— Land pattern JSON schema 校验

输入：任意 dict（来自 AI 读图提取，或外部系统）
输出：(is_valid, errors, normalized)

normalized 结构可直接喂给 lib_tolerance_check.check_dimension_tolerance。
"""
from __future__ import annotations

from typing import Any

# 合理的焊盘尺寸范围（mm）
MIN_DIM_MM = 0.001
MAX_DIM_MM = 100.0

# 合理的中心间距范围（mm）
MIN_SPACING_MM = 0.001
MAX_SPACING_MM = 1000.0


def validate_land_pattern(data: Any) -> tuple[bool, list[str], dict]:
    """
    校验并规范化 land pattern 结构。

    校验项：
      - 顶层必须是 dict
      - unit 必须是 mm 或 mil（缺省按 mm）
      - pads 必须是列表，每项必须能转出 pin(int) / width(float) / height(float)
      - width / height 必须在 [0.001, 100] mm 范围内
      - spacing_x / spacing_y 必须是数字或 null，范围 [0.001, 1000] mm
      - note 截断到 500 字符

    :param data: 待校验对象
    :return: (是否合法, 错误列表, 规范化后的 dict)
    """
    errors: list[str] = []
    normalized: dict = {
        "unit": "mm",
        "pads": [],
        "spacing_x": None,
        "spacing_y": None,
        "note": "",
    }

    if not isinstance(data, dict):
        return False, ["顶层不是 dict"], normalized

    # ---------- unit ----------
    unit = str(data.get("unit", "mm")).strip().lower()
    if unit not in ("mm", "mil"):
        errors.append(f"unit 非法: {unit!r}（应为 mm 或 mil）")
        unit = "mm"
    normalized["unit"] = unit

    # ---------- pads ----------
    pads = data.get("pads")
    if not isinstance(pads, list):
        errors.append("pads 不是列表")
    else:
        for i, pad in enumerate(pads):
            if not isinstance(pad, dict):
                errors.append(f"pads[{i}] 不是 dict")
                continue

            try:
                pin = pad.get("pin")
                pin_int = int(pin) if pin is not None else i + 1
                w = float(pad.get("width"))
                h = float(pad.get("height"))
            except (TypeError, ValueError):
                errors.append(f"pads[{i}] pin/width/height 无法转换")
                continue

            if not (MIN_DIM_MM <= w <= MAX_DIM_MM):
                errors.append(f"pads[{i}] width 超出合理范围: {w}")
            if not (MIN_DIM_MM <= h <= MAX_DIM_MM):
                errors.append(f"pads[{i}] height 超出合理范围: {h}")

            normalized["pads"].append({
                "pin": pin_int,
                "width": round(w, 4),
                "height": round(h, 4),
            })

    # ---------- spacing ----------
    for key in ("spacing_x", "spacing_y"):
        v = data.get(key)
        if v is None:
            normalized[key] = None
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            errors.append(f"{key} 无法转换为数字: {v!r}")
            continue
        if not (MIN_SPACING_MM <= fv <= MAX_SPACING_MM):
            errors.append(f"{key} 超出合理范围: {fv}")
        else:
            normalized[key] = round(fv, 4)

    # ---------- note ----------
    normalized["note"] = str(data.get("note") or "")[:500]

    is_valid = (len(errors) == 0 and len(normalized["pads"]) > 0)
    return is_valid, errors, normalized
