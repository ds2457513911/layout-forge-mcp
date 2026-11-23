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
  parameters.json 参数：id + keys/allowed/eps（所有形态共用）+ groups[]（每形态
                  一份 value/support/source）；单形态参数也可写扁平的
                  form/value/support/source —— 两种形状二选一，见 parameters@2
  rules.json      规则：id + use（参数 id）+ assert + severity + source
  sources.json    出处：id + kind + ref + note
  _severity.json  severity 映射表：id + severity + consequence + evidence
  library.json    规则集版本：version（语义版本）+ released_at + note；版本进哈希、
                  进报告首屏（ADR-0002 D9）——库内容与版本任一处变，哈希就变
  checker_rules.json ★ 冻结只读的旧阈值文件，本模块不读、不许改

覆盖率（票 #21）：coverage() 回答两个不同的问题，**分开列**（ADR-0002 D11）：
  - 有形态但无规则：form 表 × 规则表差集，静态可算（forms_without_rule）
  - 无形态：输入连 form 都匹配不上，只有运行时才知道（inputs_without_form）
方向（ADR-0002 D2）：assert 用于判定、derive 用于派生；只有一侧有规则的形态
在覆盖率里标 assert_only / derive_only，不当作「有规则」。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services import padstack_naming

SCHEMA_VERSION = "layout-forge/rules@1"

# 规则集版本文件（library.json）是唯一不是 {"items": [...]} 形状的库文件。
META_SCHEMA_KEYS = frozenset({"library"})
LIBRARY_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
LIBRARY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KNOWN_LIBRARY_FIELDS = frozenset({"schema", "version", "released_at", "note"})

# 覆盖率段的 schema 与方向标签（ADR-0002 D2 / D11）
COVERAGE_SCHEMA = "layout-forge/coverage@1"
DIRECTIONS: Tuple[str, ...] = ("assert", "derive")

# 库目录：环境变量优先（测试用），否则项目根下 knowledge/rules
RULE_DIR_ENV = "LAYOUT_FORGE_RULES_DIR"
DEFAULT_RULE_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "rules"

SCHEMA_FILE_VERSIONS: Dict[str, str] = {
    "library": "layout-forge/rules/library@1",
    "forms": "layout-forge/rules/forms@1",
    "parameters": "layout-forge/rules/parameters@2",
    "rules": "layout-forge/rules/rules@1",
    "sources": "layout-forge/rules/sources@1",
    "severity": "layout-forge/rules/severity@1",
}

# 四类对象允许出现的字段：拼错的字段一律拒绝，不静默忽略（README 的硬要求）
KNOWN_FIELDS: Dict[str, set] = {
    "forms": {"id", "name", "when", "desc"},
    "parameters": {"id", "form", "keys", "value", "allowed", "warn", "eps",
                   "support", "source", "field", "unit", "note", "groups",
                   "kind", "grammar", "tol", "derive_value"},
    "rules": {"id", "name", "use", "assert", "severity", "source", "since",
              "derive", "form", "when", "display", "summary", "note",
              "report_id", "exempt"},
    "sources": {"id", "kind", "ref", "note"},
    "severity": {"id", "severity", "consequence", "evidence", "note"},
}

# 四类对象 + severity 表的文件名（都是 {"items": [...]} 形状）
SCHEMA_FILES: Dict[str, str] = {
    "library": "library.json",
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

# 断言种类白名单（ADR D6/D9）：等值 / 不等 / 区间 / 上下界 / 绝对值 /
# 命名反推（名字里的数字必须与实测尺寸相符；不解析模板，ADR D6）
ASSERT_KINDS: Tuple[str, ...] = ("allowed", "eq", "ne", "range", "bounds", "abs",
                                 "naming")

# 命名语法白名单：语法 id 在数据里，解析器仍是代码（ADR D6）
NAMING_GRAMMARS: Tuple[str, ...] = ("TH-OBLONG", "TH-CIRCLE")
NAMING_KEYS: Dict[str, Tuple[str, ...]] = {
    "TH-OBLONG": ("name", "hole_long", "hole_short", "pad_long", "pad_short"),
    "TH-CIRCLE": ("name", "hole_dia", "pad_dia"),
}

# 参数形态组允许出现的字段：组只给「这个形态的值/支撑/出处」，
# 区间与观测键属于参数本身（一处定义，所有形态共用）。
# derive_value（票 #20）：同一参数的「建库口径」与「语料典型值」是两个概念 ——
# value 是语料统计出的典型值（判定侧展示/支撑），derive_value 是派生时该用的
# 单值（ADR D4：value 是派生用的单值；本票把历史 0.05 建库口径显式写进数据）。
# 未写 derive_value 时回落该组的 value（R6.12 的临时库只改 value 仍生效）。
GROUP_FIELDS = frozenset({"form", "value", "derive_value", "support", "source", "note"})

DEFAULT_EPS = 0.005    # 边界浮点擦边，与旧 2.2 同口径

# 派生方向允许读取的事实（ADR D5/D7）：只有这些名字能出现在 derive 表达式里，
# 事实缺失时派生报 SKIP（UNKNOWN），不许拿默认值静默填。
DERIVE_FACTS = frozenset({
    "pad_x", "pad_y", "hole_x", "hole_y", "hole_long", "hole_short",
    "pad_long", "pad_short", "plated", "offset_x", "offset_y",
    "paste_bottom", "shape", "figure",
})

# 派生对象允许出现的字段：层集 / 各层尺寸 / 钻孔 / 命名 / 旋转 / 说明。
# 不管 pin 坐标、阵列、原点、本体、Place_Bound、丝印、位号（ADR D5，仍归 solver）。
DERIVE_FIELDS = frozenset({"values", "layers", "drill", "name", "rotation", "notes"})
DERIVE_LAYER_FIELDS = frozenset({"layer", "figure", "size", "when"})
DERIVE_DRILL_FIELDS = frozenset({"size", "drill_type", "figure", "plated_from", "offset"})
DERIVE_NAME_STYLES: Tuple[str, ...] = ("th_oblong", "padstack_name")

# 派生表达式里允许的运算：数字 / 名字 / 一元正负 / 四则（AST 白名单，禁函数调用）


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
    """加载后的整库：四类对象按 id 索引 + severity 表 + 版本 + 整库哈希。"""

    def __init__(self, directory: Path, files: Dict[str, Any],
                 fingerprint: str, file_hashes: Dict[str, str]) -> None:
        self.directory = str(directory)
        self.file_hashes = dict(file_hashes)
        self.fingerprint = fingerprint
        self.library: Dict[str, Any] = dict(files.get("library") or {})
        self.version: str = str(self.library.get("version") or "")
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
            "library": self.library,
            "forms": self.forms,
            "parameters": self.parameters,
            "rules": self.rules,
            "sources": self.sources,
            "severity": self.severity,
        }

    def __repr__(self) -> str:                       # pragma: no cover - 调试用
        return (f"<RuleLibrary {self.directory} v{self.version} forms={len(self.forms)} "
                f"params={len(self.parameters)} rules={len(self.rules)} "
                f"hash={self.fingerprint[:12]}>")


# ============================================================
# 加载 + 校验
# ============================================================
def _err(errors: List[Dict[str, str]], file: str, path: str, message: str) -> None:
    errors.append({"file": file, "path": path, "message": message})


