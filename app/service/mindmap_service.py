"""脑图服务：四层树（模块→类型→用例→前置/步骤/预期）+ XMind/Markdown 导出。"""
from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.entity.models import Project, RequirementCase, TestCase


def build_tree(session: Session, pid: str, requirement_id: str | None = None) -> dict:
    """四层树：id 前缀严格 module-* / type-* / case-* / pre-* / steps-* / exp-* / s-*。

    requirement_id 非空时只展示该需求版本关联的用例。
    """
    proj = session.get(Project, pid)
    stmt = select(TestCase).where(TestCase.project_id == pid)
    if requirement_id:
        # 避免 MySQL 跨表 collation 比较（同 testcase_service 的处理）
        mapped_ids = list(
            session.scalars(select(RequirementCase.test_case_id).where(RequirementCase.requirement_id == requirement_id))
        )
        stmt = stmt.where(TestCase.id.in_(mapped_ids or ["__none__"]))
    cases = list(session.scalars(stmt.order_by(TestCase.project_seq)))

    # 模块 → 类型 → 用例
    by_module: dict[str, dict[str, list[TestCase]]] = {}
    for tc in cases:
        by_module.setdefault(tc.module or "未分类", {}).setdefault(tc.type, []).append(tc)

    modules = []
    for mname, types in by_module.items():
        type_nodes = []
        for tname, tcases in types.items():
            case_nodes = []
            for tc in tcases:
                children = []
                pres = json.loads(tc.preconditions) if tc.preconditions else []
                steps = json.loads(tc.steps) if tc.steps else []
                exps = json.loads(tc.expected_results) if tc.expected_results else []
                for pre in pres:
                    children.append({"id": f"pre-{tc.id}", "title": f"前置条件：{pre}"})
                if steps:
                    step_children = [{"id": f"s-{tc.id}-{i}", "title": f"{i + 1}. {s}"} for i, s in enumerate(steps)]
                    children.append({"id": f"steps-{tc.id}", "title": f"步骤：{len(steps)} 步", "children": step_children})
                for exp in exps:
                    children.append({"id": f"exp-{tc.id}", "title": f"预期：{exp}"})
                case_nodes.append({"id": f"case-{tc.id}", "title": f"{tc.id} {tc.title}", "children": children})
            type_nodes.append({"id": f"type-{tname}", "title": _type_cn(tname), "children": case_nodes})
        modules.append({"id": f"module-{mname}", "title": f"{mname}", "children": type_nodes})

    tree = {"id": "root", "title": f"{proj.name} · 测试用例集（{len(cases)} 条）", "children": modules}
    statistics = {
        "modules": len(modules),
        "types": sum(len(m["children"]) for m in modules),
        "cases": len(cases),
        "leaves": _count_leaves(tree),
    }
    return {"tree": tree, "statistics": statistics}


def _type_cn(t: str) -> str:
    return {"positive": "正例", "negative": "反例", "boundary": "边界", "state": "流转"}.get(t, t)


def _count_leaves(tree: dict) -> int:
    children = tree.get("children") or []
    if not children:
        return 1
    return sum(_count_leaves(c) for c in children)


def _to_xmind_node(node: dict) -> dict:
    n = {"title": node["title"]}
    if node.get("children"):
        n["children"] = {"attached": [_to_xmind_node(c) for c in node["children"]]}
    return n


def export_xmind(tree: dict) -> bytes:
    """手写 zip：content.json + metadata.json + manifest.json（XMind ZEN 最小结构）。"""
    content = json.dumps([_to_xmind_node(tree["tree"])], ensure_ascii=False)
    metadata = json.dumps({"creator": {"name": "AICase Core", "version": "0.1.0"}}, ensure_ascii=False)
    manifest = json.dumps({"file-entries": {"content.json": {}, "metadata.json": {}}}, ensure_ascii=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.json", content)
        z.writestr("metadata.json", metadata)
        z.writestr("manifest.json", manifest)
    return buf.getvalue()


def export_markdown(tree: dict) -> str:
    """markmap 兼容缩进列表。"""
    lines: list[str] = []

    def walk(node: dict, depth: int):
        lines.append("  " * depth + "- " + node["title"])
        for c in node.get("children") or []:
            walk(c, depth + 1)

    walk(tree["tree"], 0)
    return "\n".join(lines)
