# 源码静态分析 AST 重构报告

- 日期：2026-10-30
- 范围：Spring Boot 后端 + Vue 前端静态解析
- 默认引擎：`ANALYZER_ENGINE=ast`，失败自动回退 `regex`
- 验证：后端 173 个测试通过

## 1. 改造前的问题

原实现是逐行正则 + 括号配平：

- 多行注解、嵌套泛型、注释/字符串里的同名文本容易误匹配；
- 类级和方法级注解的归属靠行号猜测；
- Vue 的 `<el-form>` / `<el-form-item>` 需要特殊正则规避；
- 路由解析靠 `path:` 向左找 `{`，嵌套路由容易截断；
- 复杂状态守卫、`else`、`try/catch` 难以准确处理。

## 2. 改造后的架构

```text
scanner
  ↓
analysis_service
  ├─ Java AST：JDK Compiler API → JSON → BackendResult
  ├─ Vue AST：compiler-sfc + compiler-dom + Babel → JSON → FrontendResult
  └─ 失败回退：spring.py / vue.py 正则解析
  ↓
models.py（契约不变）
  ↓
CodeAnalysis / StateMachine
  ↓
用例生成 / AI 质检 / 执行 / 覆盖率
```

核心原则：**AST 只替换解析器层，输出契约不变**。下游的 `Endpoint`、`Entity`、
`FieldConstraint`、`Page`、`FormField`、`Selector` 等结构保持不变。

## 3. Java AST 设计

### 3.1 解析器

新增 `scripts/java_ast_dump/JavaAstDump.java`，使用 JDK 自带的
`javax.tools.JavaCompiler` + `com.sun.source.tree`：

```java
JavacTask task = (JavacTask) compiler.getTask(...);
for (CompilationUnitTree unit : task.parse()) {
    // 遍历 ClassTree / MethodTree / VariableTree
}
```

它只做 `task.parse()`，不依赖项目 classpath，也不需要额外 jar。

### 3.2 输出内容

Java AST 导出为 JSON，再由 `app/analyzer/ast_java.py` 映射：

- `ClassTree` → 类名、注解、字段、方法；
- 方法上的 `GetMapping/PostMapping/...` → Endpoint；
- 类级 `RequestMapping` → 路径前缀；
- 字段上的 `NotBlank/Size/Pattern/...` → FieldConstraint；
- `EnumTree` → EnumDef / EnumValue；
- `IfTree + ThrowTree` → BusinessRule；
- `MethodInvocationTree` → `setStatus` 等调用，供状态机使用。

### 3.3 示例

```java
@RestController
@RequestMapping("/api/orders")
class OrderController {
    @PostMapping
    ApiResponse<Order> create(@Valid @RequestBody CreateOrderRequest request) { ... }
}
```

映射为：

```text
Endpoint:
  method = POST
  path = /api/orders
  params = [request: CreateOrderRequest, source=request_body, required=true]
```

字段约束示例：

```java
@NotBlank(message = "收货人不能为空")
@Size(max = 20, message = "收货人不能超过20个字符")
private String receiverName;
```

映射为：

```text
Entity: Order
FieldConstraint: receiverName, notblank
FieldConstraint: receiverName, size, 20
uiTexts: 收货人不能为空 / 收货人不能超过20个字符
```

## 4. Vue AST 设计

### 4.1 解析器

新增 `frontend/scripts/ast_vue_parser.cjs`：

- `@vue/compiler-sfc` 把 `.vue` 拆成 template / script / script setup；
- `@vue/compiler-dom` 生成模板 AST；
- `@babel/parser` 解析 script 中的 `ElMessage` 等提示文案；
- 解析 `router/index.js` 的 `createRouter({ routes: [...] })`。

### 4.2 输出内容

- `ElementNode(el-form)` → Form；
- `ElementNode(el-form-item)` → FormField（label、prop、required）；
- 子级 `el-input/el-select/el-switch` → placeholder、类型、v-model；
- placeholder/label → Selector；
- `el-button` 文本 + `ElMessage` → uiTexts；
- 路由对象 → Page.route。