def _require(errors: List[Dict[str, str]], file: str, path: str, obj: Dict[str, Any],
             key: str, kind: type, allow_none: bool = False,
             owner: Optional[str] = None) -> bool:
    """必填字段 + 类型校验；返回是否存在且类型对。

    owner 用于「报错要指得出是哪个对象」但被校验的是子结构（形态组没有自己的 id）
    的场合：错误后缀带的是所属参数 id，而不是组的 form。
    """
    label = owner or obj.get("id")
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
    """派生表达式只允许：数字 / 事实名 / 参数键 / 一元正负 / 四则运算（AST 白名单）。"""
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
        if isinstance(node, (ast.Expression, ast.operator, ast.unaryop,
                            ast.expr_context)):
            continue
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                _err(errors, file, path, "派生表达式只许数字常量")
                return
        elif isinstance(node, ast.Name):
            if node.id not in allowed:
                _err(errors, file, path,
                     f"派生表达式引用了不允许的名字 {node.id!r}"
                     "（只许事实/参数键/先声明的 values）")
                return
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                _err(errors, file, path,
                     f"派生表达式含非法一元运算 {type(node.op).__name__}")
                return
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                _err(errors, file, path,
                     f"派生表达式含非法运算 {type(node.op).__name__}")
                return
        else:
            _err(errors, file, path, f"派生表达式含非法语法 {type(node).__name__}")
            return


def _template_placeholders(template: str) -> List[str]:
    """模板里的占位符名（{name}）；带格式说明符/转换的视为非法，返回 [] 由调用方报错。"""
    from string import Formatter
    out: List[str] = []
    for _literal, field, spec, conv in Formatter().parse(template):
        if field is None:
            continue
        if spec or conv or not str(field).isidentifier():
            return []
        out.append(str(field))
    return out


def _check_template(errors: List[Dict[str, str]], file: str, path: str,
                    template: Any, allowed_names: Iterable[str], what: str) -> None:
    if not isinstance(template, str) or not template.strip():
        _err(errors, file, path, f"{what}应为非空字符串")
        return
    names = _template_placeholders(template)
    if not names:
        _err(errors, file, path, f"{what}里的占位符不合法（只许 {{名字}}）")
        return
    allowed = set(allowed_names)
    for name in names:
        if name not in allowed:
            _err(errors, file, path,
                 f"{what}引用了不允许的名字 {name!r}（只许事实/参数键/values）")
            return


def _check_derive_object(errors: List[Dict[str, str]], file: str, path: str,
                         derive: Any, rule: Dict[str, Any],
                         params: List[Dict[str, Any]]) -> None:
    """校验一份 derive 定义（ADR D5/D7）：层集 / 尺寸 / 钻孔 / 命名 / 旋转 / 说明。"""
    if not isinstance(derive, dict):
        _err(errors, file, path, "derive 必须是对象")
        return
    extra = set(derive) - DERIVE_FIELDS
    if extra:
        _err(errors, file, f"{path}.{sorted(extra)[0]}",
             f"derive 不认识的字段 {sorted(extra)}（只许 {'/'.join(sorted(DERIVE_FIELDS))}）")
    if not derive:
        _err(errors, file, path, "derive 不能为空对象")
        return

    names = set(DERIVE_FACTS)
    for pid in rule.get("use") or []:
        for q in params:
            if q.get("id") == pid:
                names.update(q.get("keys") or [])

    values = derive.get("values", {})
    if not isinstance(values, dict):
        _err(errors, file, f"{path}.values", "values 必须是对象（名 → 表达式）")
        values = {}
    declared: List[str] = []
    for key, expr in values.items():
        vp = f"{path}.values.{key}"
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            _err(errors, file, f"{path}.values", f"values 名 {key!r} 不是合法标识符")
            continue
        if key in names:
            _err(errors, file, vp,
                 f"values 名 {key!r} 与事实/参数键同名（会盖掉读数，禁止）")
            continue
        _check_derive_expr(errors, file, vp, expr, names | set(declared))
        declared.append(key)
    usable = names | set(declared)

    layers = derive.get("layers")
    if not isinstance(layers, list) or not layers:
        _err(errors, file, f"{path}.layers", "layers 应为非空数组")
    else:
        for j, lay in enumerate(layers):
            lp = f"{path}.layers[{j}]"
            if not isinstance(lay, dict):
                _err(errors, file, lp, "层定义必须是对象")
                continue
            extra = set(lay) - DERIVE_LAYER_FIELDS
            if extra:
                _err(errors, file, f"{lp}.{sorted(extra)[0]}",
                     f"层定义不认识的字段 {sorted(extra)}")
                continue
            for key in ("layer", "figure", "size"):
                _require(errors, file, lp, lay, key,
                         str if key == "layer" else (list if key == "size" else object))
            if not isinstance(lay.get("layer"), str) or not lay.get("layer"):
                _err(errors, file, f"{lp}.layer", "层名应为非空字符串")
            fig = lay.get("figure")
            if isinstance(fig, dict):
                extra = set(fig) - {"from_fact"}
                if extra or not isinstance(fig.get("from_fact"), str):
                    _err(errors, file, f"{lp}.figure",
                         "figure 只能是字符串（层图形名）或 {\"from_fact\": 名字}")
                elif fig["from_fact"] not in usable:
                    _err(errors, file, f"{lp}.figure",
                         f"figure 引用了不允许的名字 {fig['from_fact']!r}")
            elif not isinstance(fig, str) or not fig:
                _err(errors, file, f"{lp}.figure", "figure 应为非空字符串或 {from_fact}")
            size = lay.get("size")
            if isinstance(size, list):
                if len(size) != 2:
                    _err(errors, file, f"{lp}.size", "size 应为 [宽, 高] 两个表达式")
                else:
                    for k, expr in enumerate(size):
                        _check_derive_expr(errors, file, f"{lp}.size[{k}]", expr, usable)
            when = lay.get("when")
            if when is not None:
                # 条件层（票 #20：底面钢网只在读数声明 paste_bottom 时进层集）
                if not isinstance(when, dict):
                    _err(errors, file, f"{lp}.when", "when 必须是对象或省略")
                else:
                    extra = set(when) - {"fact", "equals"}
                    if extra:
                        _err(errors, file, f"{lp}.when.{sorted(extra)[0]}",
                             f"when 不认识的字段 {sorted(extra)}（只许 fact/equals）")
                    fact = when.get("fact")
                    if fact not in DERIVE_FACTS:
                        _err(errors, file, f"{lp}.when.fact",
                             f"when.fact 必须是事实名（白名单：{'/'.join(sorted(DERIVE_FACTS))}）")
                    if not isinstance(when.get("equals"), bool):
                        _err(errors, file, f"{lp}.when.equals", "when.equals 必须是 true/false")

    drill = derive.get("drill", None)
    if drill is not None:
        if not isinstance(drill, dict):
            _err(errors, file, f"{path}.drill", "drill 必须是对象或 null")
        else:
            extra = set(drill) - DERIVE_DRILL_FIELDS
            if extra:
                _err(errors, file, f"{path}.drill.{sorted(extra)[0]}",
                     f"drill 不认识的字段 {sorted(extra)}")
            for key in ("size", "drill_type", "figure"):
                _require(errors, file, f"{path}.drill", drill, key,
                         str if key != "size" else list)
            size = drill.get("size")
            if isinstance(size, list):
                if len(size) != 2:
                    _err(errors, file, f"{path}.drill.size",
                         "drill.size 应为 [宽, 长] 两个表达式")
                else:
                    for k, expr in enumerate(size):
                        _check_derive_expr(errors, file, f"{path}.drill.size[{k}]", expr, usable)
            for key in ("drill_type", "figure"):
                if not isinstance(drill.get(key), str) or not drill.get(key):
                    _err(errors, file, f"{path}.drill.{key}", f"drill.{key} 应为非空字符串")
            pfrom = drill.get("plated_from")
            if pfrom is not None and pfrom not in usable:
                _err(errors, file, f"{path}.drill.plated_from",
                     f"plated_from 引用了不允许的名字 {pfrom!r}")
            off = drill.get("offset")
            if off is not None:
                if (not isinstance(off, list) or len(off) != 2
                        or not all(isinstance(x, str) for x in off)):
                    _err(errors, file, f"{path}.drill.offset",
                         "offset 应为 [x 名字, y 名字]（读数有偏移才写到产物）")
                else:
                    for x in off:
                        if x not in usable:
                            _err(errors, file, f"{path}.drill.offset",
                                 f"offset 引用了不允许的名字 {x!r}")

    name_def = derive.get("name")
    if not isinstance(name_def, dict):
        _err(errors, file, f"{path}.name", "name 必须是对象（style + 模板/尺寸）")
    else:
        style = name_def.get("style")
        if style not in DERIVE_NAME_STYLES:
            _err(errors, file, f"{path}.name.style",
                 f"未知命名风格 {style!r}（白名单：{'/'.join(DERIVE_NAME_STYLES)}）")
        elif style == "th_oblong":
            extra = set(name_def) - {"style", "template"}
            if extra:
                _err(errors, file, f"{path}.name.{sorted(extra)[0]}",
                     f"th_oblong 命名不认识的字段 {sorted(extra)}")
            _check_template(errors, file, f"{path}.name.template",
                            name_def.get("template"), usable, "th_oblong 命名模板")
        else:
            extra = set(name_def) - {"style", "shape", "dims"}
            if extra:
                _err(errors, file, f"{path}.name.{sorted(extra)[0]}",
                     f"padstack_name 命名不认识的字段 {sorted(extra)}")
            shape = name_def.get("shape")
            if not isinstance(shape, str) or shape not in usable:
                _err(errors, file, f"{path}.name.shape",
                     f"shape 引用了不允许的名字 {shape!r}")
            dims = name_def.get("dims")
            if (not isinstance(dims, list) or not dims
                    or not all(isinstance(x, str) and x in usable for x in dims)):
                _err(errors, file, f"{path}.name.dims",
                     "dims 应为尺寸名字数组（全部在事实/参数键/values 里）")

    rotation = derive.get("rotation")
    if rotation is not None and (not isinstance(rotation, (int, float))
                                 or isinstance(rotation, bool)):
        _err(errors, file, f"{path}.rotation", "rotation 应为数字（度）或省略")

    notes = derive.get("notes", [])
    if not isinstance(notes, list):
        _err(errors, file, f"{path}.notes", "notes 应为字符串数组")
    else:
        for j, note in enumerate(notes):
            _check_template(errors, file, f"{path}.notes[{j}]", note, usable,
                            f"notes[{j}] 说明")


