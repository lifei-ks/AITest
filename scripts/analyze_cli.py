"""命令行静态分析调试工具：纯规则分析一个源码目录并打印摘要。

用法：.venv/Scripts/python.exe scripts/analyze_cli.py <源码路径>
不落库，直接调解析器，便于快速验证解析器在真实代码上的表现。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.state_machine_agent import extract_state_machines  # noqa: E402
from app.analyzer.scanner import list_by_ext, scan  # noqa: E402
from app.analyzer.spring import analyze_backend  # noqa: E402
from app.analyzer.vue import analyze_frontend  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python scripts/analyze_cli.py <源码路径>")
        sys.exit(1)
    root = sys.argv[1]
    s = scan(root)
    print("技术栈:", s.tech_stack)

    java = list_by_ext(s, {".java"})
    backend = analyze_backend(java, root)
    print(f"\n== 后端端点数: {len(backend.endpoints)}")
    for ep in backend.endpoints:
        print(f"  {ep.method:6} {ep.path:28} {ep.controller}.{ep.methodName}  @{ep.file}:{ep.line}")

    print(f"\n实体: {[e.name for e in backend.entities]}")
    for e in backend.entities:
        cons = [(c.field, c.rule, c.value) for c in e.fieldConstraints]
        print(f"  {e.name}: 字段={[f.name for f in e.fields]}")
        print(f"    约束={cons}")

    print("\n枚举:")
    for e in backend.enums:
        print(f"  {e.name}: {[(v.name, v.label) for v in e.values]}")

    print(f"\n业务规则数: {len(backend.businessRules)}")
    for r in backend.businessRules:
        print(f"  [{r.id}] {r.description}  guard={r.guard}  @{r.file}:{r.line}")

    machines = extract_state_machines(backend, java, root)
    for m in machines:
        print(f"\n== 状态机 {m.name}（{len(m.states)} 态，{len(m.transitions)} 转换）:")
        for t in m.transitions:
            print(f"  {t.from_:10} -> {t.to:10} [{t.trigger}] verified={t.verified} {t.evidence}")

    vue_js = list_by_ext(s, {".vue", ".js"})
    frontend = analyze_frontend(vue_js, root)
    print(f"\n== 前端页面数: {len(frontend.pages)}")
    for p in frontend.pages:
        fields = [f.label or f.prop for f in p.forms[0].fields] if p.forms else []
        print(f"  {p.name:14} route={p.route:18} 表单字段={fields}")
    print(f"选择器: {[(x.type, x.value) for x in frontend.selectors]}")
    print(f"UI文案: {frontend.uiTexts}")


if __name__ == "__main__":
    main()
