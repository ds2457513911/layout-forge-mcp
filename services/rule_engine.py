# -*- coding: utf-8 -*-
"""规则集加载器与判定引擎（票 #17，设计见 docs/adr/0002-建封装知识收成规则集.md）

唯一新缝（ADR D9）分三段：

  1. 加载 —— load_library(dir)：把 knowledge/rules 下的四类对象
     （form / parameter / rule / source）整体读入，逐项校验；任何一处不合法
     即抛 RuleLibraryError（errors 逐条带 file / path / message），**绝不半载**。
     整库算一个 sha256（lib.fingerprint），作为报告里的 rule_set_hash。

  2. 判定 —— judge(lib, rule_id, subject, observed)：纯函数。按 subject 的
     事实选形态（form），取该形态下的参数（parameter），跑规则断言
     （assert），返回 finding：status / rule_id / form / 出处 / 支持件数 / 哈希。
     三态：满足（PASS）/ 违反（rule 的 severity）/ SKIP（写明缺什么）。
     ★ SKIP 不是通过：缺 form / 缺参数 / 观测 UNKNOWN 都要在 detail 里点名。

  3. 派生 —— 留给票 #18 / #20（本文件暂不实现，只有 assert 的规则在派生模式
     应报 SKIP「该形态缺派生规则」，见 ADR D2）。

本模块分两层：加载器有 I/O 与缓存（只读 knowledge/rules），判定/匹配是纯函数。
  - 只用标准库（json / hashlib / re / ast / os / pathlib），不碰宿主、网络、进程；
  - 不读 knowledge/rules 以外的路径，不写盘；
  - 同输入两次调用输出逐字节相同（canonical_json 排序键 + 固定分隔符）。

规则库文件（knowledge/rules/）：
  forms.json      形态定义：id + 谓词组合（白名单谓词才能用）
  parameters.json 参数：id + form + value/allowed/eps/support/source
  rules.json      规则：id + use（参数 id）+ assert + severity + source
  sources.json    出处：id + kind + ref + note
  _severity.json  severity 映射表：id + severity + consequence + evidence
  checker_rules.json ★ 冻结只读的旧阈值文件，本模块不读、不许改
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "layout-forge/rules@1"

# 库目录：环境变量优先（测试用），否则项目根下 knowledge/rules
RULE_DIR_ENV = "LAYOUT_FORGE_RULES_DIR"
DEFAULT_RULE_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "rules"

SCHEMA_FILE_VERSIONS: Dict[str, str] = {
    "forms": "layout-forge/rules/forms@1",
    "parameters": "layout-forge/rules/parameters@1",
    "rules": "layout-forge/rules/rules@1",
    "sources": "layout-forge/rules/sources@1",
    "severity": "layout-forge/rules/severity@1",
}

# 四类对象允许出现的字段：拼错的字段一律拒绝，不静默忽略（README 的硬要求）
KNOWN_FIELDS: Dict[str, set] = {
    "forms": {"id", "name", "when", "desc"},
    "parameters": {"id", "form", "keys", "value", "allowed", "warn", "eps",
                   "support", "source", "field", "unit", "note"},
    "rules": {"id", "name", "use", "assert", "severity", "source", "since",
              "derive", "form", "when", "display", "summary", "note"},
    "sources": {"id", "kind", "ref", "note"},
    "severity": {"id", "severity", "consequence", "evidence", "note"},
}

# 四类对象 + severity 表的文件名（都是 {"items": [...]} 形状）
SCHEMA_FILES: Dict[str, str] = {
    "forms": "forms.json",
    "parameters": "parameters.json",
    "rules": "rules.json",
    "sources": "sources.json",
    "severity": "_severity.json",
}

# 冻结的旧阈值文件：不是新库的一部分，出现在库目录里也不加载
FROZEN_FILES = frozenset({"checker_rules.json"})

# 形态谓词白名单（ADR D1）：层存在 / 层集包含 / 孔形匹配 / 名字族 /
# 安装方式 / 焊盘数。想加新谓词必须先进这份清单（改动要过架构断言）。
PREDICATES: Dict[str, Tuple[str, ...]] = {
    "mount": ("equals", "in"),                 # 安装方式：tht / smd
    "hole_shape": ("in", "not_in"),            # 孔形：OVAL SLOT / CIRCLE_DRILL
    "layer_exists": ("layer",),                # 某层存在
    "layer_set_includes": ("layers",),         # 层集包含这些层
    "name_family": ("regex",),                 # 名字族（正则）
    "pad_count": ("min", "max"),               # 焊盘数区间（闭区间）
}

# 断言种类白名单（ADR D6/D9）：等值 / 不等 / 区间 / 上下界 / 绝对值
ASSERT_KINDS: Tuple[str, ...] = ("allowed", "eq", "ne", "range", "bounds", "abs")

DEFAULT_EPS = 0.005    # 边界浮点擦边，与旧 2.2 同口径

# 派生表达式里允许出现的名字（参数键 / 层名 / 四则运算）；派生实现留给 #18


class RuleLibraryError(Exception):
    """规则库不合法：整体加载失败。errors 逐条 {file, path, message}。"""

    def __init__(self, errors: List[Dict[str, str]], directory: Optional[str] = None):
        self.errors = list(errors)
        self.directory = directory
        lines = [f"规则库加载失败（{directory or '?'}），共 {len(self.errors)} 处："]
        for e in self.errors[:10]:
            lines.append(f"  - {e.get('file', '?')} > {e.get('path', '?')}: {e.get('message', '')}")
        if len(self.errors) > 10:
            lines.append(f"  …另有 {len(self.errors) - 10} 处")
        super().__init__("\n".join(lines))


def canonical_json(obj: Any) -> str:
    """确定性序列化：键排序、固定分隔符、不转义中文。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================
