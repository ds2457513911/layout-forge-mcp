# -*- coding: utf-8 -*-
"""
shared/json_utils.py —— JSON 清洗与数值转换

用途：
  - 从 LLM / 视觉模型的自由文本输出中提取合法 JSON 对象
  - 递归把字符串型数字转成 float

说明：
  这些函数与 lib_moondream_engine 里的 _extract_json / _coerce_numeric 功能一致，
  但本模块是 MCP 层的公共依赖，不直接 import lib 内部的下划线函数。
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_json_body(raw: str) -> dict:
    """
    从自由文本中稳健提取 JSON 对象。

    处理以下常见情况：
      1. 输出被 ```json ... ``` 代码围栏包裹
      2. 输出前后有解释性文字
      3. 全角标点（，：；）导致 json.loads 失败
      4. 裸 NaN 不是合法 JSON

    :param raw: 模型原始输出字符串
    :return: 解析后的 dict
    :raises ValueError: 找不到 JSON 对象时
    :raises json.JSONDecodeError: JSON 语法错误时
    """
    text = (raw or "").strip()

    # 1) 剥离 ```json ... ``` 代码围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 2) 截取首个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"输出中找不到 JSON 对象: {raw[:200]!r}")

    body = text[start:end + 1]

    # 3) 容错：全角标点 -> 半角
    body = body.replace("，", ",").replace("：", ":").replace("；", ";")

    # 4) 容错：裸 NaN -> null
    body = re.sub(r"\bNaN\b", "null", body)

    return json.loads(body)


def coerce_numeric(obj: Any) -> Any:
    """
    递归把字符串型数字（如 "1.8"）转成 float。

    用途：视觉模型常把数值以字符串返回，后续公差比对做减法时会崩溃。

    :param obj: 任意 Python 对象（dict / list / str / 其他）
    :return: 同结构对象，字符串数字被替换为 float
    """
    if isinstance(obj, dict):
        return {k: coerce_numeric(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [coerce_numeric(x) for x in obj]
    if isinstance(obj, str):
        s = obj.strip()
        try:
            return float(s)
        except ValueError:
            return obj
    return obj
