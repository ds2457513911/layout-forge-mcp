# -*- coding: utf-8 -*-
"""
services/spec_library.py —— spec 库（**每颗料解析成果的沉淀处**）

★ 为什么要有这个模块
  建一颗封装的真正成本在「读规格书提参数」——这一步只能 AI 读图，无法穷举。
  以前解析完的 spec 是一次性消耗品（用完即弃，重建时手工重拼 JSON）。
  本模块把它落盘成 `knowledge/specs/<符号名>.json`，配套三个能力：

    save_spec()   —— 建封装时顺手存档
    load_spec()   —— ★ 同封装不同料号：**零读图**直接复用
    find_spec()   —— 按「几何指纹」找同族，只读差异部分

★ 目录（**唯一真源**在 MCP 项目内，随代码一起搬走）
```
layout-forge-mcp/
└── knowledge/specs/
    ├── _index.json            反查表：符号名 / 族 / 来源 PDF / 指纹 / 存档时间
    └── <符号名>.json          一颗料一份 spec
```

★ 指纹（判断「同封装 / 同族」的可计算判据，不靠人眼看长得像不像）
```
(mount, pin_count, pitch_x, pitch_y, body.x, body.y, pad.size)
```
  七项全等（容差内）  → `exact`：同封装，直接复用
  mount+pad+body 同、仅 pitch/脚数不同 → `family`：同族，只读差异
  其余                 → 不匹配
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from services import paths as P

logger = logging.getLogger("layout-forge")

#: ★ spec 库目录（`knowledge/specs/`），不存在时自动建
SPEC_DIR = os.path.join(P.PROJECT_ROOT, "knowledge", "specs")

#: 反查表文件名
INDEX_NAME = "_index.json"

#: 指纹比较的默认容差（mm）。规格书本身取到 0.01，0.05 足够宽容又不会误判
DEFAULT_TOL = 0.05

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ============================================================
# 1. 基础：目录 / 文件名 / 索引读写
# ============================================================
def ensure_spec_dir() -> str:
    """确保 `knowledge/specs/` 存在（幂等）。返回目录路径。"""
    os.makedirs(SPEC_DIR, exist_ok=True)
    return SPEC_DIR


def _slug(name: str) -> str:
    """把符号名规整成安全文件名（并统一小写，Allegro 落盘也是小写）。"""
    s = _SAFE_NAME_RE.sub("_", str(name or "").strip())
    return (s or "unnamed").lower()


def _path_for(name: str) -> str:
    return os.path.join(SPEC_DIR, f"{_slug(name)}.json")


def _read_index() -> Dict[str, Any]:
    p = os.path.join(SPEC_DIR, INDEX_NAME)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.warning("spec 索引读取失败 %s: %s", p, e)
        return {}


def _write_index(idx: Dict[str, Any]) -> None:
    ensure_spec_dir()
    p = os.path.join(SPEC_DIR, INDEX_NAME)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)


# ============================================================
# 2. 指纹
# ============================================================
def _num(v: Any) -> Optional[float]:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def fingerprint(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 spec 抽出「几何指纹」——判断同封装 / 同族的可计算判据。

    :return: {"mount", "pin_count", "pitch_x", "pitch_y",
              "body_x", "body_y", "pad_x", "pad_y"}
             取不到的字段为 None（比较时按「未知=不参与」处理）
    """
    spec = spec or {}
    arr = spec.get("array") or {}
    body = spec.get("body") or {}
    pad = spec.get("pad") or {}
    pad_size = pad.get("size") or [None, None]
    pins = spec.get("pins_override") or []
    pin_count = spec.get("pin_count")
    if not pin_count:
        pin_count = len(pins) or None
    # ★ pitch 为 0 = 「这个方向没有间距」（单排件的 pitch_y 常写 0）。
    #   归一成 None，好让「查询方没填」与「单排没有」互相匹配。
    px, py = _num(arr.get("pitch_x")), _num(arr.get("pitch_y"))
    return {
        "mount": spec.get("mount"),
        "pin_count": pin_count,
        "pitch_x": None if px == 0 else px,
        "pitch_y": None if py == 0 else py,
        "body_x": _num(body.get("x")),
        "body_y": _num(body.get("y")),
        "pad_x": _num(pad_size[0]) if len(pad_size) > 0 else None,
        "pad_y": _num(pad_size[1]) if len(pad_size) > 1 else None,
    }


