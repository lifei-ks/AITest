r"""Vue 前端静态解析（纯规则，零 LLM）。

关键坑（TASK_BOOK 10.2）：
- 路由解析先找 path: 再向左找 {，括号配平；禁用 [^{}]* 正则
- <el-form 正则必须加 (?![-\w])，否则 <el-form-item> 被当成表单
"""
from __future__ import annotations

import os
import re

from app.analyzer.models import (
    Form,
    FormField,
    FrontendResult,
    Page,
    Selector,
)

EL_FORM_RE = re.compile(r"<el-form(?![-\w])")
EL_FORM_ITEM_RE = re.compile(r"<el-form-item(?![-\w])")
PROP_RE = re.compile(r"\bprop\s*=\s*[\"']([^\"']+)[\"']")
LABEL_RE = re.compile(r"\blabel\s*=\s*[\"']([^\"']+)[\"']")
PLACEHOLDER_RE = re.compile(r"\bplaceholder\s*=\s*[\"']([^\"']+)[\"']")
VMODEL_RE = re.compile(r"\bv-model\s*=\s*[\"']?([^\"'\s>]+)[\"']?")
REQUIRED_RE = re.compile(r"\brequired\b|:required\s*=\s*[\"']?true[\"']?")
RULES_RE = re.compile(r":rules\s*=\s*[\"']?([^\"'>]+)[\"']?")
MESSAGE_RE = re.compile(r"ElMessage(?:Box)?\.(?:success|error|warning|info)\s*\(\s*[\"']([^\"']+)[\"']")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def _balanced_brace(s: str, start: int) -> str:
    """从 s 中位置 start（指向 '{'）开始，返回括号配平后的完整片段。"""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return s[start:]


def parse_router(text: str) -> list[dict]:
    """提取路由：先定位 path:，再向左找 {，用括号配平取完整路由对象。"""
    routes: list[dict] = []
    idx = 0
    while True:
        m = re.search(r"path\s*:", text[idx:])
        if not m:
            break
        path_pos = idx + m.start()
        # 向左找最近的 {
        brace = text.rfind("{", 0, path_pos)
        if brace == -1:
            idx = path_pos + len(m.group(0))
            continue
        block = _balanced_brace(text, brace)
        # 提取 path
        pm = re.search(r"path\s*:\s*[\"']([^\"']+)[\"']", block)
        # 提取 component
        cm = re.search(r"component\s*:\s*([^\n,}]+)", block)
        # 提取 name
        nm = re.search(r"\bname\s*:\s*[\"']([^\"']+)[\"']", block)
        if pm:
            routes.append(
                {
                    "path": pm.group(1),
                    "component": (cm.group(1).strip() if cm else ""),
                    "name": (nm.group(1) if nm else ""),
                }
            )
        idx = brace + len(block)
    return routes


def _extract_form_block(text: str, form_start: int) -> str:
    """从 <el-form 开始，取到匹配的 </el-form>（忽略 <el-form-item> 干扰）。"""
    open_count = 0
    i = form_start
    while i < len(text):
        tag = re.match(r"</?el-form(?![-\w])", text[i:])
        if tag:
            if tag.group(0).startswith("</"):
                open_count -= 1
                if open_count == 0:
                    end = text.find(">", i) + 1
                    return text[form_start:end]
            else:
                open_count += 1
            i += len(tag.group(0))
        else:
            i += 1
    return text[form_start:]