def _read_meta_file(directory: Path, errors: List[Dict[str, str]]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """读 library.json（规则集版本）。它是唯一不是 items 形状的库文件。"""
    name = SCHEMA_FILES["library"]
    path = directory / name
    hashes: Dict[str, str] = {}
    if not path.is_file():
        _err(errors, name, "$", "库文件缺失（规则集版本必须显式声明）")
        return {}, hashes
    raw = path.read_bytes()
    hashes[name] = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _err(errors, name, "$", f"JSON 解析失败：{exc}")
        return {}, hashes
    if not isinstance(data, dict):
        _err(errors, name, "$", "顶层必须是对象")
        return {}, hashes
    expected = SCHEMA_FILE_VERSIONS["library"]
    if data.get("schema") != expected:
        _err(errors, name, "$.schema", f"schema 应为 {expected!r}，实际 {data.get('schema')!r}")
    extra = set(data) - KNOWN_LIBRARY_FIELDS
    if extra:
        _err(errors, name, f"$.{sorted(extra)[0]}", f"未声明字段 {sorted(extra)}")
    version = data.get("version")
    if not isinstance(version, str) or not LIBRARY_VERSION_RE.match(version):
        _err(errors, name, "$.version",
             f"版本应为 MAJOR.MINOR.PATCH（如 1.0.0），实际 {version!r}")
    released = data.get("released_at")
    if not isinstance(released, str) or not LIBRARY_DATE_RE.match(released):
        _err(errors, name, "$.released_at", f"released_at 应为 YYYY-MM-DD，实际 {released!r}")
    return data, hashes


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


def _range_ok(allowed, value):
    """value 是否落在 [下限, 上限]；非数字视为「不参与校验」（校验器另行报错）。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return True
    return (isinstance(allowed, list) and len(allowed) == 2
            and allowed[0] <= value <= allowed[1])


def _validate_parameters(params: List[Dict[str, Any]], forms: List[Dict[str, Any]],
                         sources: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    """参数两种形状：分组（多形态）与扁平（单形态），外加 naming 参数。

    naming 参数（kind=naming）是扁平形状的特例：没有 value/allowed，
    靠 grammar + keys + tol 描述「名字里的数字如何与实测尺寸反推比对」。
    """
    name = SCHEMA_FILES["parameters"]
    form_ids = {f.get("id") for f in forms}
    source_ids = {s.get("id") for s in sources}
    for i, par in enumerate(params):
        p = f"items[{i}]"
        who = par.get("id") or "?"
        _require(errors, name, p, par, "keys", list)
        keys = par.get("keys")
        if isinstance(keys, list) and (not keys or not all(isinstance(k, str) and k for k in keys)):
            _err(errors, name, f"{p}.keys", "应为非空字符串数组")

        kind = par.get("kind", "numeric")
        if kind not in ("numeric", "naming"):
            _err(errors, name, f"{p}.kind", f"未知参数种类 {kind!r}（numeric/naming）")
            kind = "numeric"

        if kind == "naming":
            for key, typ in (("form", str), ("grammar", str), ("support", int), ("source", str)):
                _require(errors, name, p, par, key, typ)
            grammar = par.get("grammar")
            if isinstance(grammar, str) and grammar not in NAMING_GRAMMARS:
                _err(errors, name, f"{p}.grammar",
                     f"未知命名语法 {grammar!r}（白名单：{'/'.join(NAMING_GRAMMARS)}）")
            elif isinstance(grammar, str) and isinstance(keys, list):
                expect = NAMING_KEYS[grammar]
                if tuple(keys) != expect:
                    _err(errors, name, f"{p}.keys",
                         f"{grammar} 的观测键必须依次是 {list(expect)}")
            for banned in ("value", "allowed"):
                if banned in par:
                    _err(errors, name, f"{p}.{banned}",
                         "命名参数靠实测尺寸反推，不设数值区间")
            tol = par.get("tol", 0.011)
            if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol <= 0:
                _err(errors, name, f"{p}.tol", "命名比对容差应为正数")
            if "groups" in par:
                _err(errors, name, f"{p}.groups", "命名参数必须写扁平形状（单形态）")
        else:
            _require(errors, name, p, par, "allowed", list)
            allowed = par.get("allowed")
            allowed_ok = False
            if isinstance(allowed, list):
                if len(allowed) != 2 or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                for x in allowed):
                    _err(errors, name, f"{p}.allowed", "应为 [下限, 上限] 两个数字")
                elif allowed[0] > allowed[1]:
                    _err(errors, name, f"{p}.allowed", "下限大于上限")
                else:
                    allowed_ok = True
            eps = par.get("eps")
            if eps is not None and (not isinstance(eps, (int, float)) or isinstance(eps, bool) or eps < 0):
                _err(errors, name, f"{p}.eps", "应为非负数字")
            warn = par.get("warn")
            if warn is not None:
                if (not isinstance(warn, list) or len(warn) != 2
                        or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                   for x in warn)):
                    _err(errors, name, f"{p}.warn", "应为 [警告下限, 警告上限] 两个数字")
                elif warn[0] > warn[1]:
                    _err(errors, name, f"{p}.warn", "警告带下限大于上限")
                elif (isinstance(allowed, list) and len(allowed) == 2
                      and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                              for x in allowed)
                      and not (allowed[0] <= warn[0] <= warn[1] <= allowed[1])):
                    _err(errors, name, f"{p}.warn",
                         f"警告带 {warn} 必须落在 allowed {allowed} 内")

        form = par.get("form")
        if isinstance(form, str) and form not in form_ids:
            _err(errors, name, f"{p}.form", f"引用了不存在的形态 {form!r}（参数 {who}）")
        src = par.get("source")
        if isinstance(src, str) and src not in source_ids:
            _err(errors, name, f"{p}.source", f"引用了不存在的出处 {src!r}（参数 {who}）")
        if isinstance(par.get("support"), int) and par["support"] < 0:
            _err(errors, name, f"{p}.support", "支持件数不能为负")

        if kind == "naming":
            continue

        grouped = "groups" in par
        flat = sorted(set(par) & {"form", "value", "support", "source"})
        if grouped and flat:
            _err(errors, name, f"{p}.{flat[0]}",
                 f"groups 与扁平字段 {flat} 不能同时出现（参数 {who}）："
                 "单形态参数写扁平形式，多形态参数写 groups")
        elif not grouped and not flat:
            _err(errors, name, f"{p}.groups",
                 f"缺 groups（多形态）或 form/value/support/source（单形态）（参数 {who}）")
        elif not grouped:
            for key, typ in (("form", str), ("value", (int, float)),
                             ("support", int), ("source", str)):
                _require(errors, name, p, par, key, typ)
            val = par.get("value")
            if allowed_ok and not _range_ok(allowed, val):
                _err(errors, name, f"{p}.value",
                     f"value {val} 不在 allowed {allowed} 内（参数 {who}）")
            dval = par.get("derive_value")
            if dval is not None:
                if not isinstance(dval, (int, float)) or isinstance(dval, bool):
                    _err(errors, name, f"{p}.derive_value", "derive_value 应为数字")
                elif allowed_ok and not _range_ok(allowed, dval):
                    _err(errors, name, f"{p}.derive_value",
                         f"derive_value {dval} 不在 allowed {allowed} 内（参数 {who}）")
        else:
            groups = par.get("groups")
            if not isinstance(groups, list) or not groups:
                _err(errors, name, f"{p}.groups", f"groups 应为非空数组（参数 {who}）")
                continue
            seen_forms = set()
            for j, grp in enumerate(groups):
                gp = f"{p}.groups[{j}]"
                if not isinstance(grp, dict):
                    _err(errors, name, gp, "形态组必须是对象")
                    continue
                extra = set(grp) - GROUP_FIELDS
                if extra:
                    _err(errors, name, f"{gp}.{sorted(extra)[0]}",
                         f"未声明字段 {sorted(extra)}（形态组只许 "
                         f"{'/'.join(sorted(GROUP_FIELDS))}）")
                for key, typ in (("form", str), ("value", (int, float)),
                                 ("support", int), ("source", str)):
                    _require(errors, name, gp, grp, key, typ, owner=who)
                form = grp.get("form")
                if isinstance(form, str):
                    if form not in form_ids:
                        _err(errors, name, f"{gp}.form",
                             f"引用了不存在的形态 {form!r}（参数 {who}）")
                    if form in seen_forms:
                        _err(errors, name, f"{gp}.form",
                             f"形态 {form!r} 在同一参数里出现两次（参数 {who}）")
                    seen_forms.add(form)
                src = grp.get("source")
                if isinstance(src, str) and src not in source_ids:
                    _err(errors, name, f"{gp}.source",
                         f"引用了不存在的出处 {src!r}（参数 {who}）")
                if isinstance(grp.get("support"), int) and grp["support"] < 0:
                    _err(errors, name, f"{gp}.support", "支持件数不能为负")
                val = grp.get("value")
                if allowed_ok and not _range_ok(allowed, val):
                    _err(errors, name, f"{gp}.value",
                         f"value {val} 不在 allowed {allowed} 内（参数 {who}，形态 {form}）")
                dval = grp.get("derive_value")
                if dval is not None:
                    if not isinstance(dval, (int, float)) or isinstance(dval, bool):
                        _err(errors, name, f"{gp}.derive_value", "derive_value 应为数字")
                    elif allowed_ok and not _range_ok(allowed, dval):
                        _err(errors, name, f"{gp}.derive_value",
                             f"derive_value {dval} 不在 allowed {allowed} 内"
                             f"（参数 {who}，形态 {form}）")


def _check_field_form_unique(params: List[Dict[str, Any]],
                             errors: List[Dict[str, str]]) -> None:
    """同一 (field, form) 只许一个参数定义 —— 防「同一个数字两份拷贝」复活。

    票 #19 的教训：阻焊外扩曾经三处独立定义（TH 长圆 / TH 圆 / SMD 各一条参数，
    派生侧还各读各的）。合并后若有人再建一条同 field 同 form 的参数，就是第二份
    真源，加载阶段直接拒。
    """
    name = SCHEMA_FILES["parameters"]
    seen: Dict[Tuple[str, str], str] = {}
    for i, par in enumerate(params):
        field = par.get("field")
        if not isinstance(field, str) or not field:
            continue
        for grp in parameter_groups(par):
            form = grp.get("form") if isinstance(grp, dict) else None
            if not isinstance(form, str):
                continue
            key = (field, form)
            pid = str(par.get("id"))
            if key in seen:
                _err(errors, name, f"items[{i}].field",
                     f"{field}@{form} 已被参数 {seen[key]} 定义"
                     "（一个参数一处定义，不许第二份拷贝）")
            else:
                seen[key] = pid


def _validate_rules(rules: List[Dict[str, Any]], forms: List[Dict[str, Any]],
                    params: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    severity: List[Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    name = SCHEMA_FILES["rules"]
    param_ids = {q.get("id") for q in params}
    source_ids = {s.get("id") for s in sources}
    sev_ids = {s.get("id") for s in severity}
    for i, rule in enumerate(rules):
        p = f"items[{i}]"
        # 票 #20：assert 与 derive 至少一侧（ADR D2：规则可以只有一侧）。
        for key in ("name", "use", "severity", "source", "since"):
            _require(errors, name, p, rule, key, str if key != "use" else list)
        if "assert" not in rule and "derive" not in rule:
            _err(errors, name, p, "规则至少要有 assert 或 derive 一侧")
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
                elif kind == "naming":
                    used = [q for q in params
                            if q.get("id") in (use or []) and q.get("kind") == "naming"]
                    if not used:
                        _err(errors, name, f"{ap}.kind",
                             "naming 断言必须绑定 kind=naming 的参数")
        sev = rule.get("severity")
        if isinstance(sev, str) and sev not in sev_ids:
            _err(errors, name, f"{p}.severity", f"引用了不存在的 severity {sev!r}")
        src = rule.get("source")
        if isinstance(src, str) and src not in source_ids:
            _err(errors, name, f"{p}.source", f"引用了不存在的出处 {src!r}")
        derive = rule.get("derive")
        if derive is not None:
            _check_derive_object(errors, name, f"{p}.derive", derive, rule, params)
        form = rule.get("form")
        if form is not None and form not in {f.get("id") for f in forms}:
            _err(errors, name, f"{p}.form", f"引用了不存在的形态 {form!r}")
        report_id = rule.get("report_id")
        if report_id is not None:
            if not isinstance(report_id, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}[a-z]?", report_id):
                _err(errors, name, f"{p}.report_id",
                     f"报告编号应形如 2.8（数字点分，可带小写后缀），实际 {report_id!r}")
        exempt = rule.get("exempt")
        if exempt is not None:
            if not isinstance(exempt, dict):
                _err(errors, name, f"{p}.exempt", "exempt 必须是对象")
            else:
                extra = set(exempt) - {"name_prefix", "reason"}
                if extra:
                    _err(errors, name, f"{p}.exempt.{sorted(extra)[0]}",
                         f"exempt 不认识的字段 {sorted(extra)}")
                for key in ("name_prefix", "reason"):
                    _require(errors, name, f"{p}.exempt", exempt, key, str)


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

    files: Dict[str, Any] = {}
    hashes: Dict[str, str] = {}
    meta, meta_hash = _read_meta_file(directory, errors)
    files["library"] = meta
    hashes.update(meta_hash)
    for schema_key in SCHEMA_FILES:
        if schema_key in META_SCHEMA_KEYS:
            continue
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
            if file_key in META_SCHEMA_KEYS:
                continue
            known = KNOWN_FIELDS[file_key]
            for i, obj in enumerate(items):
                extra = set(obj) - known
                if extra:
                    _err(errors, SCHEMA_FILES[file_key], f"items[{i}].{sorted(extra)[0]}",
                         f"未声明字段 {sorted(extra)}")
        _check_duplicate_ids({k: v for k, v in files.items()
                              if k not in META_SCHEMA_KEYS}, errors)
        _validate_forms(files["forms"], errors)
        _validate_sources(files["sources"], errors)
        _validate_severity(files["severity"], errors)
        _validate_parameters(files["parameters"], files["forms"], files["sources"], errors)
        _check_field_form_unique(files["parameters"], errors)
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
# 覆盖缺口（票 #21；ADR-0002 D11 / D9）
# ============================================================
def rule_directions(rule: Dict[str, Any]) -> Dict[str, bool]:
    """一条规则有哪些方向：assert（判定）/ derive（派生）；可同时存在。"""
    return {"assert": bool(rule.get("assert")), "derive": rule.get("derive") is not None}


def rule_forms(lib: RuleLibrary, rule: Dict[str, Any]) -> List[str]:
    """规则作用于哪些形态：显式 form 优先，否则由 use → 参数的 form 推出来。

    显式 form 是「这条规则只服务这个形态」的强声明（票 #20：派生规则必须写
    显式 form，否则会借 use 里的多形态参数漂到别的形态去）。没有显式 form 的
    规则（2.2/2.8/2.9/2.10 的判定侧）继续按参数组推形态，覆盖率口径不变。
    """
    form = rule.get("form")
    if isinstance(form, str):
        return [form]
    out: List[str] = []
    for pid in rule.get("use") or []:
        par = lib.parameters_by_id.get(pid) or {}
        for grp in parameter_groups(par):
            fid = grp.get("form")
            if isinstance(fid, str) and fid not in out:
                out.append(fid)
    return out


def rule_index(lib: RuleLibrary) -> Dict[str, Dict[str, List[str]]]:
    """形态 → {assert: [规则 id], derive: [规则 id]}；按库内顺序、去重。

    按库缓存：库加载后不可变，反复算覆盖率不必重建索引。
    """
    cached = getattr(lib, "_rule_index", None)
    if cached is not None:
        return cached
    index: Dict[str, Dict[str, List[str]]] = {}
    for form in lib.forms:
        fid = form.get("id")
        if isinstance(fid, str):
            index[fid] = {"assert": [], "derive": []}
    for rule in lib.rules:
        rid = rule.get("id")
        if not isinstance(rid, str):
            continue
        directions = rule_directions(rule)
        for fid in rule_forms(lib, rule):
            slot = index.get(fid)
            if slot is None:
                continue
            for direction in DIRECTIONS:
                if directions[direction] and rid not in slot[direction]:
                    slot[direction].append(rid)
    lib._rule_index = index
    return index


def _form_status(entry: Dict[str, List[str]]) -> str:
    """covered / assert_only / derive_only / uncovered —— 一侧有规则不算「有规则」。"""
    has_assert, has_derive = bool(entry["assert"]), bool(entry["derive"])
    if has_assert and has_derive:
        return "covered"
    if has_assert:
        return "assert_only"
    if has_derive:
        return "derive_only"
    return "uncovered"


COVERAGE_SUPPORT_FIELD = "mask_expand_side"


def _form_param_support(lib: RuleLibrary, form_id: str, rule_ids: List[str]) -> Optional[int]:
    """该形态的焊盘支撑件数；没有规则 → None。

    覆盖率的「支撑（焊盘）」按 README 口径取阻焊外扩参数（22 / 9 / 195），
    不取全形态参数最大值——2.8/2.9/2.10 迁移后同一形态有多条参数，
    环宽 16、命名 23、钢网 25 是另外的样本口径，混在一起会顶掉阻焊的数。
    形态没有阻焊参数时退回「所有参数 support 的最大值」，保持通用。
    """
    views: List[Dict[str, Any]] = []
    for rule_id in rule_ids:
        rule = lib.rules_by_id.get(rule_id) or {}
        for pid in rule.get("use") or []:
            par = lib.parameters_by_id.get(pid)
            if par is None:
                continue
            view = resolve_parameter(par, form_id)
            if view is not None:
                views.append(view)
    for view in views:
        if view.get("field") == COVERAGE_SUPPORT_FIELD:
            support = view.get("support")
            if isinstance(support, int) and not isinstance(support, bool):
                return support
    best: Optional[int] = None
    for view in views:
        support = view.get("support")
        if isinstance(support, int) and not isinstance(support, bool):
            if best is None or support > best:
                best = support
    return best


def _subject_signature(subject: Dict[str, Any]) -> str:
    shape = _subject_hole_shape(subject)
    return f"mount={subject.get('mount') or '?'}; hole_shape={shape or '—'}"


def _no_form_reason(subject: Dict[str, Any]) -> str:
    mount = subject.get("mount")
    shape = _subject_hole_shape(subject)
    if mount not in ("tht", "smd"):
        return f"安装方式 {mount!r} 不在形态定义里"
    if mount == "tht" and shape is None:
        return "缺孔形：通孔引脚没读到 drill.shape"
    if shape is not None:
        return f"孔形 {shape!r} 还没进形态表（先定义形态，再补规则）"
    return "没有可判定的形态事实"


def coverage(lib: RuleLibrary, subjects: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """本次输入 × 规则库的覆盖率（纯函数）。

    :param lib: load_library() 的返回值
    :param subjects: 本次输入的 subject 列表（judge 的 subject 同形状）
    :return: {schema, rule_set_version, rule_set_hash, total, forms[], gaps{}}
             gaps 里两类缺口分开列：forms_without_rule（静态差集）与
             inputs_without_form（运行时才知道）。
    """
    index = rule_index(lib)
    hits: Dict[str, List[str]] = {fid: [] for fid in index}
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    total = 0
    for i, subject in enumerate(subjects or []):
        total += 1
        sid = subject.get("id")
        subject_id = sid if isinstance(sid, str) and sid else str(i)
        form = match_form(lib, subject)
        fid = form.get("id") if isinstance(form, dict) else None
        if fid in hits:
            hits[fid].append(subject_id)
            continue
        signature = _subject_signature(subject)
        if signature not in grouped:
            grouped[signature] = {"signature": signature,
                                 "reason": _no_form_reason(subject),
                                 "count": 0, "subjects": []}
            order.append(signature)
        rec = grouped[signature]
        rec["count"] += 1
        if len(rec["subjects"]) < 5:
            rec["subjects"].append(subject_id)

    forms_out: List[Dict[str, Any]] = []
    for form in lib.forms:
        fid = form.get("id")
        if not isinstance(fid, str):
            continue
        entry = index[fid]
        forms_out.append({
            "form": fid,
            "name": form.get("name") or "",
            "status": _form_status(entry),
            "assert_rules": list(entry["assert"]),
            "derive_rules": list(entry["derive"]),
            "hits": len(hits.get(fid, [])),
            "subjects": hits.get(fid, [])[:8],
            "support": _form_param_support(lib, fid, entry["assert"]),
        })
    matched = sum(rec["hits"] for rec in forms_out)
    return {
        "schema": COVERAGE_SCHEMA,
        "rule_set_version": lib.version,
        "rule_set_hash": lib.fingerprint,
        "total": {"subjects": total, "matched": matched, "unmatched": total - matched},
        "forms": forms_out,
        "gaps": {
            "forms_without_rule": [rec["form"] for rec in forms_out if not rec["assert_rules"]],
            "inputs_without_form": [grouped[sig] for sig in order],
        },
    }


def rule_set_info(lib: RuleLibrary) -> Dict[str, Any]:
    """报告首屏/JSON 用的规则集标识：版本 + 哈希 + 各对象件数（ADR-0002 D9）。"""
    return {
        "schema": SCHEMA_VERSION,
        "version": lib.version,
        "released_at": lib.library.get("released_at"),
        "hash": lib.fingerprint,
        "counts": {
            "forms": len(lib.forms),
            "parameters": len(lib.parameters),
            "rules": len(lib.rules),
            "sources": len(lib.sources),
            "severity": len(lib.severity),
        },
    }


# ============================================================
# 判定
# ============================================================
def _skip(rule_id: str, detail: str, form: Optional[str] = None,
          subject_id: Optional[str] = None, lib: RuleLibrary = None,
          report_id: Optional[str] = None,
          direction: Optional[str] = None) -> Dict[str, Any]:
    """SKIP finding：不是通过。方向/版本/哈希与正常 finding 同口径带上。"""
    out: Dict[str, Any] = {
        "status": "SKIP",
        "rule_id": rule_id,
        "report_id": report_id,
        "direction": direction or "assert",
        "form": form,
        "subject": subject_id,
        "severity": "SKIP",
        "detail": detail,
    }
    if lib is not None:
        out["rule_set_hash"] = lib.fingerprint
        out["rule_set_version"] = lib.version
    return out


def _value_in(value: float, lo: float, hi: float, eps: float) -> bool:
    return (lo - eps) <= value <= (hi + eps)


def _dnum(text: str) -> float:
    return float(str(text).replace("D", "."))


def _naming_match(grammar: str, name: str, hole: List[float], pad: List[float],
                  tol: float = 0.011) -> Tuple[bool, str]:
    """尺寸反推：解析名字里的数字，与实测孔/盘尺寸逐项比对（ADR D6）。

    保留旧 naming_match_th 的口径：长轴在前、两位小数、容差 0.011，允许
    NP 前缀、OB 省略、H 后 OB/O 省略、OS{X|Y}{偏移} 后缀、圆孔 PC..HC..。
    不解析模板，只做「名字 ↔ 实测」一致性判断。
    """
    base = re.sub(r"OS[XY][0-9D.]+$", "", str(name).upper())
    hole_s = sorted([round(float(v), 2) for v in hole if float(v) > 0], reverse=True)
    pad_s = sorted([round(float(v), 2) for v in pad if float(v) > 0], reverse=True)
    if not hole_s:
        return False, f"名字 {name} 无法反推：孔尺寸未知"

    def eq(a: float, b: float) -> bool:
        return abs(a - b) <= tol

    if grammar == "TH-OBLONG":
        m = re.match(r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)H(?:OB|O)?([0-9D]+)X([0-9D]+)", base)
        if m:
            want = [_dnum(m.group(i)) for i in (2, 3, 4, 5)]
            if len(hole_s) < 2 or len(pad_s) < 2:
                return False, f"名字 {name} 含四段尺寸，但实测孔/盘不足两轴"
            if all(eq(a, b) for a, b in zip(want, hole_s + pad_s)):
                return True, ""
            return False, (f"名字 {name} 的数字 {want} 与实测孔 {hole_s} / 盘 {pad_s} 不符")
        m = re.match(r"(NP)?P?(?:OB)?([0-9D]+)X([0-9D]+)", base)
        if m:
            want = [_dnum(m.group(i)) for i in (2, 3)]
            plated = not base.startswith("NP")
            if len(hole_s) < 2 or not all(eq(a, b) for a, b in zip(want, hole_s)):
                return False, f"名字 {name} 的孔尺寸 {want} 与实测 {hole_s} 不符"
            if plated:
                return False, f"名字 {name} 省略了盘尺寸，只有非金属化（NP）件允许省略"
            return True, ""
        return False, f"名字 {name} 不合 TH-OBLONG 命名语法（POB{{孔长}}X{{孔短}}HOB{{盘长}}X{{盘短}}）"

    if grammar == "TH-CIRCLE":
        m = re.match(r"(NP)?P?C([0-9D]+)H(?:OB|C)?([0-9D]+)", base)
        if m:
            want = [_dnum(m.group(2)), _dnum(m.group(3))]
            got = [hole_s[0], pad_s[0]] if pad_s else [hole_s[0], 0.0]
            if all(eq(a, b) for a, b in zip(want, got)):
                return True, ""
            return False, f"名字 {name} 的数字 {want} 与实测孔 {got[0]} / 盘 {got[1]} 不符"
        return False, f"名字 {name} 不合 TH-CIRCLE 命名语法（PC{{孔}}HC{{盘}}）"

    return False, f"未知命名语法 {grammar!r}"


def _eval_naming(param: Dict[str, Any], observed: Dict[str, Any]) -> Tuple[bool, str]:
    grammar = str(param.get("grammar") or "")
    tol = float(param.get("tol", 0.011))
    name = str(observed.get("name") or "")
    if grammar == "TH-OBLONG":
        hole = [observed["hole_long"], observed["hole_short"]]
        pad = [observed["pad_long"], observed["pad_short"]]
    else:
        hole = [observed["hole_dia"]]
        pad = [observed["pad_dia"]]
    return _naming_match(grammar, name, hole, pad, tol)


def _eval_assert(assertion: Dict[str, Any], param: Dict[str, Any],
                 observed: Dict[str, Any]) -> Tuple[bool, str]:
    """跑一条断言；返回 (是否满足, 不满足时的说明)。"""
    kind = assertion.get("kind")
    if kind == "naming":
        return _eval_naming(param, observed)
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


def parameter_groups(param: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把一个参数展开成「形态组」列表（扁平单形态参数折成一组）。

    参数有两种形状（见 parameters@2）：多形态写 groups[]，单形态写扁平的
    form/value/support/source。调用方只想问「这个参数管哪几个形态、各组的典型值
    是多少」，不该关心形状差异 —— 这一层归一化就干这个。
    """
    groups = param.get("groups")
    if isinstance(groups, list):
        return [g for g in groups if isinstance(g, dict)]
    if "form" in param:
        grp = {"form": param.get("form"),
               "value": param.get("value"),
               "support": param.get("support"),
               "source": param.get("source")}
        if param.get("derive_value") is not None:
            grp["derive_value"] = param.get("derive_value")
        return [grp]
    return []


def param_covers_form(param: Dict[str, Any], form_id: Optional[str]) -> bool:
    """这个参数是否为该形态给过值。"""
    if form_id is None:
        return False
    return any(g.get("form") == form_id for g in parameter_groups(param))


def resolve_parameter(param: Dict[str, Any],
                      form_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """按形态解析参数：返回「参数级字段 + 该形态组的值」的合并视图。

    合并视图是判定与派生共用的形状：allowed / eps / keys 来自参数（区间对所有
    形态相同），value / support / source 来自形态组（不同形态取不同典型值）。
    该形态没有组 → None（调用方据此报 SKIP 或拒绝猜值，不许回落到别的形态）。
    """
    if form_id is None:
        return None
    for grp in parameter_groups(param):
        if grp.get("form") != form_id:
            continue
        view = dict(param)
        for key in ("groups", "form", "value", "derive_value", "support", "source"):
            view.pop(key, None)
        view["id"] = param.get("id")
        view["form"] = form_id
        view["value"] = grp.get("value")
        # 建库口径：派生侧用 derive_value（没声明就回落到典型值 value）。
        # 典型值 value 仍是语料事实（SMD 组 0.08），两者语义不同（票 #20）。
        _dv = grp.get("derive_value")
        view["derive_value"] = _dv if _dv is not None else grp.get("value")
        view["support"] = grp.get("support")
        view["source"] = grp.get("source")
        if grp.get("note"):
            view["group_note"] = grp.get("note")
        return view
    return None


# ============================================================
# 派生（derive）求值（票 #20；ADR-0002 D2 / D5 / D6 / D7）
# ============================================================
class DeriveError(Exception):
    """派生求值失败：缺 form / 缺参数组 / 缺事实 / 表达式不合法。

    message 是给人看的短句（进 SKIP 的 reason），rule_id / form 用于追规则。
    """

    def __init__(self, message: str, rule_id: Optional[str] = None,
                 form: Optional[str] = None, kind: str = "derive"):
        super().__init__(message)
        self.message = message
        self.rule_id = rule_id
        self.form = form
        self.kind = kind


def expr_names(expr: Any) -> List[str]:
    """表达式里引用到的名字（给决策记录算依赖、给缺事实报错用）。"""
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError:
        return []
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})


