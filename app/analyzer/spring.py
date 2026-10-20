"""Spring Boot 后端静态解析（纯规则，零 LLM）。

关键坑（TASK_BOOK 10.2）：
- 类级 @RequestMapping 只能作为路径前缀，不能自身成为端点
- 端点必须带 file 和 line
"""
from __future__ import annotations

import os
import re

from app.analyzer.models import (
    BackendResult,
    BusinessRule,
    Endpoint,
    Entity,
    EntityField,
    EnumDef,
    EnumValue,
    FieldConstraint,
)

# 方法级映射注解
# 只匹配方法级映射注解；@RequestMapping 由 CLASS_MAPPING_RE 单独处理前缀，
# 不能混入（否则类级 @RequestMapping 会被当成端点导致前缀重复拼接）
# 括号可选：Spring 允许 @PostMapping（省略括号，path 默认空，继承类级前缀）
METHOD_MAPPING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\s*(?:\(([^)]*)\))?")
CLASS_MAPPING_RE = re.compile(r"@RequestMapping\s*\(([^)]*)\)")
CLASS_DECL_RE = re.compile(r"\b(class|interface|enum)\s+(\w+)")
METHOD_DECL_RE = re.compile(r"\b(public|private|protected)\s+[A-Za-z_<>,\.\[\]\s]+\s+(\w+)\s*\(")
FIELD_DECL_RE = re.compile(r"\bprivate\s+([A-Za-z_<>,\[\]\.\s]+?)\s+(\w+)\s*;")
THROW_RE = re.compile(r"throw\s+new\s+\w+\s*\(\s*\"([^\"]*)\"\s*\)")
# 守卫：if (...) { throw ... }，取 if 括号内容
IF_GUARD_RE = re.compile(r"\bif\s*\(([^{}]*)\)\s*(?:\{|throw)")

# 约束注解 → (rule, 值解析)；覆盖 jakarta.validation 常用注解
CONSTRAINT_RE = re.compile(
    r"@(NotBlank|NotNull|NotEmpty|Size|Max|Min|Pattern|Email|DecimalMin|DecimalMax|Digits|Positive|PositiveOrZero|Negative|Length)\s*(?:\(\s*([^)]*)\s*\))?"
)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff][\u4e00-\u9fff\w，。！？、：；""''（）-]*")

# 状态枚举识别：名字含 status/state，或取值多为大写状态词
STATUS_KEYWORDS = ("status", "state")

# 非数据模型类后缀：这些是 Controller/Service/通用包装，不该被当成"实体"
NON_ENTITY_SUFFIXES = (
    "Controller", "Service", "ServiceImpl", "Repository", "Config",
    "Application", "Handler", "Exception", "Response",
)


def _extract_path_value(annotation_args: str) -> str | None:
    """从 @XxxMapping(...) 参数里提取 path/value。"""
    # 直接字符串：@PostMapping("/create")
    m = re.search(r'"([^"]+)"', annotation_args)
    if m:
        return m.group(1)
    # value="/x" 或 path="/x"
    m = re.search(r"\b(?:value|path)\s*=\s*\"([^\"]+)\"", annotation_args)
    if m:
        return m.group(1)
    return None


def _join_path(prefix: str | None, path: str | None) -> str:
    """拼接类级前缀与方法路径，保证单斜杠分隔。"""
    if not path:
        path = ""
    if prefix:
        return ("/" + prefix.strip("/") + "/" + path.strip("/")).rstrip("/") or "/"
    return ("/" + path.strip("/")).rstrip("/") or "/"