def parse_vue_file(path: str, rel: str) -> FrontendResult:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    result = FrontendResult()

    # 路由：仅 router/index.js 或 router.js
    base = os.path.basename(path)
    if base in {"index.js", "router.js"} and "router" in rel.replace("\\", "/"):
        for r in parse_router(text):
            page_name = r.get("name") or _page_name_from_component(r.get("component", ""))
            result.pages.append(
                Page(name=page_name, route=r["path"], file=rel)
            )

    # 表单与字段：.vue 文件
    if base.endswith(".vue"):
        page_name = _page_name_from_file(rel)
        page = Page(name=page_name, route="", file=rel)
        has_form = False
        pos = 0
        while True:
            m = EL_FORM_RE.search(text, pos)
            if not m:
                break
            has_form = True
            block = _extract_form_block(text, m.start())
            form = Form()
            # 逐个 <el-form-item> 解析字段
            item_pos = 0
            while True:
                im = EL_FORM_ITEM_RE.search(block, item_pos)
                if not im:
                    break
                # 取该 form-item 到下一个 form-item 之间的片段
                nxt = EL_FORM_ITEM_RE.search(block, im.end())
                seg = block[im.start() : nxt.start() if nxt else len(block)]
                field = FormField(
                    label=_first(LABEL_RE, seg) or "",
                    prop=_first(PROP_RE, seg) or "",
                    type="input",
                    required=bool(REQUIRED_RE.search(seg)),
                    placeholder=_first(PLACEHOLDER_RE, seg) or "",
                    rules=_first(RULES_RE, seg).split(",") if _first(RULES_RE, seg) else [],
                )
                # input 类型推断：textarea/select/switch
                if "<el-input" in seg and "textarea" in seg:
                    field.type = "textarea"
                elif "<el-select" in seg:
                    field.type = "select"
                elif "<el-switch" in seg:
                    field.type = "switch"
                # 过滤无 prop 且无 label 的空 form-item（如纯按钮项）
                if field.prop or field.label:
                    form.fields.append(field)
                item_pos = im.end()
            page.forms.append(form)
            pos = m.end()

        # .vue 文件即页面（form 可为空，如纯列表页）
        result.pages.append(page)

        # 选择器：由 label/placeholder 反推
        for p in result.pages:
            if p.file != rel:
                continue
            for frm in p.forms:
                for fld in frm.fields:
                    if fld.placeholder:
                        result.selectors.append(
                            Selector(type="placeholder", value=fld.placeholder, page=p.name)
                        )
                    elif fld.label:
                        result.selectors.append(
                            Selector(type="label", value=fld.label, page=p.name)
                        )

        # 文案：ElMessage / ElMessageBox
        for mm in MESSAGE_RE.finditer(text):
            result.uiTexts.append(mm.group(1))

        # 补充中文文案（模板里的按钮文本等，简化：ElMessage 已覆盖主要）

    seen = set()
    result.uiTexts = [t for t in result.uiTexts if not (t in seen or seen.add(t))]
    return result


def _first(regex: re.Pattern, text: str) -> str | None:
    m = regex.search(text)
    return m.group(1) if m else None


def _page_name_from_component(component: str) -> str:
    m = re.search(r"([^/]+)\.vue", component)
    if m:
        return m.group(1)
    m = re.search(r"['\"]?([A-Za-z0-9_]+)['\"]?", component)
    return m.group(1) if m else "Page"


def _page_name_from_file(rel: str) -> str:
    return os.path.splitext(os.path.basename(rel))[0]


def analyze_frontend(vue_js_files: list[str], root: str) -> FrontendResult:
    merged = FrontendResult()
    # 路由文件只用于建立 page_name -> route 映射；真正的 page（含 forms）由 .vue 解析产出
    router_files = [f for f in vue_js_files if os.path.basename(f) in {"index.js", "router.js"}]
    page_files = [f for f in vue_js_files if f.endswith(".vue")]

    route_map: dict[str, str] = {}  # page_name -> route
    for f in router_files:
        one = parse_vue_file(f, os.path.relpath(f, root))
        for p in one.pages:
            route_map[p.name] = p.route

    for f in page_files:
        one = parse_vue_file(f, os.path.relpath(f, root))
        for p in one.pages:
            if not p.route and p.name in route_map:
                p.route = route_map[p.name]
        merged.pages.extend(one.pages)
        merged.selectors.extend(one.selectors)
        merged.uiTexts.extend(one.uiTexts)

    # 去重 pages（按 file+name）
    dedup: dict[str, Page] = {}
    for p in merged.pages:
        dedup[(p.file, p.name)] = p
    merged.pages = list(dedup.values())

    seen = set()
    merged.uiTexts = [t for t in merged.uiTexts if not (t in seen or seen.add(t))]
    return merged