# 库对象
# ============================================================
class RuleLibrary:
    """加载后的整库：四类对象按 id 索引 + severity 表 + 整库哈希。"""

    def __init__(self, directory: Path, files: Dict[str, List[Dict[str, Any]]],
                 fingerprint: str, file_hashes: Dict[str, str]) -> None:
        self.directory = str(directory)
        self.file_hashes = dict(file_hashes)
        self.fingerprint = fingerprint
        self.forms: List[Dict[str, Any]] = files["forms"]
        self.parameters: List[Dict[str, Any]] = files["parameters"]
        self.rules: List[Dict[str, Any]] = files["rules"]
        self.sources: List[Dict[str, Any]] = files["sources"]
        self.severity: List[Dict[str, Any]] = files["severity"]
        self.forms_by_id: Dict[str, Dict[str, Any]] = {o["id"]: o for o in self.forms}
        self.parameters_by_id: Dict[str, Dict[str, Any]] = {o["id"]: o for o in self.parameters}
        self.rules_by_id: Dict[str, Dict[str, Any]] = {o["id"]: o for o in self.rules}
        self.sources_by_id: Dict[str, Dict[str, Any]] = {o["id"]: o for o in self.sources}
        self.severity_by_id: Dict[str, Dict[str, Any]] = {o["id"]: o for o in self.severity}

    def dump(self) -> Dict[str, Any]:
        """整库的确定性视图（报告 / 哈希 / 对拍都用它）。"""
        return {
            "schema": SCHEMA_VERSION,
            "forms": self.forms,
            "parameters": self.parameters,
            "rules": self.rules,
            "sources": self.sources,
            "severity": self.severity,
        }

    def __repr__(self) -> str:                       # pragma: no cover - 调试用
        return (f"<RuleLibrary {self.directory} forms={len(self.forms)} "
                f"params={len(self.parameters)} rules={len(self.rules)} "
                f"hash={self.fingerprint[:12]}>")


# ============================================================
# 加载 + 校验
# ============================================================
def _err(errors: List[Dict[str, str]], file: str, path: str, message: str) -> None:
    errors.append({"file": file, "path": path, "message": message})


def _require(errors: List[Dict[str, str]], file: str, path: str, obj: Dict[str, Any],
             key: str, kind: type, allow_none: bool = False) -> bool:
    """必填字段 + 类型校验；返回是否存在且类型对。"""
    label = obj.get("id")
    suffix = f"（对象 {label}）" if isinstance(label, str) and label else ""
    if key not in obj:
        _err(errors, file, f"{path}.{key}", f"缺必填字段{suffix}")
        return False
    val = obj[key]
    if val is None:
        if allow_none:
            return True
        _err(errors, file, f"{path}.{key}", "值为 null")
        return False
    if not isinstance(val, kind):
        _err(errors, file, f"{path}.{key}",
             f"类型应为 {getattr(kind, '__name__', kind)}，实际 {type(val).__name__}")
        return False
    return True


