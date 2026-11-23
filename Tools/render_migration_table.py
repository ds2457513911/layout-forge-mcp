# -*- coding: utf-8 -*-
"""把迁移映射表（knowledge/migrations/rules_v1.json）渲染成人读文档。

为什么要有渲染器而不是直接手写 Markdown：
  - 映射表是「每个旧键、每条旧规则的唯一归属」的台账，手抄两份必然漂移；
  - R6.13 判据用本渲染器的输出与 docs 里的文档做逐字节比对，
    谁改了 JSON 忘了重渲染，闸门直接红。

用法：
  python Tools/render_migration_table.py            # 重写 docs/迁移映射表-规则集v1.md
  python Tools/render_migration_table.py --check    # 只比对，不一致退出码 1
  python Tools/render_migration_table.py --out X.md # 渲染到别处（测试用）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "knowledge" / "migrations" / "rules_v1.json"
DOC = ROOT / "docs" / "迁移映射表-规则集v1.md"

STATUS_LABEL = {"migrated": "已迁移", "legacy": "保留 legacy"}


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _fmt_target(target: Dict[str, Any]) -> str:
    kind = target.get("type")
    oid = target.get("id")
    path = target.get("path")
    if kind == "parameter":
        return f"参数 {oid} → {path}"
    if kind == "rule":
        return f"规则 {oid}"
    return f"保留在 {path}"


def _table(header: List[str], rows: List[List[str]]) -> List[str]:
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return out


def render(doc: Dict[str, Any]) -> str:
    entries = doc.get("entries") or []
    keys = [e for e in entries if e.get("kind") == "threshold_key"]
    rules = [e for e in entries if e.get("kind") == "rule"]
    migrated = [e for e in entries if e.get("status") == "migrated"]
    legacy = [e for e in entries if e.get("status") != "migrated"]
    src = doc.get("sources") or {}
    th = src.get("thresholds") or {}
    recipe = doc.get("rollback_recipe") or {}

    out: List[str] = []
    out.append("# 规则集 v1 迁移映射表")
    out.append("")
    out.append(f"票 {doc.get('ticket')}，映射表 {doc.get('id')}（schema {doc.get('schema')}），"
               f"生成日期 {doc.get('created_at')}。")
    out.append("")
    out.append(doc.get("about") or "")
    out.append("")
    out.append("**这份文档是生成物，不要手改。** 台账在 "
               "@@BT@@knowledge/migrations/rules_v1.json@@BT@@，改完跑 "
               "@@BT@@python Tools/render_migration_table.py@@BT@@ 重渲染；"
               "@@BT@@eval/suites/geometry_regression/check_rule_engine.py@@BT@@ 的 R6.13 会逐字节比对"
               "两份是否一致，并核对旧阈值文件的每个叶子键、旧检查器的每条规则是否都有且只有一个归属。")
    out.append("")
    out.append("## 盘点口径")
    out.append("")
    out.append(f"- 旧阈值文件：@@BT@@{th.get('file')}@@BT@@（冻结只读，sha256 @@BT@@{th.get('sha256')}@@BT@@），"
               f"共 {len(keys)} 个叶子键。")
    rule_ids = (src.get("legacy_rules") or {}).get("rule_ids") or []
    out.append(f"- 旧检查器规则：@@BT@@{(src.get('legacy_rules') or {}).get('file')}@@BT@@，"
               f"共 {len(rules)} 条规则 id（{' / '.join(rule_ids)}）。")
    out.append(f"- 合计 {len(entries)} 条：已迁移 {len(migrated)} 条，保留 legacy {len(legacy)} 条。")
    out.append("")
    out.append("## 一、旧阈值文件的每个键")
    out.append("")
    rows = []
    for e in keys:
        rows.append([f"@@BT@@{str(e.get('legacy', '')).split('#', 1)[-1]}@@BT@@",
                     _fmt_value(e.get("legacy_value")),
                     _fmt_target(e.get("target") or {}),
                     STATUS_LABEL.get(e.get("status"), str(e.get("status"))),
                     e.get("note") or ""])
    out.extend(_table(["旧键", "旧值", "归属（唯一）", "状态", "说明"], rows))
    out.append("")
    out.append("## 二、旧代码里的每条规则")
    out.append("")
    rows = []
    for e in rules:
        rows.append([f"@@BT@@{str(e.get('legacy', '')).split('#', 1)[-1]}@@BT@@",
                     e.get("legacy_value") or "",
                     _fmt_target(e.get("target") or {}),
                     STATUS_LABEL.get(e.get("status"), str(e.get("status"))),
                     e.get("note") or ""])
    out.extend(_table(["旧规则", "名称", "归属（唯一）", "状态", "说明"], rows))
    out.append("")
    out.append("legacy 行的保留原因与回滚方式逐条记在台账 JSON 的 @@BT@@reason@@BT@@ / @@BT@@rollback@@BT@@ 字段；"
               "共同点是：旧检查器仍读冻结的阈值文件，收编按 ADR-0002 D9 分阶段（#20 派生侧 / #21 覆盖率与其余规则）。")
    out.append("")
    out.append("## 三、回滚配方（可按步骤执行）")
    out.append("")
    out.append(f"配方 @@BT@@{recipe.get('id')}@@BT@@ 适用于 {', '.join(recipe.get('applies_to') or [])}。")
    out.append("")
    for i, step in enumerate(recipe.get("steps") or [], 1):
        if step.get("op") == "expand_parameter":
            targets = "、".join(t.get("id") for t in step.get("to") or [])
            out.append(f"{i}. @@BT@@expand_parameter@@BT@@：把 @@BT@@{step.get('from')}@@BT@@ 展开成三个扁平单形态参数 "
                       f"{targets}（每组的 form/value/support/source + 参数级 field/keys/allowed/eps/unit）。")
        elif step.get("op") == "rewrite_rule":
            out.append(f"{i}. @@BT@@rewrite_rule@@BT@@：把 @@BT@@{step.get('rule')}@@BT@@ 的 @@BT@@use@@BT@@ 改回 "
                       f"{'、'.join(step.get('use') or [])}。")
        else:
            out.append(f"{i}. @@BT@@{step.get('op')}@@BT@@：{json.dumps(step, ensure_ascii=False)}")
    out.append("")
    out.append(f"验收口径：{recipe.get('expect') or ''}")
    out.append("")
    out.append(f"注意：{recipe.get('note') or ''}")
    out.append("")
    out.append("## 四、下次迁移怎么复用")
    out.append("")
    out.append("1. **先登记再动手**：在台账 @@BT@@entries@@BT@@ 里给要迁的旧键/旧规则写好 @@BT@@target@@BT@@（指向真实存在的"
               "参数或规则 id），@@BT@@status@@BT@@ 先留 @@BT@@legacy@@BT@@。")
    out.append("2. **迁移后改行**：真正接上以后把该行 @@BT@@status@@BT@@ 改成 @@BT@@migrated@@BT@@，@@BT@@rollback@@BT@@ 换成可执行配方"
               "（没有配方就只能写「未动」，R6.13 会对 @@BT@@migrated@@BT@@ 行要求非空 @@BT@@rollback@@BT@@）。")
    out.append("3. **重渲染**：@@BT@@python Tools/render_migration_table.py@@BT@@，再跑 R6.13 闸门；"
               "旧键集合与旧规则 id 集合必须与源码里实际存在的完全相等——多一条漏一条都红。")
    out.append("4. **回滚演练**：@@BT@@R6.14@@BT@@ 会照 @@BT@@rollback_recipe@@BT@@ 把库展开回旧形态，用同一批夹具重判，"
               "结果必须与合并后逐件一致（@@BT@@eval/baselines/rule_engine.json@@BT@@ 与 "
               "@@BT@@eval/baselines/shadow_2_2_253.json@@BT@@）。")
    out.append("5. **派生侧**：映射表只管数据；若迁移同时改了派生取值来源（如本次 convention_engine），"
               "回滚=连代码一起 revert，并在台账 @@BT@@rollback_recipe.note@@BT@@ 里写明。")
    out.append("")
    return "\n".join(out)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染规则集迁移映射表")
    ap.add_argument("--check", action="store_true", help="只比对，不一致退出码 1")
    ap.add_argument("--out", default=None, help="渲染到指定路径（默认 docs/迁移映射表-规则集v1.md）")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    data = json.loads(MIGRATION.read_text(encoding="utf-8"))
    text = render(data).replace("@@BT@@", "\x60")
    target = Path(args.out) if args.out else DOC
    payload = text.encode("utf-8")
    if args.check:
        current = target.read_bytes() if target.exists() else b""
        if current == payload:
            print(f"[ OK ] {target.name} 与台账一致（{len(payload)} 字节）")
            return 0
        print(f"[FAIL] {target.name} 与台账不一致，请重渲染：python Tools/render_migration_table.py")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)
    print(f"[ OK ] 已写 {target}（{len(payload)} 字节，{len(data.get('entries') or [])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
