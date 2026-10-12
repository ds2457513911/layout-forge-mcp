# -*- coding: utf-8 -*-
"""
services/case_library.py —— 检测案例库（**每次人工裁定的沉淀处**）

★ 为什么要有这个模块
  规则检查的数值部分由 rule_checker 判，但「这条 WARN/FAIL 是真问题还是误报」
  只能人裁决。以前裁定结论用完即弃——同族件下次再踩同一个坑，工程师还要再吵一轮。
  本模块把「案例 = 指纹 + 检查摘要 + 人工裁定 + 教训」落盘成
  `knowledge/cases/<符号名>__<标签>.json`，配套：

    save_case()   —— 检查完成、人工裁定后顺手存档
    find_case()   —— ★ 检查**前**先查：同封装 / 同族件有哪些已知坑与裁定
    list_cases()  —— 浏览全库

★ 与 spec 库（spec_library.py）的分工
  spec 库沉淀「参数」（喂 solve 复用读图成果）；
  案例库沉淀「经验」（喂 AI 解读报告 / 预警）。检索共用同一套几何指纹——
  同族件自动召回彼此的案例，裁定一次、全族受益。

★ 指纹与匹配级别（复用 spec_library 的实现，保证两库口径一致）
  exact  —— 同封装（七项全等）
  family —— 同族（主体+焊盘同、脚数/pitch 可差）
  keyword —— 名字/族/来源 PDF 子串搜（人工导航用）
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from services import paths as P
from services.spec_library import _cmp, _level, _slug, fingerprint

logger = logging.getLogger("layout-forge")

#: 案例库目录（`knowledge/cases/`），不存在时自动建
CASE_DIR = os.path.join(P.PROJECT_ROOT, "knowledge", "cases")

#: 反查表文件名
INDEX_NAME = "_index.json"

#: 案例文件名分隔符：<符号名>__<标签>.json
TAG_SEP = "__"


def ensure_case_dir() -> str:
    os.makedirs(CASE_DIR, exist_ok=True)
    return CASE_DIR


def _path_for(name: str, tag: Optional[str]) -> str:
    base = _slug(name)
    if tag:
        base = f"{base}{TAG_SEP}{_slug(tag)}"
    return os.path.join(CASE_DIR, f"{base}.json")


def _read_index() -> Dict[str, Any]:
    p = os.path.join(CASE_DIR, INDEX_NAME)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.warning("案例索引读取失败 %s: %s", p, e)
        return {}


def _write_index(idx: Dict[str, Any]) -> None:
    ensure_case_dir()
    p = os.path.join(CASE_DIR, INDEX_NAME)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)


# ============================================================
# 对外能力：save / find / list / load
# ============================================================
def save_case(
    name: str,
    *,
    tag: Optional[str] = None,
    family: Optional[str] = None,
    source_pdf: Optional[str] = None,
    spec: Optional[Dict[str, Any]] = None,
    fingerprint_override: Optional[Dict[str, Any]] = None,
    check_summary: Optional[Dict[str, Any]] = None,
    report_path: Optional[str] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    lessons: Optional[List[str]] = None,
    verdict: Optional[str] = None,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """
    存一条检测案例到 `knowledge/cases/`，并更新 `_index.json`。

    :param name: 封装名（如 `NB_WSON8_8P`）
    :param tag: 可选标签，区分同一封装的多条案例（如 `散热盘pitch裁定`）
    :param spec: 该件的 spec（有则自动算指纹）；也可直接给 fingerprint_override
    :param check_summary: 检查报告 summary（total/pass/fail/warn/na）
    :param report_path: 报告 JSON 路径，便于回查全文
    :param findings: ★ 人工裁定列表，每条
                     {"rule_id": "3.2y", "issue": "0.635 vs 1.27",
                      "verdict": "false_positive|true_positive|needs_human",
                      "reason": "中心散热盘污染 min-gap"}
    :param lessons: ★ 教训 / 预警（最值钱），如
                    "中心有散热盘时 pitch 判据必须剔除散热盘再算"
    :param verdict: 整案结论 "pass" / "pass_with_notes" / "fail"
    :param overwrite: 同名案例已存在时是否覆盖（默认 True）
    :return: {"ok": bool, "name", "file", "error"}
    """
    ensure_case_dir()
    if not name:
        return {"ok": False, "error": "name 为空"}
    if not any([spec, fingerprint_override, check_summary, findings, lessons]):
        return {"ok": False, "error": "案例内容全空：至少给 spec / check_summary /"
                                      " findings / lessons 之一"}

    fp = fingerprint_override or (fingerprint(spec) if spec else {})
    path = _path_for(name, tag)
    if os.path.isfile(path) and not overwrite:
        return {"ok": False, "error": f"已存在且 overwrite=False: {path}",
                "file": path}

    payload: Dict[str, Any] = {
        "schema": "layout-forge-case/1",
        "name": str(name),
        "tag": tag,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "family": family,
        "source_pdf": source_pdf,
        "fingerprint": fp,
        "check_summary": check_summary,
        "report_path": report_path,
        "findings": findings or [],
        "lessons": lessons or [],
        "verdict": verdict,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    idx = _read_index()
    key = os.path.basename(path)[:-5]
    idx[key] = {
        "name": str(name),
        "tag": tag,
        "file": os.path.basename(path),
        "family": family,
        "source_pdf": source_pdf,
        "saved_at": payload["saved_at"],
        "fingerprint": fp,
        "verdict": verdict,
        "n_lessons": len(payload["lessons"]),
    }
    _write_index(idx)
    return {"ok": True, "name": str(name), "file": path}


def load_case(name: str, tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """按名（+可选标签）取回案例全文；找不到返回 None。"""
    p = _path_for(name, tag)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("案例读取失败 %s: %s", p, e)
        return None


def list_cases() -> List[Dict[str, Any]]:
    """列出全库案例（索引摘要）。"""
    idx = _read_index()
    return sorted(idx.values(), key=lambda e: e.get("saved_at", ""))


def find_case(
    *,
    spec: Optional[Dict[str, Any]] = None,
    fingerprint_query: Optional[Dict[str, Any]] = None,
    keyword: Optional[str] = None,
    tol: float = 0.05,
    include_payload: bool = False,
) -> Dict[str, Any]:
    """
    查库里有没有「同封装 / 同族」的案例 —— 检查**前**调用做预警。

    :param spec: 该件的 spec（自动算指纹）
    :param fingerprint_query: 已算好的指纹（与 spec 二选一或都不给）
    :param keyword: 名字/族/来源 PDF 子串搜（人工导航用）
    :param include_payload: True 时返回案例全文（否则只给索引摘要）
    :return: {"exact": [...], "family": [...], "keyword": [...],
              "query": {...}, "total": int}
    """
    q = fingerprint_query or (fingerprint(spec) if spec else {})
    exact: List[Dict[str, Any]] = []
    family: List[Dict[str, Any]] = []
    kw_hits: List[Dict[str, Any]] = []

    kw = (keyword or "").lower()
    for entry in list_cases():
        fp = entry.get("fingerprint") or {}
        level = _level(q, fp, tol) if q else None
        payload: Dict[str, Any] = entry
        if include_payload:
            loaded = load_case(entry.get("name", ""), entry.get("tag"))
            if loaded:
                payload = {**entry, "case": loaded}
        if level == "exact":
            exact.append(payload)
        elif level == "family":
            family.append(payload)
        elif kw and (
            kw in str(entry.get("name", "")).lower()
            or kw in str(entry.get("family") or "").lower()
            or kw in str(entry.get("source_pdf") or "").lower()
        ):
            kw_hits.append(payload)

    return {"exact": exact, "family": family, "keyword": kw_hits,
            "query": q, "total": len(exact) + len(family) + len(kw_hits)}