def eval_expr(expr: Any, env: Dict[str, Any]) -> float:
    """求一个派生表达式：数字 / 名字 / 四则运算，返回数字。

    白名单与加载校验（_check_derive_expr）一致；这里额外拒绝非数字名字
    （布尔不是数字，避免 plated 被当尺寸算）。
    """
    text = str(expr)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise DeriveError(f"派生表达式语法错误 {text!r}: {exc.msg}") from exc
    return _eval_derive_node(tree.body, env, text)


def _eval_derive_node(node: ast.AST, env: Dict[str, Any], text: str) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value  # type: ignore[return-value]
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise DeriveError(f"派生表达式 {text!r} 引用了没有的名字 {node.id!r}")
        val = env[node.id]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise DeriveError(f"派生表达式 {text!r} 引用的 {node.id!r} 不是数字（{val!r}）")
        return val
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _eval_derive_node(node.operand, env, text)
        return val if isinstance(node.op, ast.UAdd) else -val
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _eval_derive_node(node.left, env, text)
        right = _eval_derive_node(node.right, env, text)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise DeriveError(f"派生表达式 {text!r} 除以零")
        return left / right
    raise DeriveError(f"派生表达式 {text!r} 含不允许的写法（只许数字/名字/四则运算）")


class _EnvMap(dict):
    """{占位符} 渲染：只做字符串替换，不是 format 迷你语言（ADR D6）。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_note(template: str, env: Dict[str, Any]) -> str:
    """把备注模板里的 {名字} 换成人能读的字符串（数字用 Python 原样 repr）。"""
    view = _EnvMap()
    for key, val in env.items():
        if val is None:
            continue
        view[key] = str(val)
    return str(template).format_map(view)


def _decision(item: str, value: Any, expression: Optional[str],
              rule: Dict[str, Any], form_id: Optional[str],
              param_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """一条派生决策记录：算出什么、用什么表达式、依据哪条规则与哪些参数。"""
    used = []
    for name in expr_names(expression) if expression is not None else []:
        ref = param_index.get(name)
        if ref is not None and ref not in used:
            used.append(ref)
    return {
        "item": item,
        "value": value,
        "expression": expression,
        "rule_id": rule.get("id"),
        "source": rule.get("source"),
        "form": form_id,
        "parameters": used,
    }


def _require_names(expr: Any, env: Dict[str, Any], what: str,
                   rule_id: Optional[str], form_id: Optional[str]) -> None:
    missing = [n for n in expr_names(expr) if n not in env]
    if missing:
        raise DeriveError(f"{what} 缺事实/参数 {'/'.join(missing)}",
                          rule_id=rule_id, form=form_id)


def resolve_derive(lib: RuleLibrary, rule: Dict[str, Any], form_id: Optional[str],
                   facts: Dict[str, Any]) -> Dict[str, Any]:
    """把一条派生规则在给定事实下展开成 padstack 定义（纯函数）。

    返回 dict：
      values      计算出来的中间量（规则声明顺序）
      layers      [{layer, figure, size}]（尺寸已 round 4）
      drill       None 或 {size, drill_type, figure, plated?, offset?}
      name        命名规格（模板与占位符值；格式化在代码侧，ADR D6）
      rotation    旋转角度（度）或 None
      notes       渲染好的备注
      parameters  这条规则用到的参数（id/值/出处）
      decisions   逐项决策记录（规则 id + 参数值 + 出处）

    失败抛 DeriveError（缺 form / 缺参数组 / 缺事实 / 表达式不合法）。
    """
    rid = rule.get("id")
    derive = rule.get("derive")
    if not isinstance(derive, dict):
        raise DeriveError("该规则只有 assert，没有 derive 一侧", rule_id=rid, form=form_id)
    if form_id is None:
        raise DeriveError("缺 form：读数不匹配任何形态", rule_id=rid)

    env: Dict[str, Any] = {}
    for key, val in (facts or {}).items():
        if val is not None:
            env[key] = val

    parameters: List[Dict[str, Any]] = []
    param_index: Dict[str, Dict[str, Any]] = {}
    for pid in rule.get("use") or []:
        par = lib.parameters_by_id.get(pid)
        if par is None:
            raise DeriveError(f"引用了不存在的参数 {pid}", rule_id=rid, form=form_id)
        if par.get("kind") == "naming":
            continue  # 名字由规则里的模板决定；命名参数只服务判定侧反推
        view = resolve_parameter(par, form_id)
        if view is None:
            raise DeriveError(f"参数 {pid} 没给形态 {form_id} 的值",
                              rule_id=rid, form=form_id)
        value = view.get("derive_value")
        parameters.append({"id": pid, "value": value, "source": view.get("source"),
                           "form": form_id})
        for key in par.get("keys") or []:
            env[key] = value
            param_index[key] = {"id": pid, "key": key, "value": value,
                                "source": view.get("source")}

    decisions: List[Dict[str, Any]] = []

    values: Dict[str, Any] = {}
    for name, expr in (derive.get("values") or {}).items():
        _require_names(expr, env, f"派生值 {name}", rid, form_id)
        val = eval_expr(expr, env)
        values[name] = val
        env[name] = val
        decisions.append(_decision(f"values.{name}", val, str(expr), rule, form_id,
                                   param_index))

    layers: List[Dict[str, Any]] = []
    for i, layer in enumerate(derive.get("layers") or []):
        when = layer.get("when")
        if isinstance(when, dict):
            # 条件层：事实缺省按 False 处理（读数没声明 = 不要这层）
            actual = bool(env.get(when.get("fact")))
            if actual != bool(when.get("equals")):
                continue
        label = layer.get("layer") or i
        sizes: List[Any] = []
        for j, expr in enumerate(layer.get("size") or []):
            _require_names(expr, env, f"层 {label} 尺寸", rid, form_id)
            val = round(eval_expr(expr, env), 4)
            sizes.append(val)
            decisions.append(_decision(f"layers[{i}].size[{j}]", val, str(expr), rule,
                                       form_id, param_index))
        figure = layer.get("figure")
        if isinstance(figure, dict):
            fname = figure.get("from_fact")
            if fname not in env:
                raise DeriveError(f"层的 figure 引用了没有的事实 {fname!r}",
                                  rule_id=rid, form=form_id)
            figure = env[fname]
            decisions.append(_decision(f"layers[{i}].figure", figure, None, rule,
                                       form_id, param_index))
        layers.append({"layer": layer.get("layer"), "figure": figure, "size": sizes})

    drill = derive.get("drill")
    out_drill: Optional[Dict[str, Any]] = None
    if drill is not None:
        dsz: List[Any] = []
        for j, expr in enumerate(drill.get("size") or []):
            _require_names(expr, env, "钻孔尺寸", rid, form_id)
            val = round(eval_expr(expr, env), 4)
            dsz.append(val)
            decisions.append(_decision(f"drill.size[{j}]", val, str(expr), rule,
                                       form_id, param_index))
        out_drill = {"size": dsz,
                     "drill_type": drill.get("drill_type"),
                     "figure": drill.get("figure")}
        plated_from = drill.get("plated_from")
        if plated_from is not None:
            if plated_from not in env:
                raise DeriveError(f"plated 引用了没有的事实 {plated_from!r}",
                                  rule_id=rid, form=form_id)
            out_drill["plated"] = bool(env[plated_from])
            decisions.append(_decision("drill.plated", out_drill["plated"], None, rule,
                                       form_id, param_index))
        offset = drill.get("offset")
        if offset:
            ox, oy = offset
            if ox in env and oy in env:
                out_drill["offset"] = [env[ox], env[oy]]
                decisions.append(_decision("drill.offset", out_drill["offset"], None,
                                           rule, form_id, param_index))

    name_rule = derive.get("name") or {}
    style = name_rule.get("style")
    name_spec: Dict[str, Any] = {"style": style}
    if style == "th_oblong":
        template = str(name_rule.get("template") or "")
        holders = _template_placeholders(template)
        missing = [h for h in holders if h not in env]
        if missing:
            raise DeriveError(f"命名模板缺少 {missing}", rule_id=rid, form=form_id)
        name_spec["template"] = template
        name_spec["values"] = {h: env[h] for h in holders}
    elif style == "padstack_name":
        shape_name = name_rule.get("shape")
        if shape_name not in env:
            raise DeriveError(f"命名缺少事实 {shape_name!r}", rule_id=rid, form=form_id)
        dims = []
        for d in name_rule.get("dims") or []:
            if d not in env:
                raise DeriveError(f"命名缺少事实 {d!r}", rule_id=rid, form=form_id)
            dims.append(env[d])
        name_spec["shape"] = env[shape_name]
        name_spec["dims"] = dims
    else:
        raise DeriveError(f"未知命名方式 {style!r}", rule_id=rid, form=form_id)

    # 命名也是派生值（ADR D5 的四件事之一）：记下它由哪条规则、经哪些派生值、
    # 最终落到哪些参数。name 决策的 parameters 是「命名引用到的派生值」所用参数的并集，
    # 这样从名字能一路追回参数（values.X 的决策里就有参数）。
    refs: List[str] = []
    if style == "th_oblong":
        refs = list(name_spec.get("values") or {})
    else:
        refs = [str(name_spec.get("shape"))] + [str(d) for d in name_spec.get("dims") or []]
    name_params: List[Dict[str, Any]] = []
    for ref in refs:
        for dec in decisions:
            if dec.get("item") != f"values.{ref}":
                continue
            for prm in dec.get("parameters") or []:
                if prm not in name_params:
                    name_params.append(prm)
    name_value = (name_spec.get("template") if style == "th_oblong"
                  else padstack_naming.padstack_name(name_spec.get("dims") or [],
                                                     name_spec.get("shape") or "rect"))
    name_dec = _decision("name", name_value, None, rule, form_id, param_index)
    name_dec["parameters"] = name_params
    decisions.append(name_dec)

    rotation = derive.get("rotation")
    if rotation is not None:
        decisions.append(_decision("rotation", rotation, None, rule, form_id, param_index))

    notes = [render_note(t, env) for t in (derive.get("notes") or [])]

    return {
        "values": values,
        "layers": layers,
        "drill": out_drill,
        "name": name_spec,
        "rotation": rotation,
        "notes": notes,
        "parameters": parameters,
        "decisions": decisions,
    }


def judge(lib: RuleLibrary, rule_id: str, subject: Dict[str, Any],
          observed: Dict[str, Any]) -> Dict[str, Any]:
    """单条规则在一个 subject 上判定，纯函数。

    :param lib: load_library() 的返回值
    :param rule_id: rules.json 里的规则 id
    :param subject: 事实（mount / layers / drill / name / pad_count）
    :param observed: 观测值（按参数 keys 取值，缺键即 UNKNOWN）
    :return: finding dict（status / rule_id / report_id / form / direction /
             severity / source / support / rule_set_hash / rule_set_version / detail …）；
             三态之一，SKIP 也带 rule_id、方向与规则集标识。
    """
    subject_id = subject.get("id")
    rule = lib.rules_by_id.get(rule_id)
    if rule is None:
        return _skip(rule_id, f"缺规则：规则库里没有 {rule_id}", lib=lib)

    form = match_form(lib, subject)
    if form is None:
        return _skip(rule_id, f"缺 form：{subject_id} 没有匹配的形态",
                     subject_id=subject_id, lib=lib,
                     report_id=rule.get("report_id"))
    form_id = form.get("id")

    param = None
    for pid in rule.get("use", []):
        candidate = lib.parameters_by_id.get(pid)
        if candidate is None:
            continue
        resolved = resolve_parameter(candidate, form_id)
        if resolved is not None:
            param = resolved
            break
    if param is None:
        # 形态完全没有判定规则，与「规则不绑定这个形态的参数」是两类缺口，分开报。
        if not (rule_index(lib).get(form_id) or {}).get("assert"):
            return _skip(rule_id,
                         f"缺规则：形态 {form_id} 尚无判定规则（覆盖率缺口，不判 PASS）",
                         form=form_id, subject_id=subject_id, lib=lib,
                         report_id=rule.get("report_id"))
        return _skip(rule_id, f"缺参数：规则 {rule_id} 未对形态 {form_id} 声明参数",
                     form=form_id, subject_id=subject_id, lib=lib,
                     report_id=rule.get("report_id"))

    keys = param.get("keys") or []
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
        "report_id": rule.get("report_id"),
        "form": form_id,
        "subject": subject_id,
        "direction": "assert",
        "param": param.get("id"),
        "severity": None,
        "severity_ref": severity_ref,
        "source": source,
        "source_ref": source_ref,
        "support": param.get("support"),
        "rule_set_hash": lib.fingerprint,
        "rule_set_version": lib.version,
        "value": {},
        "allowed": allowed,
        "eps": eps,
        "detail": "",
    }

    # 豁免先于观测：NP 机械孔不需要环宽/钢网观测，旧路径也是直接放行。
    exempt = rule.get("exempt")
    if isinstance(exempt, dict):
        prefix = str(exempt.get("name_prefix") or "")
        sname = str(subject.get("name") or "")
        if prefix and sname.upper().startswith(prefix.upper()):
            base["status"] = "PASS"
            base["severity"] = None
            base["exempt"] = True
            base["detail"] = str(exempt.get("reason") or "")
            return base

    missing = [k for k in keys if k not in observed]
    if missing:
        return _skip(rule_id, f"输入 UNKNOWN：缺观测 {', '.join(missing)}", form=form_id,
                     subject_id=subject_id, lib=lib,
                     report_id=rule.get("report_id"))
    base["value"] = {k: observed[k] for k in keys}

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

    # 警告带：allowed 内但 warn 外 → WARN（三档判定的中间档）。
    warn = param.get("warn")
    if isinstance(warn, list) and len(warn) == 2:
        wlo, whi = float(warn[0]), float(warn[1])
        outside = [k for k in keys if not _value_in(float(observed[k]), wlo, whi, eps)]
        if outside:
            base["status"] = "WARN"
            base["severity"] = "WARN"
            base["severity_ref"] = None
            base["warn_band"] = [wlo, whi]
            base["detail"] = "非标准：" + "；".join(
                f"{k}={float(observed[k]):g} 不在标准带 [{wlo:g}, {whi:g}]"
                for k in outside)
    return base