_FP_KEYS = ("mount", "pin_count", "pitch_x", "pitch_y",
            "body_x", "body_y", "pad_x", "pad_y")

#: 判断 exact 时**必须**相等的项（其余项允许容差）
_FP_HARD = ("mount", "pin_count")
#: 判断 family 时**必须**相等的项（只差 pitch / 脚数也算同族）
_FP_FAMILY_HARD = ("mount", "pad_x", "pad_y")


def _cmp(a: Any, b: Any, tol: float) -> bool:
    """
    相等判定：数值用容差，其余用严格相等。

    ★ 语义：两边都"不适用"(None) 算**相等**；只有一边未知才算不匹配。
      （单排器件的 pitch_y=0 在 fingerprint 里已归一成 None；
       查询方不传 pitch_y 也是 None —— 两边都"没有 y 向间距"，应视为一致。）
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b


def _level(q: Dict[str, Any], t: Dict[str, Any], tol: float) -> Optional[str]:
    """返回 "exact" / "family" / None。"""
    hard_ok = all(_cmp(q.get(k), t.get(k), tol) for k in _FP_HARD)
    soft_ok = all(_cmp(q.get(k), t.get(k), tol)
                  for k in _FP_KEYS if k not in _FP_HARD)
    if hard_ok and soft_ok:
        return "exact"
    fam_ok = all(_cmp(q.get(k), t.get(k), tol) for k in _FP_FAMILY_HARD)
    body_ok = all(_cmp(q.get(k), t.get(k), tol) for k in ("body_x", "body_y"))
    if fam_ok and body_ok:
        return "family"
    return None


# ============================================================
# 3. 对外能力：save / load / find / list
# ============================================================
def save_spec(
    name: str,
    spec: Dict[str, Any],
    *,
    family: Optional[str] = None,
    source_pdf: Optional[str] = None,
    notes: Optional[str] = None,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """
    把一份 spec 存档到 `knowledge/specs/<name>.json`，并更新 `_index.json`。

    :param name: 符号名（建议用封装名，如 `NB_LMBR140ET1G_2P`）
    :param spec: 解析出的 spec 字典（会原样落盘）
    :param family: 封装族（如 `SOD-323HE` / `SOT-23` / `FPC-0.5`），可空
    :param source_pdf: 来源规格书路径，便于以后回查
    :param notes: ★ 方法论 / 踩过的坑（如「F 是中心距不是总长」）——**最值钱的部分**
    :param overwrite: 同名已存在时是否覆盖（默认 True；False 则报 collision）
    :return: {"ok": bool, "name", "file", "index_entry", "error"}
    """
    ensure_spec_dir()
    if not name or not isinstance(spec, dict):
        return {"ok": False, "error": "name 为空或 spec 不是 dict"}
    path = _path_for(name)
    if os.path.isfile(path) and not overwrite:
        return {"ok": False, "error": f"已存在且 overwrite=False: {path}",
                "file": path}

    payload: Dict[str, Any] = {
        "schema": "layout-forge-spec/1",
        "name": str(name),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "family": family or spec.get("family"),
        "source_pdf": source_pdf,
        "notes": notes,
        "fingerprint": fingerprint(spec),
        "spec": spec,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    idx = _read_index()
    key = _slug(name)
    idx[key] = {
        "name": str(name),
        "file": os.path.basename(path),
        "family": payload.get("family"),
        "source_pdf": source_pdf,
        "saved_at": payload["saved_at"],
        "fingerprint": payload["fingerprint"],
    }
    _write_index(idx)
    return {"ok": True, "name": str(name), "file": path,
            "index_entry": idx[key]}


def load_spec(name: str) -> Optional[Dict[str, Any]]:
    """
    按名取回 spec（**不含**外层元信息，直接是能喂 `solve()` 的 spec）。

    找不到时返回 None（大小写在 index 里做了规整，所以大小写不敏感）。
    """
    if not name:
        return None
    p = _path_for(name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("spec"), dict):
        return data["spec"]
    return None


def list_specs() -> List[Dict[str, Any]]:
    """列出库里所有 spec 的索引条目（按名字排序）。"""
    idx = _read_index()
    return [dict(v, key=k) for k, v in sorted(idx.items())]


def rebuild_index() -> Dict[str, Any]:
    """
    ★ 扫 `knowledge/specs/*.json` **重建** `_index.json`。

    什么时候需要：
      · 指纹规则改了（如 pitch=0 的归一化口径变化）
      · 手工编辑过某个 spec JSON
      · 索引文件被误删（spec 文件还在就能救回来）

    :return: {"ok": bool, "count": int, "names": [...], "skipped": [...]}
    """
    ensure_spec_dir()
    idx: Dict[str, Any] = {}
    names: List[str] = []
    skipped: List[str] = []
    for fn in sorted(os.listdir(SPEC_DIR)):
        if not fn.endswith(".json") or fn == INDEX_NAME:
            continue
        p = os.path.join(SPEC_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            skipped.append(f"{fn}: {e}")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("spec"), dict):
            skipped.append(f"{fn}: 不是 spec 文件（缺 spec 字段）")
            continue
        spec = data["spec"]
        fp = fingerprint(spec)
        data["fingerprint"] = fp
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(p + ".tmp", p)
        key = _slug(data.get("name") or fn[:-5])
        idx[key] = {
            "name": data.get("name") or fn[:-5],
            "file": fn,
            "family": data.get("family"),
            "source_pdf": data.get("source_pdf"),
            "saved_at": data.get("saved_at"),
            "fingerprint": fp,
        }
        names.append(idx[key]["name"])
    _write_index(idx)
    return {"ok": True, "count": len(idx), "names": names, "skipped": skipped}


def find_spec(
    *,
    mount: Optional[str] = None,
    pin_count: Optional[int] = None,
    pitch_x: Optional[float] = None,
    pitch_y: Optional[float] = None,
    body_x: Optional[float] = None,
    body_y: Optional[float] = None,
    pad_x: Optional[float] = None,
    pad_y: Optional[float] = None,
    tol: float = DEFAULT_TOL,
    keyword: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ★ 找「同封装 / 同族」的已有 spec。

    两种用法：
      1. 给几何字段 → 按指纹匹配（推荐，自动化）
      2. 给 `keyword` → 按名字 / 族 / 来源 PDF 子串找（人工导航用）

    :return: {"exact": [...], "family": [...], "keyword": [...], "query": {...}}
             每项是 {"name", "file", "family", "level", "score", "fingerprint"}
             score = 命中的指纹项数（8 项满分）
    """
    query = {
        "mount": mount, "pin_count": pin_count,
        "pitch_x": _num(pitch_x), "pitch_y": _num(pitch_y),
        "body_x": _num(body_x), "body_y": _num(body_y),
        "pad_x": _num(pad_x), "pad_y": _num(pad_y),
    }
    has_geom = any(query.get(k) is not None for k in _FP_KEYS)

    exact: List[Dict[str, Any]] = []
    family: List[Dict[str, Any]] = []
    for entry in list_specs():
        fp = entry.get("fingerprint") or {}
        score = sum(1 for k in _FP_KEYS
                    if _cmp(query.get(k), fp.get(k), tol))
        if has_geom:
            lv = _level(query, fp, tol)
            if lv == "exact":
                exact.append(dict(entry, level="exact", score=score))
            elif lv == "family":
                family.append(dict(entry, level="family", score=score))

    kw: List[Dict[str, Any]] = []
    if keyword:
        q = str(keyword).lower()
        for entry in list_specs():
            hay = " ".join(str(entry.get(f) or "")
                           for f in ("name", "family", "source_pdf", "key"))
            if q in hay.lower():
                kw.append(dict(entry, level="keyword", score=0))

    exact.sort(key=lambda e: -e.get("score", 0))
    family.sort(key=lambda e: -e.get("score", 0))
    return {"exact": exact, "family": family, "keyword": kw,
            "query": query, "tol": tol, "total": len(list_specs())}
