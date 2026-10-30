"""Java AST 解析：JDK Compiler API 输出 → 现有 BackendResult 契约。

解析器本身在 scripts/java_ast_dump/JavaAstDump.java，零第三方依赖；
Python 只负责调用和把 AST 摘要映射为 Endpoint / Entity / EnumDef / BusinessRule。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

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
from app.analyzer.spring import (
    NON_ENTITY_SUFFIXES,
    PARAM_CONSTRAINT_NAMES,
    SIMPLE_PARAM_TYPES,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
JAVA_AST_SCRIPT = BASE_DIR / "scripts" / "java_ast_dump" / "JavaAstDump.java"
CHINESE_RE = re.compile(r"[\u4e00-\u9fff][\u4e00-\u9fff\w，。！？、：；（）\-]*")
AUTH_NAMES = {"PreAuthorize", "Secured", "RolesAllowed", "PermitAll", "DenyAll"}
MAPPING_NAMES = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}


JAVA_CHUNK_SIZE = 50


def _run_java(files: list[str]) -> dict:
    """分批调用，避免大仓库一次性传参超过 Windows 命令行长度上限。"""
    if not files:
        return {"units": []}
    units: list[dict] = []
    for start in range(0, len(files), JAVA_CHUNK_SIZE):
        units.extend(_run_java_once(files[start:start + JAVA_CHUNK_SIZE]))
    return {"units": units}


def _run_java_once(files: list[str]) -> list[dict]:
    java = shutil.which("java")
    if not java:
        raise RuntimeError("未找到 java，可执行文件不可用")
    if not JAVA_AST_SCRIPT.exists():
        raise RuntimeError(f"Java AST 导出器不存在: {JAVA_AST_SCRIPT}")
    proc = subprocess.run(
        [java, str(JAVA_AST_SCRIPT), *files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Java AST 解析失败: {proc.stderr[:800]}")
    return json.loads(proc.stdout).get("units") or []


def _annotations(items: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in items or []:
        name = str(item.get("name") or "").split(".")[-1]
        out.setdefault(name, []).extend(str(arg) for arg in item.get("args") or [])
    return out


def _kv(raw: str) -> tuple[str, str]:
    text = str(raw).strip()
    if "=" in text:
        key, value = text.split("=", 1)
        # 只有标识符才算命名参数，避免把路径里的 = 当成 key
        if re.fullmatch(r"[A-Za-z_]\w*", key.strip()):
            return key.strip(), value.strip().strip('"').strip("'")
    return "", text.strip('"').strip("'")


VALID_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"}


def _split_array(value: str) -> list[str]:
    """把 {"/a", "/b"} 或 /a 拆成路径列表，忽略字符串内部的逗号。"""
    text = str(value or "").strip()
    if not text:
        return []
    if text == "{}":
        return []
    # 数组字面量一定带引号（@GetMapping({"/a", "/b"})）；
    # 不带引号的 {id} 是路径变量，不能当数组拆
    if not (text.startswith("{") and text.endswith("}") and '"' in text):
        # 单个路径不拆逗号：Java 导出器已去掉字面量引号，无法区分 /a,b 与数组
        return [text.strip('"').strip("'")]
    text = text[1:-1]
    items: list[str] = []
    buf: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
            buf.append(char)
        elif char == "," and not quoted:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    if buf:
        items.append("".join(buf))
    return [item.strip().strip('"').strip("'") for item in items if item.strip()]


def _annotation_paths(annotation_args: list[str]) -> list[str]:
    """只认 value/path/无名参数。

    旧实现取第一个参数字符串，@GetMapping(name = "x", path = "/y") 会把 x 当路径。
    """
    paths: list[str] = []
    for raw in annotation_args:
        key, value = _kv(raw)
        if key and key not in ("value", "path"):
            continue
        paths.extend(_split_array(value))
    return [path for path in paths if path]


def _request_mapping_verbs(annotation_args: list[str]) -> list[str]:
    """方法级 @RequestMapping(method = RequestMethod.GET) 也要算端点。"""
    verbs: list[str] = []
    for raw in annotation_args:
        key, value = _kv(raw)
        if key != "method":
            continue
        tokens = re.findall(r"RequestMethod\.([A-Z]+)", value) or re.findall(r"\b([A-Z]+)\b", value)
        for token in tokens:
            if token in VALID_VERBS and token not in verbs:
                verbs.append(token)
    return verbs


def _method_mappings(annotations: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    mappings = [(verb, annotations[name]) for name, verb in MAPPING_NAMES.items() if name in annotations]
    if not mappings and "RequestMapping" in annotations:
        args = annotations["RequestMapping"]
        mappings = [(verb, args) for verb in _request_mapping_verbs(args)]
    return mappings


def _annotation_value(args: list[str], keys: tuple[str, ...]) -> str:
    for raw in args:
        key, value = _kv(raw)
        if key in keys:
            return value
    for raw in args:
        key, value = _kv(raw)
        if not key:
            return value
    return ""


def _normalize_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    return "/" + value.strip("/")


def _join_path(prefix: str, path: str) -> str:
    left = _normalize_path(prefix)
    right = _normalize_path(path)
    if not right:
        return left or "/"
    if not left:
        return right
    # prefix 为 "/" 时不能直接拼，否则得到 //a
    return (left.rstrip("/") + right).rstrip("/") or "/"


def _constraint(field: str, name: str, args: list[str]) -> FieldConstraint | None:
    rule = name.lower()
    if rule not in PARAM_CONSTRAINT_NAMES:
        return None
    value = None
    if rule == "size":
        raw = _annotation_value(args, ("max", "value"))
        if raw.isdigit():
            value = int(raw)
    elif rule == "pattern":
        value = _annotation_value(args, ("regexp", "value")) or None
    elif rule in ("max", "min", "length", "digits"):
        raw = _annotation_value(args, ("value", "max", "min"))
        if raw.isdigit():
            value = int(raw)
    elif rule in ("decimalmin", "decimalmax"):
        value = _annotation_value(args, ("value",)) or None
    return FieldConstraint(field=field, rule=rule, value=value)


def _message(name: str, args: list[str]) -> str:
    for raw in args:
        key, value = _kv(raw)
        if key == "message" and value:
            return value
    if name in ("Pattern", "Size", "NotBlank", "NotNull", "NotEmpty"):
        return ""
    return ""


def _enum_label(args: list[str], name: str) -> str:
    for raw in args:
        match = CHINESE_RE.search(str(raw))
        if match:
            return match.group(0)
    return name


def _param_source(annotations: dict[str, list[str]]) -> str:
    if "RequestBody" in annotations:
        return "request_body"
    if "PathVariable" in annotations:
        return "path_variable"
    if "RequestParam" in annotations:
        return "request_param"
    return "unknown"


def _param_required(source: str, annotations: dict[str, list[str]]) -> bool:
    ann_name = {
        "request_body": "RequestBody",
        "path_variable": "PathVariable",
        "request_param": "RequestParam",
    }.get(source)
    if ann_name:
        for raw in annotations.get(ann_name, []):
            key, value = _kv(raw)
            # defaultValue 隐含 required = false，Spring 语义如此
            if key == "defaultValue":
                return False
            if key == "required" and value.lower() == "false":
                return False
        return True
    return any(name in annotations for name in ("NotBlank", "NotNull", "NotEmpty"))


def _endpoint_params(parameters: list[dict]) -> list[dict]:
    out: list[dict] = []
    for param in parameters or []:
        anns = _annotations(param.get("annotations") or [])
        source = _param_source(anns)
        param_type = str(param.get("type") or "")
        constraints = []
        for name, args in anns.items():
            if name.lower() in PARAM_CONSTRAINT_NAMES:
                constraints.append(f"{name}({' , '.join(args)})" if args else name)
        out.append({
            "name": param.get("name") or "",
            "type": param_type,
            "source": source,
            "required": _param_required(source, anns),
            "constraints": constraints,
            "dtoType": (
                param_type if param_type[:1].isupper()
                and param_type.lower() not in SIMPLE_PARAM_TYPES else ""
            ),
        })
    return out


def analyze_backend_ast(java_files: list[str], root: str) -> BackendResult:
    data = _run_java(java_files)
    result = BackendResult()
    ep_counter = 0
    br_counter = 0
    texts: list[str] = []
    seen_rules: set[tuple[str, str, str]] = set()

    for unit in data.get("units") or []:
        file_path = str(unit.get("file") or "")
        rel = os.path.relpath(file_path, root).replace("\\", "/") if file_path else ""
        for cls in unit.get("types") or []:
            kind = str(cls.get("kind") or "")
            class_anns = _annotations(cls.get("annotations") or [])
            class_prefixes = _annotation_paths(class_anns.get("RequestMapping", [])) or [""]

            if kind == "ENUM":
                enum_def = EnumDef(name=str(cls.get("name") or ""))
                for value in cls.get("enumValues") or []:
                    name = str(value.get("name") or "")
                    label = _enum_label(value.get("args") or [], name)
                    enum_def.values.append(EnumValue(name=name, label=label))
                if enum_def.values:
                    result.enums.append(enum_def)
                continue

            fields: list[EntityField] = []
            constraints: list[FieldConstraint] = []
            for field in cls.get("fields") or []:
                name = str(field.get("name") or "")
                fields.append(EntityField(name=name, type=str(field.get("type") or "")))
                for ann_name, args in _annotations(field.get("annotations") or []).items():
                    item = _constraint(name, ann_name, args)
                    if item is not None:
                        constraints.append(item)
                    msg = _message(ann_name, args)
                    if msg:
                        texts.append(msg)
            name = str(cls.get("name") or "")
            if fields and not name.endswith(NON_ENTITY_SUFFIXES):
                result.entities.append(
                    Entity(name=name, file=rel, fields=fields, fieldConstraints=constraints)
                )

            for method in cls.get("methods") or []:
                method_anns = _annotations(method.get("annotations") or [])
                auth = [
                    f"@{ann}" for ann in list(class_anns) + list(method_anns)
                    if ann in AUTH_NAMES
                ]
                for verb, path_args in _method_mappings(method_anns):
                    paths = _annotation_paths(path_args) or [""]
                    for class_prefix in class_prefixes:
                        for path in paths:
                            ep_counter += 1
                            result.endpoints.append(
                                Endpoint(
                                    id=f"ep-{ep_counter}",
                                    method=verb,
                                    path=_join_path(class_prefix, path),
                                    controller=name,
                                    methodName=str(method.get("name") or ""),
                                    file=rel,
                                    line=int(method.get("line") or 0),
                                    params=_endpoint_params(method.get("params") or []),
                                    auth=list(dict.fromkeys(auth)),
                                )
                            )

                for item in method.get("ifs") or []:
                    for message in item.get("throws") or []:
                        if not message:
                            continue
                        # 带上文件，否则两个 Controller 里相同的文案+条件会被误去重
                        key = (rel, str(message), str(item.get("condition") or ""))
                        if key in seen_rules:
                            continue
                        seen_rules.add(key)
                        br_counter += 1
                        result.businessRules.append(
                            BusinessRule(
                                id=f"br-{br_counter}",
                                description=str(message),
                                guard=str(item.get("condition") or ""),
                                file=rel,
                                line=int(item.get("line") or 0),
                            )
                        )
                        texts.append(str(message))

    seen = set()
    result.uiTexts = [text for text in texts if not (text in seen or seen.add(text))]
    return result
