# -*- coding: utf-8 -*-
"""
services/report_renderer.py —— 把数值检查 + 语义检查渲染成固定 Markdown 报告

输入：
  numeric_result  : check_footprint_by_rules 的返回
  semantic_result : AI 填写的语义检查结果（naming / pin_number / polarity）
  report_filename : 可选，已保存的报告文件名（显示在计数行）

输出：
  Markdown 字符串（固定格式，AI 原样贴出，不得改写）

v2 变更：
  - 1.1 前缀：只显示前缀（如 nb_），不再显示完整封装名
  - 1.3 含数字：只显示 有/无，不再列具体数字
  - 2.1 尺寸一致性：显示 padstack 命名值 vs 实测值（来自 value dict）
  - 2.3 钢网等大：显示钢网尺寸 vs 焊盘尺寸（来自 value dict）
  - 命名语义行：pitch 未在名字编码时，显示"N 项一致（pitch 未含）"

设计原则：
  - 格式完全由 Python 控制，AI 零自由度 → 跨封装、跨轮次完全一致
  - 按大项分组：1 命名 / 2 焊盘 / 3 间距原点 / 4 pin number / 5 Place_Bound / 6 Assembly+Silk
  - 每行四列：大项 / 检查项 / 实测 / 要求 / 状态
  - 大项名只在组首行加粗显示，同组后续行留空，视觉上形成分块
  - "实测"列从 item.value 提取，因 value 形状不统一（str/num/dict/list），
    按 item.id 写分发映射函数 _format_actual
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


# JSON 报告格式版本（checklist_report_*.json）；3.1 起含 rule_set / coverage
REPORT_VERSION = "3.1"

# 覆盖率状态：一侧有规则不算「覆盖」，与 rule_engine._form_status 同口径
COVERAGE_STATUS_TEXT = {
    "covered": "✅ 判定+派生",
    "assert_only": "✓ 仅判定",
    "derive_only": "✓ 仅派生",
    "uncovered": "❌ 无规则",
}


# ============================================================
# 大项分组（"0" 用于数据提取失败的兜底场景）
# ============================================================
GROUP_TITLES = {
    "0": "数据提取",
    "1": "1 命名规范",
    "2": "2 焊盘尺寸",
    "3": "3 间距与原点",
    "4": "4 pin number",
    "5": "5 Place_Bound",
    "6": "6 Assembly / Silkscreen",
}

GROUP_ORDER = ["0", "1", "2", "3", "4", "5", "6"]

# 状态图标
STATUS_ICON = {
    "PASS": "✅",
    "WARN": "⚠️",
    "FAIL": "❌",
    "NA": "—",
}

# 结论图标（标题行用）
CONCLUSION_ICON = {
    "PASS": "✅ PASS",
    "FAIL": "❌ FAIL",
    "REVIEW_REQUIRED": "⚠️ REVIEW_REQUIRED",
}


# ============================================================
# 表格单元格转义
# ============================================================
def _esc(s: Any) -> str:
    """把值转成 Markdown 单元格安全的字符串（转义 |，换行变空格）"""
    if s is None:
        return "—"
    text = str(s)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text


# ============================================================
# 数字格式化：去掉多余尾零
# ============================================================
def _fmt_num(v: Any) -> str:
    """
    0.35 → "0.35"，0.4 → "0.4"，2.0 → "2"，None → "—"
    保证渲染出的实测值不会出现 0.35000000001 这种浮点尾巴。
    """
    if v is None:
        return "—"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


# ============================================================
# 辅助：从完整封装名提取前缀
# ============================================================
def _extract_prefix(name: Any) -> str:
    """
    从封装名里提取前缀（如 "nb_xtal4..." → "nb_"）。

    规则：取第一个下划线及之前的部分（含下划线）。
    若没有下划线，返回原字符串。
    """
    if not isinstance(name, str) or not name:
        return "—"
    if "_" in name:
        return name.split("_", 1)[0] + "_"
    return name


# ============================================================
# "实测"列：按 item.id 分发
# ============================================================
def _format_actual(item: dict) -> str:
    """
    从 item 的 value / status 中提取"实测"列文字。

    为什么要按 id 分发：rule_checker 里 value 的形状不统一
    （str / float / list / 各种 key 的 dict），通用兜底会输出难读的
    "{'单边x': 0.05, '单边y': 0.05}" 这类东西。每个 id 有明确的展示意图，
    在这里一对一映射，渲染结果才稳定、可读。
    """
    id_ = item.get("id", "")
    value = item.get("value")
    status = item.get("status")

    # ---------- 大项 1 ----------
    if id_ == "1.1":
        # 只显示前缀（如 nb_），不显示完整名字
        return _esc(_extract_prefix(value))
    if id_ == "1.2":
        return "有" if status == "FAIL" else "无"
    if id_ == "1.3":
        # 只显示有/无，不列具体数字
        return "无" if not value else "有"

    # ---------- 大项 2 ----------
    if id_ == "2.1":
        # 显示 padstack 命名值 vs 实测值
        if isinstance(value, dict):
            padstack = value.get("padstack")
            actual = value.get("actual")
            if padstack is not None and actual is not None:
                return f"padstack {_esc(padstack)} / 实测 {_esc(actual)}"
        # 兼容旧 value（纯字符串）：退化显示
        return "一致" if status == "PASS" else "不一致"
    if id_ == "2.2":
        if isinstance(value, dict):
            x = _fmt_num(value.get("单边x"))
            y = _fmt_num(value.get("单边y"))
            return f"x {x} / y {y}"
        return "—"
    if id_ == "2.3":
        # 显示钢网尺寸 vs 焊盘尺寸
        if isinstance(value, dict):
            pad = value.get("pad")
            paste = value.get("paste")
            if pad is not None and paste is not None:
                return f"钢网 {_esc(paste)} / 焊盘 {_esc(pad)}"
        # 兼容旧 value（"等大"/"不等大"）
        return _esc(value) if value else "—"

    # ---------- 大项 3 ----------
    if id_ == "3.1":
        if isinstance(value, dict):
            x = _fmt_num(value.get("pitch_x"))
            y = _fmt_num(value.get("pitch_y"))
            return f"x {x} / y {y}"
        return "—"
    if id_ in ("3.2x", "3.2y"):
        if isinstance(value, dict):
            e = _fmt_num(value.get("extracted"))
            d = _fmt_num(value.get("datasheet"))
            return f"{e} = {d}"
        return "—"
    if id_ == "3.3":
        if status == "FAIL":
            return "重叠"
        if value is not None:
            return f"最小间距 {_fmt_num(value)}"
        return "—"
    if id_ == "3.4":
        if isinstance(value, dict):
            c = value.get("center")
            if isinstance(c, list) and len(c) == 2:
                return f"[{_fmt_num(c[0])}, {_fmt_num(c[1])}]"
        return "—"

    # ---------- 大项 5 ----------
    if id_ == "5.1":
        return "有" if value else "无"
    if id_ == "5.2":
        name = item.get("name", "")
        # 5.2 有两个分支：覆盖检查（FAIL 时） / 外扩量（正常路径）
        if "覆盖" in name:
            return "覆盖" if status == "PASS" else "未覆盖"
        if isinstance(value, dict):
            x = _fmt_num(value.get("expand_x"))
            y = _fmt_num(value.get("expand_y"))
            return f"x {x} / y {y}"
        return "—"

    # ---------- 大项 6 ----------
    if id_ == "6.1":
        return f"{_fmt_num(value)} 元素" if value else "无"
    if id_ == "6.2":
        if isinstance(value, dict):
            n = value.get("n_elements")
            return f"{n} 元素" if n else "—"
        return "—"
    if id_ == "6.3":
        # Assembly 元素数（由 len(asm) 判定；1 脚标识存在性由 AI 语义检查负责）
        return _esc(value) if value else "—"
    if id_ == "6.4":
        return f"{_fmt_num(value)} 元素" if value else "无"
    if id_ == "6.5":
        # Silkscreen 复杂 path 数（由 n_segs>=3 的 path 数量判定）
        return _esc(value) if value else "—"
    if id_ == "6.6":
        if status == "FAIL":
            return "重叠"
        if value is not None:
            return f"最小间隙 {_fmt_num(value)}"
        return "—"

    # ---------- 兜底 ----------
    return _format_value_generic(value)


def _format_value_generic(value: Any) -> str:
    """兜底：任何未显式映射的 value 形状，按类型给个合理展示"""
    if value is None:
        return "—"
    if isinstance(value, str):
        return _esc(value)
    if isinstance(value, (int, float)):
        return _fmt_num(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return " / ".join(f"{k}={_fmt_num(v)}" for k, v in value.items())
    return _esc(value)


# ============================================================
# 第 4 大项：pin number（来自 AI 语义检查）
# ============================================================
def _pin_number_rows(semantic_result: Optional[dict]) -> list[dict]:
    """
    从 semantic_result.pin_number 生成两行：
      - pin 数量
      - pin 编号与位置
    缺字段时返回空列表（渲染器会跳过整个大项 4）。
    """
    pn = (semantic_result or {}).get("pin_number")
    if not isinstance(pn, dict):
        return []

    dra = pn.get("dra_pin_count")
    ds = pn.get("datasheet_pin_count")
    actual1 = f"{dra} = {ds}" if (dra is not None and ds is not None) else "—"
    status1 = "PASS" if pn.get("count_match") else "FAIL"

    status2 = "PASS" if pn.get("pin_numbers_match") else "FAIL"
    actual2 = "一一对应" if status2 == "PASS" else "不一致"

    return [
        {"name": "pin 数量", "actual": actual1, "expected": "一致", "status": status1},
        {"name": "pin 编号与位置", "actual": actual2, "expected": "一致", "status": status2},
    ]


# ============================================================
# 第 1 大项：命名语义（补充行）
# ============================================================
def _naming_semantic_row(semantic_result: Optional[dict]) -> Optional[dict]:
    """
    从 semantic_result.naming 生成一行"命名语义 vs datasheet"。
    未提供该字段时返回 None（不显示此行）。

    显示规则：
      - 4 项全检且全过 → "全部一致"
      - 只检了部分（如名字未含 pitch）→ "N 项一致（X、Y 未含）"
      - 有失败项 → "不一致：X、Y"
    """
    nm = (semantic_result or {}).get("naming")
    if not isinstance(nm, dict):
        return None

    checks = [
        ("类型", nm.get("type_match")),
        ("引脚数", nm.get("pin_count_match")),
        ("尺寸", nm.get("dimension_match")),
        ("pitch", nm.get("pitch_match")),
    ]
    # None 表示该维度不适用（名字未含 / datasheet 未提供），不参与判定
    bad = [name for name, ok in checks if ok is False]
    checked = [name for name, ok in checks if ok is not None]
    if not checked:
        return None

    if not bad:
        if len(checked) == len(checks):
            actual = "全部一致"
        else:
            missing = [name for name, ok in checks if ok is None]
            actual = f"{len(checked)} 项一致（{'、'.join(missing)} 未含）"
        status = "PASS"
    else:
        actual = "不一致：" + "、".join(bad)
        status = "FAIL"

    return {
        "name": "命名语义（类型/引脚数/尺寸/pitch）",
        "actual": actual,
        "expected": "一致",
        "status": status,
    }


# ============================================================
# 第 6 大项：极性标识（补充行，仅 required=true 时显示）
# ============================================================
def _polarity_row(semantic_result: Optional[dict]) -> Optional[dict]:
    """
    从 semantic_result.polarity 生成一行"极性标识"。
    非极性元件（required=false）不显示——避免给工程师制造无意义的行。
    """
    p = (semantic_result or {}).get("polarity")
    if not isinstance(p, dict) or not p.get("required"):
        return None

    asm = p.get("pin1_marker_assembly")
    silk = p.get("pin1_marker_silkscreen")

    parts = []
    if asm is not None:
        parts.append(f"Assembly {'有' if asm else '无'}")
    if silk is not None:
        parts.append(f"Silkscreen {'有' if silk else '无'}")
    actual = " / ".join(parts) if parts else "—"

    ok = (asm is not False) and (silk is not False)
    return {
        "name": "极性标识（Assembly + Silkscreen）",
        "actual": actual,
        "expected": "两层都有",
        "status": "PASS" if ok else "FAIL",
    }


# ============================================================
# 大项分组
# ============================================================
def _group_items(items: list[dict]) -> dict[str, list[dict]]:
    """把 rule_checker 返回的 items 按 id 前缀（0./1./2./3./5./6.）归组"""
    groups: dict[str, list[dict]] = {k: [] for k in GROUP_ORDER}
    for it in items or []:
        id_ = str(it.get("id", ""))
        if not id_:
            continue
        prefix = id_.split(".")[0]
        if prefix in groups:
            groups[prefix].append(it)
    return groups




# ============================================================
# 规则集与覆盖率（票 #21；ADR-0002 D9 / D11）
# ============================================================
def rule_set_line(numeric_result: dict) -> str:
    """首屏的规则集标识行；缺 rule_set 返回空串（历史/错误分支安全）。"""
    info = numeric_result.get("rule_set")
    if not isinstance(info, dict) or not info.get("hash"):
        return ""
    counts = info.get("counts") or {}
    version = info.get("version") or "?"
    short = str(info.get("hash"))[:12]
    return (f"规则集 v{version} · hash {short}…"
            f"（form {counts.get('forms', 0)} / 参数 {counts.get('parameters', 0)}"
            f" / 规则 {counts.get('rules', 0)}）")


def coverage_lines(cov: Any) -> list:
    """覆盖率块（合计 + 形态表 + 两类缺口）。缺 coverage 返回空列表。"""
    if not isinstance(cov, dict):
        return []
    total = cov.get("total") or {}
    forms = cov.get("forms") or []
    gaps = cov.get("gaps") or {}
    uncovered_hit = sum(1 for rec in forms
                        if isinstance(rec, dict) and rec.get("hits") and not rec.get("assert_rules"))
    headline = (f"覆盖率：本次输入 {total.get('subjects', 0)} 个焊盘 · "
                f"命中形态 {total.get('matched', 0)}")
    if uncovered_hit:
        headline += f"（其中 {uncovered_hit} 个命中但无规则）"
    out = [
        headline + f" · 无形态 {total.get('unmatched', 0)}",
        "| 形态 | 形态名 | 本次命中 | 支撑（焊盘） | 判定规则（assert） | 派生规则（derive） | 覆盖 |",
        "|---|---|---|---|---|---|---|",
    ]
    for rec in forms:
        if not isinstance(rec, dict):
            continue
        status = rec.get("status")
        out.append(
            f"| {_esc(rec.get('form') or '')} | {_esc(rec.get('name') or '')} "
            f"| {rec.get('hits', 0)} "
            f"| {str(rec.get('support')) + ' 焊盘' if rec.get('support') is not None else '—'} "
            f"| {_esc('、'.join(rec.get('assert_rules') or []) or '—')} "
            f"| {_esc('、'.join(rec.get('derive_rules') or []) or '—')} "
            f"| {COVERAGE_STATUS_TEXT.get(status, status or '—')} |"
        )

    names = {rec.get("form"): rec.get("name") or "" for rec in forms if isinstance(rec, dict)}
    no_rule = gaps.get("forms_without_rule") or []
    no_form = gaps.get("inputs_without_form") or []

    def _named(fid):
        name = names.get(fid) or ""
        return f"{fid}（{name}）" if name else str(fid)

    out.append("缺口·有形态但无规则：" + ("、".join(_named(f) for f in no_rule) if no_rule else "无"))
    if no_form:
        detail = "；".join(
            f"{rec.get('signature')} × {rec.get('count')}（{rec.get('reason')}"
            + (f"；样例 {'、'.join(rec.get('subjects') or [])}" if rec.get("subjects") else "")
            + "）"
            for rec in no_form if isinstance(rec, dict)
        )
        out.append("缺口·无形态的输入：" + detail)
    else:
        out.append("缺口·无形态的输入：无")
    return out


# ============================================================
# JSON 报告（checklist_report_*.json）的唯一定义处
# ============================================================
def build_checklist_report(result: dict, generated_at: Optional[str] = None) -> dict:
    """把 check_footprint_by_rules 的结果整理成 JSON 报告。

    两个生产方（MCP 工具与 Tools/cleanup_redundant.py）都调这里，
    保证「报告字段」只有一处定义；新增字段不会在两条路径上漂移。
    """
    result = result if isinstance(result, dict) else {}
    fp = result.get("footprint_data") or {}
    return {
        "report_version": REPORT_VERSION,
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "dra_file": fp.get("source_file"),
        "symbol_name": fp.get("symbol_name"),
        "component_type": result.get("component_type"),
        "conclusion": result.get("conclusion"),
        "summary": result.get("summary"),
        "rule_set": result.get("rule_set"),
        "coverage": result.get("coverage"),
        "items": result.get("items"),
        "failed": result.get("failed"),
        "warned": result.get("warned"),
    }

# ============================================================
# 渲染主函数
# ============================================================
def render_markdown(
    numeric_result: dict,
    semantic_result: Optional[dict] = None,
    report_filename: Optional[str] = None,
) -> str:
    """
    渲染 Markdown 报告。

    :param numeric_result: check_footprint_by_rules 的返回
    :param semantic_result: AI 填的语义结果（naming / pin_number / polarity）
    :param report_filename: 可选，已保存的报告文件名
    :return: Markdown 字符串
    """
    if not isinstance(numeric_result, dict):
        raise ValueError("numeric_result 必须是 dict")

    fp = numeric_result.get("footprint_data") or {}
    symbol_name = fp.get("symbol_name") or "unknown"
    conclusion = numeric_result.get("conclusion") or "UNKNOWN"

    summary = numeric_result.get("summary") or {}
    n_total = summary.get("total", 0)
    n_pass = summary.get("pass", 0)
    n_fail = summary.get("fail", 0)
    n_warn = summary.get("warn", 0)
    n_na = summary.get("na", 0)

    # ---------- 标题行 ----------
    c_icon = CONCLUSION_ICON.get(conclusion, conclusion)
    lines = [f"## {symbol_name} — {c_icon}", ""]

    # ---------- 计数行：只显示非零项，避免 0❌ 这种噪音 ----------
    parts = [f"{n_pass}✅"]
    if n_fail:
        parts.append(f"{n_fail}❌")
    if n_warn:
        parts.append(f"{n_warn}⚠️")
    if n_na:
        parts.append(f"{n_na}—")
    count_str = f"数值 {n_total}（{' '.join(parts)}，单位 mm）"
    if report_filename:
        count_str += f"｜报告 {report_filename}"
    lines.append(count_str)
    lines.append("")

    # ---------- 规则集与覆盖率（首屏；旧调用方缺这两个字段时整块跳过） ----------
    rule_line = rule_set_line(numeric_result)
    if rule_line:
        lines.append(rule_line)
    lines.extend(coverage_lines(numeric_result.get("coverage")))
    if rule_line or numeric_result.get("coverage"):
        lines.append("")

    # ---------- 表格 ----------
    lines.append("| 大项 | 检查项 | 实测 | 要求 | 状态 |")
    lines.append("|---|---|---|---|---|")

    groups = _group_items(numeric_result.get("items") or [])
    naming_row = _naming_semantic_row(semantic_result)
    polarity_row = _polarity_row(semantic_result)

    for gid in GROUP_ORDER:
        title = GROUP_TITLES[gid]

        # 大项 4：完全来自语义结果
        if gid == "4":
            pn_rows = _pin_number_rows(semantic_result)
            if not pn_rows:
                continue
            first = True
            for row in pn_rows:
                group_cell = f"**{title}**" if first else ""
                first = False
                status_icon = STATUS_ICON.get(row["status"], row["status"])
                lines.append(
                    f"| {group_cell} | {_esc(row['name'])} | {_esc(row['actual'])} "
                    f"| {_esc(row['expected'])} | {status_icon} |"
                )
            continue

        # 其他大项：来自 numeric items + 语义补充行
        group_items = groups.get(gid) or []
        extra_rows = []
        if gid == "1" and naming_row:
            extra_rows.append(naming_row)
        if gid == "6" and polarity_row:
            extra_rows.append(polarity_row)

        if not group_items and not extra_rows:
            continue

        first = True
        for it in group_items:
            group_cell = f"**{title}**" if first else ""
            first = False
            name = it.get("name", "")
            # 方向标签：只有 assert 侧的规则才标（票 #18 / ADR-0002 D2）。
            # derive-only 的规则不标，避免报告里出现无意义的方向噪音。
            if it.get("direction") == "assert":
                name = f"{name}（方向：assert）"
            actual = _format_actual(it)
            expected = it.get("expected") or "—"
            status = it.get("status", "")
            status_icon = STATUS_ICON.get(status, status)
            lines.append(
                f"| {group_cell} | {_esc(name)} | {actual} "
                f"| {_esc(expected)} | {status_icon} |"
            )
        for row in extra_rows:
            group_cell = f"**{title}**" if first else ""
            first = False
            status_icon = STATUS_ICON.get(row["status"], row["status"])
            lines.append(
                f"| {group_cell} | {_esc(row['name'])} | {_esc(row['actual'])} "
                f"| {_esc(row['expected'])} | {status_icon} |"
            )

    skip_notes = [
        (it.get("name") or "", " ".join(str(it.get("detail") or "").split()))
        for it in (numeric_result.get("items") or [])
        if it.get("status") == "NA" and str(it.get("detail") or "").strip()
    ]
    if skip_notes:
        lines.append("")
        lines.append("> 未判定（SKIP，不判 PASS）：")
        for name, detail in skip_notes[:10]:
            lines.append(f"> - {_esc(name)}：{_esc(detail)}")

    lines.append("")
    lines.append("> 理论值由 AI 读图获取，建议对照 PDF 原图复核。")

    return "\n".join(lines)