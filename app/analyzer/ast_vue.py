"""Vue AST 解析：compiler-sfc + compiler-dom + Babel → FrontendResult。

Node 侧解析器在 frontend/scripts/ast_vue_parser.cjs，复用前端已安装依赖。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from app.analyzer.models import Form, FormField, FrontendResult, Page, Selector

BASE_DIR = Path(__file__).resolve().parent.parent.parent
NODE_AST_SCRIPT = BASE_DIR / "frontend" / "scripts" / "ast_vue_parser.cjs"


def _run_node(files: list[str]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 node，可执行文件不可用")
    if not NODE_AST_SCRIPT.exists():
        raise RuntimeError(f"Vue AST 导出器不存在: {NODE_AST_SCRIPT}")
    proc = subprocess.run(
        [node, str(NODE_AST_SCRIPT), *files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Vue AST 解析失败: {proc.stderr[:800]}")
    return json.loads(proc.stdout)


def analyze_frontend_ast(vue_js_files: list[str], root: str) -> FrontendResult:
    units = _run_node(vue_js_files)
    result = FrontendResult()
    route_map: dict[str, str] = {}

    for unit in units:
        if unit.get("kind") != "router":
            continue
        for route in unit.get("routes") or []:
            path = str(route.get("path") or "")
            if not path:
                continue
            name = str(route.get("name") or "")
            component = str(route.get("component") or "")
            if name:
                route_map[name] = path
            if component:
                route_map[component] = path

    for unit in units:
        if unit.get("kind") != "vue":
            continue
        file_path = str(unit.get("file") or "")
        rel = os.path.relpath(file_path, root).replace("\\", "/") if file_path else ""
        name = str(unit.get("name") or Path(file_path).stem)
        page = Page(name=name, route=route_map.get(name, ""), file=rel)
        for form_data in unit.get("forms") or []:
            form = Form()
            for field_data in form_data.get("fields") or []:
                form.fields.append(
                    FormField(
                        label=str(field_data.get("label") or ""),
                        prop=str(field_data.get("prop") or ""),
                        type=str(field_data.get("type") or "input"),
                        required=bool(field_data.get("required")),
                        placeholder=str(field_data.get("placeholder") or ""),
                        rules=[],
                    )
                )
            page.forms.append(form)
        result.pages.append(page)
        for selector in unit.get("selectors") or []:
            result.selectors.append(
                Selector(
                    type=str(selector.get("type") or ""),
                    value=str(selector.get("value") or ""),
                    page=name,
                )
            )
        result.uiTexts.extend(str(item) for item in unit.get("uiTexts") or [] if str(item))

    dedup: dict[tuple[str, str], Page] = {}
    for page in result.pages:
        dedup[(page.file, page.name)] = page
    result.pages = list(dedup.values())

    seen_selectors = set()
    selectors = []
    for selector in result.selectors:
        key = (selector.type, selector.value, selector.page)
        if key in seen_selectors:
            continue
        seen_selectors.add(key)
        selectors.append(selector)
    result.selectors = selectors

    seen_texts = set()
    result.uiTexts = [
        text for text in result.uiTexts
        if not (text in seen_texts or seen_texts.add(text))
    ]
    return result