def parse_java_file(path: str, rel: str) -> BackendResult:
    """解析单个 .java 文件，返回累积到结果（单文件粒度便于并行/测试）。"""
    result = BackendResult()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    lines = [ln.rstrip("\n") for ln in lines]

    # 1) 类级 @RequestMapping 前缀
    class_prefix: str | None = None
    class_name: str | None = None
    class_kind: str | None = None
    for i, ln in enumerate(lines):
        m = CLASS_DECL_RE.search(ln)
        if m:
            class_kind = m.group(1)
            class_name = m.group(2)
            # 向前找类级 @RequestMapping（在类声明之前最近的一个）
            for j in range(i - 1, max(i - 20, -1), -1):
                cm = CLASS_MAPPING_RE.search(lines[j])
                if cm:
                    class_prefix = _extract_path_value(cm.group(1))
                    break
            break

    # 2) 端点：方法级映射注解 + 方法名
    ep_idx = 0
    for i, ln in enumerate(lines):
        mm = METHOD_MAPPING_RE.search(ln)
        if not mm:
            continue
        method_verb = mm.group(1)
        verb = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH"}.get(method_verb, "GET")
        # 无括号时 group(2) 为 None → path 空，_join_path 只返回类级前缀
        path = _extract_path_value(mm.group(2)) if mm.group(2) else ""
        # 找本行及后续几行里的方法名
        method_name = None
        for j in range(i, min(i + 5, len(lines))):
            dm = METHOD_DECL_RE.search(lines[j])
            if dm:
                method_name = dm.group(2)
                break
        if not method_name:
            continue
        ep_idx += 1
        result.endpoints.append(
            Endpoint(
                id=f"ep-{ep_idx}",
                method=verb,
                path=_join_path(class_prefix, path),
                controller=class_name or "",
                methodName=method_name,
                file=rel,
                line=i + 1,
            )
        )

    # 3) 实体：仅 class（非 interface/enum），且排除 Controller/Service 等非数据模型
    if (
        class_kind == "class"
        and class_name
        and class_name[0].isupper()
        and not class_name.endswith(NON_ENTITY_SUFFIXES)
    ):
        entity = Entity(name=class_name, file=rel)
        # 约束用列表累积：同一字段多个注解都要保留（@NotBlank + @Size）
        pending: list[tuple[str, object]] = []
        for i, ln in enumerate(lines):
            cm = CONSTRAINT_RE.search(ln)
            if cm:
                rule = cm.group(1).lower()
                args = cm.group(2) or ""
                value = None
                if rule == "size":
                    mx = re.search(r"max\s*=\s*(\d+)", args)
                    value = int(mx.group(1)) if mx else None
                elif rule in {"max", "min"}:
                    m = re.search(r"\b(?:max|min)\s*=\s*(\d+)", args)
                    value = int(m.group(1)) if m else None
                elif rule == "pattern":
                    m = re.search(r'"([^"]+)"', args)
                    value = m.group(1) if m else None
                else:
                    # decimalmin/decimalmax/notblank/notnull 等：值取引号内容，可空
                    m = re.search(r'"([^"]+)"', args)
                    value = m.group(1) if m else None
                pending.append((rule, value))
                continue

            fm = FIELD_DECL_RE.search(ln)
            if fm:
                ftype = fm.group(1).strip()
                fname = fm.group(2)
                entity.fields.append(EntityField(name=fname, type=ftype))
                for rule, value in pending:
                    entity.fieldConstraints.append(FieldConstraint(field=fname, rule=rule, value=value))
                pending = []
                continue

            # 遇到方法/类声明才清空 pending（避免跨字段泄漏）；未知注解/注释/空行不清空
            if METHOD_DECL_RE.search(ln) or CLASS_DECL_RE.search(ln):
                pending = []

        if entity.fields:
            result.entities.append(entity)

    # 4) 枚举
    if class_kind == "enum" and class_name:
        enum_def = EnumDef(name=class_name)
        # \b 避免把构造方法名（如 OrderStatus(...)）当枚举值：枚举常量是全大写 token
        const_re = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\b\s*(?:\(([^)]*)\))?[,;]?")
        for ln in lines:
            m = const_re.match(ln)
            if m and not ln.strip().startswith(("package", "import", "//", "enum", "}", "public")):
                name = m.group(1)
                args = m.group(2) or ""
                label_m = CHINESE_RE.search(args)
                enum_def.values.append(EnumValue(name=name, label=label_m.group(0) if label_m else name))
        if enum_def.values:
            result.enums.append(enum_def)

    # 5) 业务规则：if(...) throw，取守卫 + 中文消息
    rule_idx = 0
    for i, ln in enumerate(lines):
        tm = THROW_RE.search(ln)
        if not tm:
            continue
        msg = tm.group(1)
        # 向上找同一方法内的 if 守卫（最近 15 行内）
        guard = None
        for j in range(i - 1, max(i - 15, -1), -1):
            gm = IF_GUARD_RE.search(lines[j])
            if gm:
                guard = gm.group(1).strip()
                break
        if guard is None:
            continue
        rule_idx += 1
        result.businessRules.append(
            BusinessRule(
                id=f"br-{rule_idx}",
                description=msg,
                guard=guard,
                file=rel,
                line=i + 1,
            )
        )
        if msg:
            result.uiTexts.append(msg)

    # 6) 额外 UI 文案：约束注解里的中文 message
    for i, ln in enumerate(lines):
        m = re.search(r'message\s*=\s*"([^"]*)"', ln)
        if m and re.search(r"[\u4e00-\u9fff]", m.group(1)):
            result.uiTexts.append(m.group(1))

    # 去重 uiTexts 保持顺序
    seen = set()
    result.uiTexts = [t for t in result.uiTexts if not (t in seen or seen.add(t))]

    return result


def analyze_backend(java_files: list[str], root: str) -> BackendResult:
    """汇总多个 java 文件的解析结果；端点 id 全局递增。"""
    merged = BackendResult()
    ep_counter = 0
    br_counter = 0
    for f in java_files:
        rel = os.path.relpath(f, root)
        one = parse_java_file(f, rel)
        for ep in one.endpoints:
            ep_counter += 1
            ep.id = f"ep-{ep_counter}"
            merged.endpoints.append(ep)
        for br in one.businessRules:
            br_counter += 1
            br.id = f"br-{br_counter}"
            merged.businessRules.append(br)
        merged.entities.extend(one.entities)
        merged.enums.extend(one.enums)
        merged.uiTexts.extend(one.uiTexts)

    seen = set()
    merged.uiTexts = [t for t in merged.uiTexts if not (t in seen or seen.add(t))]
    return merged
