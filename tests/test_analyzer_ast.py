"""AST 静态解析回归：Java/JDK Compiler API + Vue compiler-sfc。"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.analyzer.ast_java import analyze_backend_ast
from app.analyzer.ast_vue import analyze_frontend_ast
from app.analyzer.spring import analyze_backend

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT / "scripts" / "order-system"


def _analyze_sources(sources: dict[str, str]):
    """把夹具写进仓库内临时目录再解析（沙箱下系统临时目录不可写）。"""
    workdir = Path(tempfile.mkdtemp(prefix="ast-edge-", dir=REPO_ROOT))
    try:
        files = []
        for name, text in sources.items():
            path = workdir / "src" / "main" / "java" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            files.append(str(path))
        return analyze_backend_ast(files, str(workdir))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@pytest.mark.skipif(shutil.which("java") is None, reason="java not available")
def test_java_ast_extracts_endpoints_entities_enums_and_rules():
    backend_root = ROOT / "backend"
    files = [str(p) for p in backend_root.rglob("*.java") if "target" not in p.parts]
    result = analyze_backend_ast(files, str(backend_root))

    endpoints = {(item.method, item.path) for item in result.endpoints}
    assert ("POST", "/api/orders") in endpoints
    assert ("POST", "/api/orders/{id}/pay") in endpoints

    order = next(item for item in result.entities if item.name == "Order")
    constraints = {(item.field, item.rule, item.value) for item in order.fieldConstraints}
    assert ("receiverName", "size", 20) in constraints
    assert ("phone", "pattern", "^1[3-9]\\d{9}$") in constraints

    status = next(item for item in result.enums if item.name == "OrderStatus")
    labels = {item.name: item.label for item in status.values}
    assert labels["CREATED"] == "待支付"
    assert labels["PAID"] == "已支付"

    assert any("只有待支付订单可以支付" in item.description for item in result.businessRules)
    assert "收货人不能超过20个字符" in result.uiTexts


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_vue_ast_extracts_pages_fields_selectors_and_texts():
    frontend_root = ROOT / "frontend"
    files = [
        str(p)
        for p in frontend_root.rglob("*")
        if p.suffix in (".vue", ".js") and "node_modules" not in p.parts
    ]
    result = analyze_frontend_ast(files, str(frontend_root))

    page = next(item for item in result.pages if item.name == "OrderCreate")
    assert page.route == "/orders/create"
    fields = [field for form in page.forms for field in form.fields]
    receiver = next(field for field in fields if field.prop == "receiverName")
    assert receiver.placeholder == "请输入收货人"

    selectors = {(item.type, item.value) for item in result.selectors}
    assert ("placeholder", "请输入收货人") in selectors
    assert "订单创建成功" in result.uiTexts


JAVA_EDGE_CASES = """
package demo;

import java.util.Optional;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/demo")
public class DemoController {

    @GetMapping(name = "listByName", path = "/list")
    public String list(@RequestParam(defaultValue = "1") int page,
                       @RequestParam String keyword) {
        return "";
    }

    @GetMapping({"/a", "/b"})
    public String multi() {
        return "";
    }

    @RequestMapping(value = "/legacy", method = RequestMethod.PUT)
    public String legacy() {
        return "";
    }

    @DeleteMapping("/{id}")
    public String remove(@PathVariable Long id) {
        return find(id).orElseThrow(() -> new BizException(40401, "记录不存在"));
    }

    @PostMapping("/lambdas")
    public String lambdas() {
        items.stream().map(item -> {
            if (item == null) {
                throw new BizException("元素不能为空");
            }
            return item;
        }).count();
        return "";
    }

    private Optional<String> find(Long id) {
        return Optional.empty();
    }
}

@RestController
@RequestMapping("/")
class RootController {

    @GetMapping("/root")
    public String root() {
        return "";
    }

    @GetMapping("/a,b")
    public String commaPath() {
        return "";
    }

    @GetMapping("{raw}")
    public String rawPath() {
        return "";
    }
}
"""


@pytest.mark.skipif(shutil.which("java") is None, reason="java not available")
def test_java_ast_parses_named_path_arrays_and_lambda_throws():
    result = _analyze_sources({"demo/DemoController.java": JAVA_EDGE_CASES})

    endpoints = {(item.method, item.path) for item in result.endpoints}

    # name 在前、path 在后时仍要取 path
    assert ("GET", "/api/demo/list") in endpoints
    # 数组路径展开成多个端点
    assert ("GET", "/api/demo/a") in endpoints
    assert ("GET", "/api/demo/b") in endpoints
    # 方法级 @RequestMapping(method = ...) 也是端点
    assert ("PUT", "/api/demo/legacy") in endpoints
    # 类前缀 "/" 不能拼出 //root
    assert ("GET", "/root") in endpoints
    # 单个路径里的逗号不能被当数组拆开
    assert ("GET", "/a,b") in endpoints
    # 不带引号的 {raw} 是路径变量，不能被当成数组
    assert ("GET", "/{raw}") in endpoints
    assert len(result.endpoints) == 9

    list_ep = next(item for item in result.endpoints if item.path == "/api/demo/list")
    required = {param["name"]: param["required"] for param in list_ep.params}
    assert required["page"] is False  # defaultValue 隐含 required = false
    assert required["keyword"] is True

    rule = next(item for item in result.businessRules if item.description == "记录不存在")
    assert "orElseThrow" in rule.guard
    # lambda 块里的 if 守卫只产出一条规则，不能被外层调用重复记账
    assert [item.description for item in result.businessRules].count("元素不能为空") == 1


RULE_TEMPLATE = """
package demo;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/%s")
public class %sController {

    @GetMapping("/x")
    public String x(Long id) {
        if (id == null) {
            throw new BizException("参数不能为空");
        }
        return "";
    }
}

class BizException extends RuntimeException {
    BizException(String message) {
        super(message);
    }
}
"""


@pytest.mark.skipif(shutil.which("java") is None, reason="java not available")
def test_java_ast_keeps_same_rule_text_from_different_files():
    result = _analyze_sources({
        "demo/AController.java": RULE_TEMPLATE % ("a", "A"),
        "demo/BController.java": RULE_TEMPLATE % ("b", "B"),
    })
    rules = [item for item in result.businessRules if item.description == "参数不能为空"]
    assert len(rules) == 2
    assert len({item.file for item in rules}) == 2


@pytest.mark.skipif(shutil.which("java") is None, reason="java not available")
def test_ast_and_regex_engines_agree_on_order_system():
    backend_root = ROOT / "backend"
    files = [str(p) for p in backend_root.rglob("*.java") if "target" not in p.parts]
    ast = analyze_backend_ast(files, str(backend_root))
    regex = analyze_backend(files, str(backend_root))

    assert {(item.method, item.path) for item in ast.endpoints} == {
        (item.method, item.path) for item in regex.endpoints
    }
    ast_list = next(item for item in ast.endpoints if item.methodName == "list")
    regex_list = next(item for item in regex.endpoints if item.methodName == "list")
    assert {p["name"]: p["required"] for p in ast_list.params} == {
        p["name"]: p["required"] for p in regex_list.params
    }