def _check_predicate(errors: List[Dict[str, str]], file: str, path: str,
                     pred: Any, owner: str = "") -> None:
    suffix = f"（形态 {owner}）" if owner else ""
    if not isinstance(pred, dict):
        _err(errors, file, path, f"谓词必须是对象{suffix}")
        return
    name = pred.get("predicate")
    if not isinstance(name, str) or not name:
        _err(errors, file, f"{path}.predicate", f"缺谓词名{suffix}")
        return
    if name not in PREDICATES:
        _err(errors, file, f"{path}.predicate",
             f"未知谓词 {name!r}（白名单：{'/'.join(sorted(PREDICATES))}）{suffix}")
        return
    allowed_keys = set(PREDICATES[name]) | {"predicate"}
    extra = set(pred) - allowed_keys
    if extra:
        _err(errors, file, f"{path}.{sorted(extra)[0]}", f"谓词 {name} 不认识的字段 {sorted(extra)}")
        return
    args = {k: v for k, v in pred.items() if k != "predicate"}
    if not args:
        _err(errors, file, path, f"谓词 {name} 缺比较参数")
        return
    for key, val in args.items():
        if key in ("in", "not_in", "layers"):
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                _err(errors, file, f"{path}.{key}", "应为字符串数组")
        elif key == "regex":
            if not isinstance(val, str):
                _err(errors, file, f"{path}.{key}", "应为字符串")
            else:
                try:
                    re.compile(val)
                except re.error as exc:
                    _err(errors, file, f"{path}.{key}", f"正则不合法：{exc}")
        elif key in ("min", "max"):
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                _err(errors, file, f"{path}.{key}", "应为数字")
        elif key == "equals":
            if not isinstance(val, (str, int, float, bool)):
                _err(errors, file, f"{path}.{key}", "应为标量")
        elif key == "layer":
            if not isinstance(val, str):
                _err(errors, file, f"{path}.{key}", "应为字符串")