### 4.3 示例

```vue
<el-form :model="form">
  <el-form-item label="收货人" prop="receiverName">
    <el-input v-model="form.receiverName" placeholder="请输入收货人" />
  </el-form-item>
</el-form>
```

映射为：

```text
Page: OrderCreate
  route: /orders/create
  FormField:
    label: 收货人
    prop: receiverName
    placeholder: 请输入收货人
Selector: placeholder / 请输入收货人
```

## 5. 接入方式

`app/config.py` 新增：

```text
ANALYZER_ENGINE=ast   # 默认；失败自动回退 regex
```

`app/service/analysis_service.py`：

```python
try:
    backend = analyze_backend_ast(java, root)
except Exception as exc:
    warnings.append(f"Java AST 解析失败，已回退规则解析：{exc}")
    backend = analyze_backend(java, root)
```

前端同理。Java 或 Node 不可用、AST 抛异常时，自动回退原正则实现，
不会让整个分析失败。

## 6. 输出契约

AST 解析结果仍然落到 `app/analyzer/models.py` 的 dataclass：

```text
BackendResult
  Endpoint / Entity / FieldConstraint / EnumDef / BusinessRule

FrontendResult
  Page / Form / FormField / Selector
```

因此以下下游代码不需要修改：

- `generation_service` 的用例生成；
- `test_generator_agent` 的边界/反例/状态用例；
- `case_review_service` 的 AI 质检事实；
- `execution_agent` 的 selector 映射；
- `coverage_service` 的接口/规则/状态迁移覆盖。

## 7. 验证结果

```text
后端：173 passed
Java AST：OrderController 14 个端点、Order 字段约束、OrderStatus 枚举、业务规则
Vue AST：OrderCreate 表单字段、/orders/create 路由、8 个 placeholder 选择器、按钮文案
```

新增测试 `tests/test_analyzer_ast.py`：

- `POST /api/orders` 和 `POST /api/orders/{id}/pay`；
- `receiverName size=20`、`phone pattern`；
- `OrderStatus.CREATED = 待支付`、`PAID = 已支付`；
- 业务规则“只有待支付订单可以支付”；
- Vue 页面 `OrderCreate` 的 `receiverName` / `请输入收货人`；
- `订单创建成功` 文案。

## 8. 当前限制

- Java 只做语法解析，没有做类型解析/符号求解；
- DTO 和实体通过类名/字段名关联，不是编译器级符号引用；
- Vue 的 `:rules` 目前主要依赖模板属性，`script` 里的 rules 还未完全回填；
- 动态组件、computed 路由、运行时注入的字段仍可能漏采；
- AST 子进程按文件全量解析，尚未做按文件 hash 的增量缓存。

## 9. 面试怎么讲

30 秒版本：

> 原来静态分析是正则逐行扫描，容易受多行注解、嵌套结构和注释影响。
> 我把它重构成解析器插件：Java 用 JDK Compiler API 的 AST，Vue 用
> compiler-sfc + compiler-dom + Babel，输出仍然是原来的结构化事实契约。
> AST 失败会自动回退正则，保证可用性。下游的生成、审核、执行不需要改。

可能追问：

- **为什么不引入 JavaParser？** 当前环境没有网络依赖，JDK 自带 Compiler API
  就能拿到语法树，零新增 jar；JavaParser 适合需要类型解析/符号求解的阶段。
- **AST 和正则的区别？** AST 有父子节点和节点类型，能准确回答“这个注解属于
  哪个方法”“这个 if 守卫属于哪个方法”，正则只能按行猜。
- **AST 是否一定准确？** AST 解决语法结构，不解决运行时语义；动态代理、
  MyBatis XML、消息队列这些仍然需要运行时证据和人工兜底。
- **怎么保证不退化？** 输出契约不变 + 173 个测试 + AST 失败回退 regex +
  对 order-system 做 golden case 验证。

一句话总结：**用 AST 替换“按行猜”的解析层，但保留原有结构化契约和正则兜底，
让静态分析更准、更可解释，同时不破坏下游。**