def _check_derive_expr(errors: List[Dict[str, str]], file: str, path: str,
                       expr: Any, allowed_names: Iterable[str]) -> None:
    """派生表达式只允许：数字 / 参数键 / 层名占位符 / 四则运算。"""
    if not isinstance(expr, str) or not expr.strip():
        _err(errors, file, path, "派生表达式应为非空字符串")
        return
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        _err(errors, file, path, f"派生表达式语法错误：{exc.msg}")
        return
    allowed = set(allowed_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in allowed:
                _err(errors, file, path,
                     f"派生表达式引用了不允许的名字 {node.id!r}（只许参数键/层名）")
                return
        elif isinstance(node, (ast.Call, ast.Attribute, ast.Subscript, ast.Lambda,
                               ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
                               ast.Compare, ast.BoolOp, ast.IfExp, ast.JoinedStr)):
            _err(errors, file, path, f"派生表达式含非法语法 {type(node).__name__}")
            return


def _read_schema_file(directory: Path, key: str, errors: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    name = SCHEMA_FILES[key]
    path = directory / name
    hashes: Dict[str, str] = {}
    if not path.is_file():
        _err(errors, name, "$", "库文件缺失")
        return [], hashes
    raw = path.read_bytes()
    hashes[name] = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _err(errors, name, "$", f"JSON 解析失败：{exc}")
        return [], hashes
    expected = SCHEMA_FILE_VERSIONS.get(key)
    if isinstance(data, dict) and expected and data.get("schema") != expected:
        _err(errors, name, "$.schema", f"schema 应为 {expected!r}，实际 {data.get('schema')!r}")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        _err(errors, name, "$.items", "顶层必须是 {\"items\": [...]}")
        return [], hashes
    out: List[Dict[str, Any]] = []
    for i, obj in enumerate(items):
        if not isinstance(obj, dict):
            _err(errors, name, f"items[{i}]", "数组元素必须是对象")
            continue
        _require(errors, name, f"items[{i}]", obj, "id", str)
        out.append(obj)
    return out, hashes


def _validate_forms(forms: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    name = SCHEMA_FILES["forms"]
    for i, form in enumerate(forms):
        p = f"items[{i}]"
        for key in ("name", "when"):
            _require(errors, name, p, form, key, dict if key == "when" else str)
        when = form.get("when")
        if not isinstance(when, dict):
            continue
        conds = when.get("all")
        if not isinstance(conds, list) or not conds:
            _err(errors, name, f"{p}.when.all", "形态必须至少有一条谓词")
            continue
        for j, pred in enumerate(conds):
            _check_predicate(errors, name, f"{p}.when.all[{j}]", pred,
                             str(form.get("id") or ""))
        if "any" in when:
            _err(errors, name, f"{p}.when.any", "when 暂不支持 any（只支持全部谓词同时满足）")
        unknown = set(when) - {"all", "any"}
        if unknown:
            _err(errors, name, f"{p}.when.{sorted(unknown)[0]}",
                 f"when 只支持 all/any，发现 {sorted(unknown)}")


def _validate_parameters(params: List[Dict[str, Any]], forms: List[Dict[str, Any]],
                         sources: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    name = SCHEMA_FILES["parameters"]
    form_ids = {f.get("id") for f in forms}
    source_ids = {s.get("id") for s in sources}
    for i, par in enumerate(params):
        p = f"items[{i}]"
        _require(errors, name, p, par, "form", str)
        _require(errors, name, p, par, "keys", list)
        _require(errors, name, p, par, "value", (int, float))
        _require(errors, name, p, par, "allowed", list)
        _require(errors, name, p, par, "support", int)
        _require(errors, name, p, par, "source", str)
        form = par.get("form")
        if isinstance(form, str) and form not in form_ids:
            _err(errors, name, f"{p}.form",
                 f"引用了不存在的形态 {form!r}（参数 {par.get('id')}）")
        src = par.get("source")
        if isinstance(src, str) and src not in source_ids:
            _err(errors, name, f"{p}.source",
                 f"引用了不存在的出处 {src!r}（参数 {par.get('id')}）")
        keys = par.get("keys")
        if isinstance(keys, list) and (not keys or not all(isinstance(k, str) and k for k in keys)):
            _err(errors, name, f"{p}.keys", "应为非空字符串数组")
        allowed = par.get("allowed")
        if isinstance(allowed, list):
            if len(allowed) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                            for x in allowed):
                _err(errors, name, f"{p}.allowed", "应为 [下限, 上限] 两个数字")
            elif allowed[0] > allowed[1]:
                _err(errors, name, f"{p}.allowed", "下限大于上限")
            else:
                val = par.get("value")
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if not (allowed[0] <= val <= allowed[1]):
                        _err(errors, name, f"{p}.value", f"value {val} 不在 allowed {allowed} 内")
        eps = par.get("eps")
        if eps is not None and (not isinstance(eps, (int, float)) or isinstance(eps, bool) or eps < 0):
            _err(errors, name, f"{p}.eps", "应为非负数字")
        support = par.get("support")
        if isinstance(support, int) and support < 0:
            _err(errors, name, f"{p}.support", "支持件数不能为负")


def _validate_rules(rules: List[Dict[str, Any]], forms: List[Dict[str, Any]],
                    params: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    severity: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    name = SCHEMA_FILES["rules"]
    param_ids = {q.get("id") for q in params}
    source_ids = {s.get("id") for s in sources}
    sev_ids = {s.get("id") for s in severity}
    for i, rule in enumerate(rules):
        p = f"items[{i}]"
        for key in ("name", "use", "assert", "severity", "source", "since"):
            _require(errors, name, p, rule, key, str if key != "use" and key != "assert" else list)
        use = rule.get("use")
        if isinstance(use, list):
            if not use:
                _err(errors, name, f"{p}.use", "规则至少要引用一个参数")
            for j, pid in enumerate(use):
                if not isinstance(pid, str):
                    _err(errors, name, f"{p}.use[{j}]", "参数 id 应为字符串")
                elif pid not in param_ids:
                    _err(errors, name, f"{p}.use[{j}]",
                         f"引用了不存在的参数 {pid!r}（规则 {rule.get('id')}）")
        asserts = rule.get("assert")
        if isinstance(asserts, list):
            if not asserts:
                _err(errors, name, f"{p}.assert", "规则至少要有一条断言")
            for j, a in enumerate(asserts):
                ap = f"{p}.assert[{j}]"
                if not isinstance(a, dict):
                    _err(errors, name, ap, "断言必须是对象")
                    continue
                kind = a.get("kind")
                if kind not in ASSERT_KINDS:
                    _err(errors, name, f"{ap}.kind", f"未知断言种类 {kind!r}（白名单：{'/'.join(ASSERT_KINDS)}）")
                    continue
                extra = set(a) - {"kind", "eps", "tol", "lo", "hi", "min", "max", "value"}
                if extra:
                    _err(errors, name, f"{ap}.{sorted(extra)[0]}", f"断言不认识的字段 {sorted(extra)}")
        sev = rule.get("severity")
        if isinstance(sev, str) and sev not in sev_ids:
            _err(errors, name, f"{p}.severity", f"引用了不存在的 severity {sev!r}")
        src = rule.get("source")
        if isinstance(src, str) and src not in source_ids:
            _err(errors, name, f"{p}.source", f"引用了不存在的出处 {src!r}")
        derive = rule.get("derive")
        if derive is not None:
            names = set()
            for pid in (use or []):
                for q in params:
                    if q.get("id") == pid:
                        names.update(q.get("keys") or [])
            _check_derive_expr(errors, name, f"{p}.derive", derive, names)
        form = rule.get("form")
        if form is not None and form not in {f.get("id") for f in forms}:
            _err(errors, name, f"{p}.form", f"引用了不存在的形态 {form!r}")


def _validate_sources(sources: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    name = SCHEMA_FILES["sources"]
    for i, src in enumerate(sources):
        p = f"items[{i}]"
        for key in ("kind", "ref"):
            _require(errors, name, p, src, key, str)


def _validate_severity(severity: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    name = SCHEMA_FILES["severity"]
    for i, sev in enumerate(severity):
        p = f"items[{i}]"
        _require(errors, name, p, sev, "severity", str)
        _require(errors, name, p, sev, "consequence", str)
        _require(errors, name, p, sev, "evidence", str)
        s = sev.get("severity")
        if isinstance(s, str) and s not in ("PASS", "FAIL", "WARN", "NA", "REVIEW_REQUIRED"):
            _err(errors, name, f"{p}.severity", f"未知状态 {s!r}")


def _check_duplicate_ids(objs: Dict[str, List[Dict[str, Any]]], errors: List[Dict[str, str]]) -> None:
    seen: Dict[str, Dict[str, str]] = {}
    for file_key, items in objs.items():
        file_name = SCHEMA_FILES[file_key]
        for i, obj in enumerate(items):
            oid = obj.get("id")
            if not isinstance(oid, str) or not oid:
                continue
            if oid in seen:
                first = seen[oid]
                _err(errors, file_name, f"items[{i}].id",
                     f"id {oid!r} 重复（首次出现在 {first['file']} > {first['path']}）")
            else:
                seen[oid] = {"file": file_name, "path": f"items[{i}]"}


_CACHE: Dict[str, Tuple[Tuple[Tuple[str, int, int, int], ...], RuleLibrary]] = {}


def _dir_state(directory: Path) -> Tuple[Tuple[str, int, int, int], ...]:
    state = []
    for name in sorted(p.name for p in directory.glob("*.json") if p.is_file()):
        path = directory / name
        st = path.stat()
        state.append((name, int(st.st_mtime_ns), int(st.st_ctime_ns), int(st.st_size)))
    return tuple(state)


def reset_cache() -> None:
    """清加载缓存（测试改库 / 切目录后调）。"""
    _CACHE.clear()


def load_library(path: Optional[Any] = None) -> RuleLibrary:
    """整体加载规则库。任何一处不合法 → RuleLibraryError（绝不半载）。

    目录优先级：显式参数 > 环境变量 LAYOUT_FORGE_RULES_DIR > knowledge/rules。
    同一目录、文件没变 → 命中缓存（改 JSON 立刻生效，不用重启进程）。
    """
    directory = Path(path or os.environ.get(RULE_DIR_ENV) or DEFAULT_RULE_DIR).resolve()
    key = str(directory)
    state = _dir_state(directory) if directory.is_dir() else ()
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == state and state:
        return cached[1]

    errors: List[Dict[str, str]] = []
    if not directory.is_dir():
        _err(errors, str(directory), "$", "规则库目录不存在")
        raise RuleLibraryError(errors, str(directory))

    files: Dict[str, List[Dict[str, Any]]] = {}
    hashes: Dict[str, str] = {}
    for schema_key in SCHEMA_FILES:
        items, hs = _read_schema_file(directory, schema_key, errors)
        files[schema_key] = items
        hashes.update(hs)

    unknown_json = sorted(
        p.name for p in directory.glob("*.json")
        if p.name not in set(SCHEMA_FILES.values()) and p.name not in FROZEN_FILES
    )
    for name in unknown_json:
        _err(errors, name, "$", "库目录里的未知 JSON（新对象类型请先登记 schema）")

    if not errors:
        for file_key, items in files.items():
            known = KNOWN_FIELDS[file_key]
            for i, obj in enumerate(items):
                extra = set(obj) - known
                if extra:
                    _err(errors, SCHEMA_FILES[file_key], f"items[{i}].{sorted(extra)[0]}",
                         f"未声明字段 {sorted(extra)}")
        _check_duplicate_ids(files, errors)
        _validate_forms(files["forms"], errors)
        _validate_sources(files["sources"], errors)
        _validate_severity(files["severity"], errors)
        _validate_parameters(files["parameters"], files["forms"], files["sources"], errors)
        _validate_rules(files["rules"], files["forms"], files["parameters"],
                        files["sources"], files["severity"], errors)

    if errors:
        raise RuleLibraryError(errors, str(directory))

    fingerprint = sha256_text(canonical_json(files))
    lib = RuleLibrary(directory, files, fingerprint, hashes)
    _CACHE[key] = (state, lib)
    return lib


# ============================================================
# 形态匹配
# ============================================================
def _subject_layers(subject: Dict[str, Any]) -> List[str]:
    layers = subject.get("layers")
    if isinstance(layers, list):
        return [str(x) for x in layers]
    return []


def _subject_hole_shape(subject: Dict[str, Any]) -> Optional[str]:
    drill = subject.get("drill")
    if isinstance(drill, dict):
        shape = drill.get("shape")
        if isinstance(shape, str):
            return shape
    return None


def _pred_match(pred: Dict[str, Any], subject: Dict[str, Any]) -> bool:
    name = pred.get("predicate")
    if name == "mount":
        mount = subject.get("mount")
        if "equals" in pred:
            return mount == pred["equals"]
        return mount in (pred.get("in") or [])
    if name == "hole_shape":
        shape = _subject_hole_shape(subject)
        if "in" in pred:
            return shape in (pred["in"] or [])
        return shape not in (pred.get("not_in") or [])
    if name == "layer_exists":
        return pred.get("layer") in _subject_layers(subject)
    if name == "layer_set_includes":
        layers = set(_subject_layers(subject))
        return all(layer in layers for layer in (pred.get("layers") or []))
    if name == "name_family":
        regex = pred.get("regex")
        return isinstance(regex, str) and re.search(regex, str(subject.get("name") or "")) is not None
    if name == "pad_count":
        count = subject.get("pad_count")
        if not isinstance(count, (int, float)):
            return False
        if "min" in pred and count < pred["min"]:
            return False
        if "max" in pred and count > pred["max"]:
            return False
        return True
    return False


def match_form(lib: RuleLibrary, subject: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按库里的形态顺序取第一个匹配的形态；都不匹配 → None（调用方报缺 form）。"""
    for form in lib.forms:
        when = form.get("when") or {}
        conds = when.get("all") or []
        if conds and all(_pred_match(p, subject) for p in conds):
            return form
    return None


# ============================================================
# 判定
# ============================================================
def _skip(rule_id: str, detail: str, form: Optional[str] = None,
          subject_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "status": "SKIP",
        "rule_id": rule_id,
        "form": form,
        "subject": subject_id,
        "severity": "SKIP",
        "detail": detail,
    }


def _value_in(value: float, lo: float, hi: float, eps: float) -> bool:
    return (lo - eps) <= value <= (hi + eps)


def _eval_assert(assertion: Dict[str, Any], param: Dict[str, Any],
                 observed: Dict[str, Any]) -> Tuple[bool, str]:
    """跑一条断言；返回 (是否满足, 不满足时的说明)。"""
    kind = assertion.get("kind")
    eps = assertion.get("eps", param.get("eps", DEFAULT_EPS))
    keys = param.get("keys") or []
    allowed = param.get("allowed") or [None, None]
    lo = assertion.get("lo", allowed[0])
    hi = assertion.get("hi", allowed[1])
    bad: List[str] = []
    for key in keys:
        val = observed[key]
        if kind in ("allowed", "range"):
            if not _value_in(val, lo, hi, eps):
                bad.append(f"{key}={val:g} 不在 [{lo:g}, {hi:g}]±{eps:g}")
        elif kind == "eq":
            target = assertion.get("value", param.get("value"))
            tol = assertion.get("tol", eps)
            if target is None or abs(val - target) > tol:
                bad.append(f"{key}={val:g} 不等于 {target}（±{tol:g}）")
        elif kind == "ne":
            target = assertion.get("value", param.get("value"))
            tol = assertion.get("tol", eps)
            if target is not None and abs(val - target) <= tol:
                bad.append(f"{key}={val:g} 不应等于 {target}")
        elif kind == "bounds":
            if "min" in assertion and val < assertion["min"]:
                bad.append(f"{key}={val:g} 小于下界 {assertion['min']}")
            if "max" in assertion and val > assertion["max"]:
                bad.append(f"{key}={val:g} 大于上界 {assertion['max']}")
        elif kind == "abs":
            limit = assertion.get("max", param.get("value"))
            if limit is None or abs(val) > abs(limit):
                bad.append(f"{key}={val:g} 超出 |{limit}|")
    if bad:
        return False, "；".join(bad)
    return True, ""


def judge(lib: RuleLibrary, rule_id: str, subject: Dict[str, Any],
          observed: Dict[str, Any]) -> Dict[str, Any]:
    """单条规则在一个 subject 上判定，纯函数。

    :param lib: load_library() 的返回值
    :param rule_id: rules.json 里的规则 id
    :param subject: 事实（mount / layers / drill / name / pad_count）
    :param observed: 观测值（按参数 keys 取值，缺键即 UNKNOWN）
    :return: finding dict（status / rule_id / form / severity / source / support /
             rule_set_hash / detail …）；三态之一，SKIP 也带 rule_id。
    """
    subject_id = subject.get("id")
    rule = lib.rules_by_id.get(rule_id)
    if rule is None:
        return _skip(rule_id, f"缺规则：规则库里没有 {rule_id}")

    form = match_form(lib, subject)
    if form is None:
        return _skip(rule_id, f"缺 form：{subject_id} 没有匹配的形态", subject_id=subject_id)
    form_id = form.get("id")

    params = [lib.parameters_by_id[pid] for pid in rule.get("use", [])
              if pid in lib.parameters_by_id and lib.parameters_by_id[pid].get("form") == form_id]
    if not params:
        return _skip(rule_id, f"缺参数：形态 {form_id} 没有绑定参数", form=form_id,
                     subject_id=subject_id)

    param = params[0]
    keys = param.get("keys") or []
    missing = [k for k in keys if k not in observed]
    if missing:
        return _skip(rule_id, f"输入 UNKNOWN：缺观测 {', '.join(missing)}", form=form_id,
                     subject_id=subject_id)

    severity_ref = rule.get("severity")
    sev_obj = lib.severity_by_id.get(severity_ref) or {}
    severity = sev_obj.get("severity") or "WARN"
    source = rule.get("source")
    source_ref = param.get("source")

    allowed = list(param.get("allowed") or [None, None])
    eps = float(param.get("eps", DEFAULT_EPS))

    base = {
        "status": "PASS",
        "rule_id": rule_id,
        "form": form_id,
        "subject": subject_id,
        "param": param.get("id"),
        "severity": None,
        "severity_ref": severity_ref,
        "source": source,
        "source_ref": source_ref,
        "support": param.get("support"),
        "rule_set_hash": lib.fingerprint,
        "value": {k: observed[k] for k in keys},
        "allowed": allowed,
        "eps": eps,
        "detail": "",
    }

    notes: List[str] = []
    for assertion in rule.get("assert") or []:
        ok, note = _eval_assert(assertion, param, observed)
        if not ok:
            notes.append(note)

    if notes:
        base["status"] = severity
        base["severity"] = severity
        base["detail"] = "违反：" + "；".join(notes)
    return base
