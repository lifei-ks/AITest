# 简历四段 · 深挖问答（代码级证据版）

> 配套文档：`docs/RESUME-INTERVIEW-QA.md`（通用 53 问，管"讲清楚"）。
> 这份文档只管一件事：**面试官不信你的描述，要你指出代码在哪一行时，你怎么答。**
> 每题都标注了真实文件与行号，追问到第 3 层时不至于掉链子。

---

## 0. 简历原文有 3 处必须改，否则会被当场问穿

| 简历原文 | 问题 | 改法 |
|---|---|---|
| 基于 **AI** 解析需求文档**与源码** | 源码是规则解析，零模型。面试官问"用什么模型处理 AST"就崩 | 基于 LLM 解析需求文档，**结合规则静态分析**源码 |
| 输出审核结论、**置信度**和异常区域坐标 | 置信度已从代码里**废弃**，`review.confidence = None` 显式清空 | 输出审核结论、**问题描述与异常区域坐标** |
| **闭环**（用例生成→执行→审核→报告） | 闭环这个词没错，但要能说清"闭在哪" | 闭在**人工复核**：可疑结果转人工，人给出最终结论 |

置信度那条是硬伤，代码里有两处明确废弃：

```python
# app/service/execution_service.py:256-257
# 已废弃：不再采集模型自报的置信度（无校准依据、不参与判定），重审时显式清空旧值
review.confidence = None
```

```python
# app/entity/models.py:178-180
# 已废弃：模型自报置信度无校准依据、不参与判定，接口不再输出，新记录恒为 NULL。
# 保留列仅为避免对已有 MySQL 表做破坏性迁移。
confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
```

**被追问时的标准口径**：

> 早期确实存过模型自报的 confidence，后来发现它没校准——同一个页面模型这次给 0.9 下次给 0.6，没有任何可验证的对应关系，当阈值用只会制造假精确。所以我把它废弃了，列还留着是为了不做破坏性迁移，但值恒为 NULL、接口不输出、也不参与判定。现在用三态结论 `ok / suspect / unavailable`，外加一条规矩：**证据不足一律 suspect，宁可让人多看一眼，也不给它一个假的置信度**。

---

## 1. 源码静态分析（对应 bullet 1）

### Q1.1 你说"解析源码"，为什么不用 LLM？现在都流行让模型读代码。

三个原因，按重要性排：

1. **可复现**。同一个仓库跑两次，结果必须一模一样。测试平台如果分析结果会漂，后面生成的用例就没法比对、没法回归。
2. **不该把源码传出去**。这是企业代码，一传就是合规问题。
3. **不需要**。注解、字段、路由这些都是强结构，正则足够，成本是 0。

代价我也清楚：正则不是 AST，遇到动态拼接的路径、继承来的 `@RequestMapping`、Kotlin DSL 这些会漏。但漏的后果是"少生成"，不是"生成错的"——因为下游有质量闸兜底。

> 一句话：**代码用规则，文档用模型。** 代码是给机器看的，文档是给人看的，用错了工具两边都亏。

### Q1.2 接口到底怎么拼的？类级和方法级注解怎么处理？

这是踩过坑的地方。类级 `@RequestMapping` **只能当路径前缀，不能自己成为端点**，否则前缀会被重复拼接：

```python
# app/analyzer/spring.py:23-28
# 只匹配方法级映射注解；@RequestMapping 由 CLASS_MAPPING_RE 单独处理前缀，
# 不能混入（否则类级 @RequestMapping 会被当成端点导致前缀重复拼接）
# 括号可选：Spring 允许 @PostMapping（省略括号，path 默认空，继承类级前缀）
METHOD_MAPPING_RE = re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping\s*(?:\(([^)]*)\))?")
CLASS_MAPPING_RE = re.compile(r"@RequestMapping\s*\(([^)]*)\)")
```

拼接规则在 `_join_path`（`spring.py:77-83`）：两边都 `strip('/')` 再补单斜杠，避免 `/api/` + `/order` 变成 `//`。端点必须带 `file` 和 `line`，因为后面所有代码事实都要能**回溯到源码行**，做不到就不算事实。

### Q1.3 那 `@PostMapping` 后面不写括号呢？

坑在这里。很多人写 `@PostMapping` 不带括号，表示复用类级路径。所以正则的括号段是 `(?:\(...\))?` 可选的——不写括号时 path 视为空串，最终路径就是类级前缀本身。

如果正则写成强制括号 `@PostMapping\("...")`，这类写法会被整个漏掉，而且不漏报错，是静默漏。

### Q1.4 字段约束怎么提？前后端冲突听谁的？

后端注解是唯一可信来源：

```python
# app/analyzer/spring.py:49-51
CONSTRAINT_RE = re.compile(
    r"@(NotBlank|NotNull|NotEmpty|Size|Max|Min|Pattern|Email|DecimalMin|DecimalMax|Digits|Positive|PositiveOrZero|Negative|Length)\s*(?:\(\s*([^)]*)\s*\))?"
)
```

冲突听后端，理由写在代码注释里：

```python
# app/service/generation_service.py:733
# 后端校验注解才是必填/长度的可靠来源（Vue rules 常在 <script> 中，前端解析拿不到）
```

> Vue 的校验规则大多写在 `<script>` 里，我的模板解析读不到；后端 `@NotBlank` 是明确的。所以**"这个字段必填"听后端，"这个字段在页面上叫什么、placeholder 是什么"听前端**——两边各管一半，不重叠。

### Q1.5 状态机怎么提取的？`verified` 这个字段什么含义？

流程五步（`state_machine_agent.py:1-13` 的模块注释）：找状态枚举 → 找 `setStatus(X)` 写入点 → 向上找方法边界推 trigger → 方法内向上找状态守卫推 `from` → BFS 算最短路径。

`verified` 的判据只有一条：**能不能找到写状态的那行代码**。

```python
# app/agent/state_machine_agent.py:199
verified=True,  # 找到 setStatus 写入点
```

**这里我会主动交底**：现在只要产出 transition 就恒为 True，粒度太粗。它只保证"确实有代码在写这个状态"，不保证"from 推导正确"。守卫缺失时 `from` 是全集减目标态，那属于"可能态"不是"确定态"。正确的做法是把 `verified` 拆成两维：写入证据 verified + 守卫推导 derived/exhaustive。这条是我下次重构要做的。

### Q1.6 守卫条件写 `status != CREATED` 和写 `status == CREATED`，推导逻辑一样吗？

不一样，这是核心逻辑：

```python
# app/state_machine_agent.py:171-181
if guard_text:
    g = _extract_status_literals(guard_text)
    if _is_negated(guard_text):
        from_states = sorted(g)              # !X / != X → 来源态就是 {X}
    else:
        from_states = sorted(set(all_states) - g)   # == X → 来源态是全集 − X
else:
    from_states = sorted(set(all_states) - {target})  # 无守卫 → 全集 − 目标态
from_states = [s for s in from_states if s != target]
```

翻译成人话：
- `if (status != CREATED) throw` → 只有在 CREATED 才能走到后面，所以来源是 CREATED；
- `if (status == SHIPPED) throw` → 排除了 SHIPPED，来源是剩下的所有态；
- 没守卫 → 谁都可能，来源是全集中除掉目标态。

最后一行剔除 `from == target`，避免出现 `PAID → PAID` 这种自环。

**真实踩坑**：早期只识别 `throw`，碰到用 `continue` / `return` 做守卫的批量操作，守卫全被漏掉，于是 `from` 退回成"全集减目标态"，一条批量取消被拆成 5 条错误转换。修复是让守卫识别认出"显式离开当前流程"的四种写法：

```python
# app/state_machine_agent.py:28
GUARD_EXIT_RE = re.compile(r"\b(throw|continue|return|break)\b")
```

### Q1.7 为什么 `setStatus(CREATED)` 这种要被跳过？

因为那是**对象初始化**，不是状态流转。创建订单时 `setStatus(CREATED)` 写在 create 方法里，它不构成任何一条边。

```python
# app/state_machine_agent.py:143-145
# 写初始态是"对象初始化"（如 create 里 setStatus(CREATED)），不是状态流转，跳过
if all_states and target == all_states[0]:
    continue
```

判据是"等于枚举第一个值"。这个判据比较朴素——依赖枚举按生命周期顺序书写。这是已知的启发式，不是严格语义，我会在讲的时候主动说清楚。

### Q1.8 Vue 页面元素解析到什么粒度？

到"能定位"的粒度：页面路由、表单、字段 label / prop、placeholder、按钮文案、可定位选择器、可见文案集合。这些不是给人看的，是给 LLM 划红线的——**它只能用这些值。**

关键设计：`uiSelector.type` 只有 `route / placeholder / text / label / role / testid / css / xpath` 八种（`case_review_service.py:19`），前六种必须能在源码事实里查到，查不到就报错：

```python
# app/service/case_review_service.py:297-306
if selector_type == "placeholder" and selector_value not in placeholders:
    errors.append(f"步骤 {index} placeholder 不在源码事实中: {selector_value}")
if selector_type == "text" and selector_value and selector_value not in text_corpus:
    errors.append(f"步骤 {index} 按钮文案不在源码/需求事实中: {selector_value}")
```

> 这就把"幻觉"从**事后检查**变成了**结构上不可能**——模型想编一个页面上没有的按钮，它过不了这道校验。

### Q1.9 正则解析怎么证明准？有没有指标？

说实话，**没有精确率/召回率指标**，这是这个项目的短板之一。我能给的是三条侧面证据：

1. 分析结果全部带 `file#line`，可以人工抽检；
2. `scripts/analyze_cli.py` 支持不落库直接看分析产物，改规则可以立刻验证；
3. 关键场景有回归测试锁定——列表筛选点"查询"、金额区间、Element Plus 下拉。

如果面试官继续问"你怎么知道自己漏了多少"，正确回答是：**不知道，所以才需要下游质量闸**。分析漏了 → 生成层缺少事实 → 校验报错或拒收 → 这条测试点被记为未覆盖，而不是被伪造成覆盖。宁可覆盖数字难看，也不要假绿。

### Q1.10 换 React 要改多少？

只加一个 analyzer。`scanner.py` 只管给文件列表，analyzer 输出统一的 `BackendResult / FrontendResult / StateMachineResult`（`app/analyzer/models.py`），生成器和执行器完全不感知框架。

真正的成本不在执行器，在**页面结构差异**：Element Plus 的 `el-form-item` 有 `prop`/`label` 的天然映射，React 社区没有这种统一约定，得做更复杂的启发式。

---

## 2. AI 用例生成（对应 bullet 2）

### Q2.1 "LLM 优先、规则兜底"具体怎么跑？是并行的吗？

串行，且**按测试点粒度降级**，不是整体降级：

```python
# app/service/generation_service.py:429-440
if missing and mode in ("llm", "auto") and not fallback_reason:
    fallback_reason = f"{len(missing)} 个测试点未产出 LLM 用例，已用规则补齐"
    ...
    cases.extend(generate_rule_cases_for_points(missing, backend, frontend, machines))
```

逻辑是：LLM 跑完 → 对比期望覆盖的测试点 → 算出 `missing`（没产出或产出被拒的）→ 只对 `missing` 调规则生成器 → 记 `fallbackReason` 返回给前端。

> 为什么按测试点而不是整体降级：一个测试点 LLM 写崩了，不该把 20 个好的也丢掉。**降级粒度越细，浪费越少。**

### Q2.2 怎么保证 LLM 生成的用例"能执行"，而不是"看起来对"？

四道关，每道都能独立拒收：

| 关 | 做什么 | 代码位置 |
|---|---|---|
| 结构 | 必须 JSON、必须有 `testPointId`、首步必须是 `ui_action + route` | `case_review_service.py:214-217` |
| 事实 | 选择器/文案/锚点必须能在源码事实里查到 | `case_review_service.py:297-323` |
| 语义 | 复合预期、同字段多输入直接丢 | `quality_gate.py:214-220` |
| 兜底 | 不合法就丢弃该条，测试点交给规则补 | `generation_service.py:429` |

结构校验里有个细节值得说：**第一步必须导航**。因为 Playwright 打开的 `about:blank` 上什么都点不到，LLM 经常忘记写导航。

```python
# app/service/case_review_service.py:214-217
first = steps[0]
first_selector = first.get("uiSelector") or {}
if first.get("type") != "ui_action" or first_selector.get("type") != "route":
    errors.append("第一步必须是 ui_action + route")
```

### Q2.3 质量评分公式具体是什么？

100 分制，四项扣分，40 分以下丢弃：

```python
# app/agent/quality_gate.py:136-149
def score_case(case, lint_issues: list[str]) -> int:
    s = 100
    s -= min(8 * len(lint_issues), 40)          # 每个 lint 问题 −8，最多扣 40
    for step in case.structured_steps:
        sel = step.get("uiSelector") or {}
        if step.get("type") in ("ui_action", "input") and not sel.get("value"):
            s -= 10                              # 每个缺定位的步骤 −10（不封顶）
    refs = case.coverage_refs or {}
    if not any(refs.values()):
        s -= 10                                  # 完全没挂覆盖点 −10
    if len(case.structured_steps) > 15:
        s -= 10                                  # 步骤过长 −10
    return max(s, 0)
```

设计上有意做了区分：**lint 问题封顶 40，缺定位不封顶**。因为 lint（接口化描述、"显示正常"这种模糊断言）是可用性问题，缺定位是**能不能跑**的问题——两者不是一个量级。

另外，三个硬门槛在打分之前就拦掉了，根本不进评分：无预期结果、复合预期、同字段多输入。

### Q2.4 为什么死守"一个测试点一个场景"？这不限制覆盖吗？

不限制覆盖，反而保护可归因性。理由很具体：

> 一条用例"先填 20 字提交看成功，再改 21 字看报错"，执行器失败时截图只拍到最后一次输入。你拿到一张图、一个 error，**没法判断是哪个场景挂了**。

所以这不是审美问题，是证据问题。实现上做了两个独立的硬门槛：

```python
# app/agent/quality_gate.py:72-90
def steps_have_multiple_inputs(steps) -> bool:
    """同一字段被填入两个不同的值 → 一条用例在验证两个输入场景。"""
    seen: dict[str, str] = {}
    for step in steps or []:
        if step.get("type") != "input":
            continue
        key = str(selector.get("value") or step.get("target") or "").strip()
        value = str(step.get("inputValue") or "").strip()
        if key in seen and seen[key] != value:
            return True
        seen[key] = value
```

复合预期的判据是"引号锚点 ≥ 2"或"分号两侧各是一句完整中文"（`quality_gate.py:36-55`）。锚点 ≥ 2 的判定还顺带解决了一个执行器约束：`_do_assert` 要求**所有**锚点都命中才算通过，一条用例写两个锚点、分别属于两个场景，必然全失败。

### Q2.5 相似度去重用 0.95，为什么不是 0.85？

因为 0.85 会误杀合法用例。指纹是"标题 + 全部步骤 + 预期"归一化后算 `SequenceMatcher` 相似度：

```python
# app/agent/quality_gate.py:156-166
def _scenario_text(case) -> str:
    """去重指纹：标题 + 全部步骤（含输入值/断言）+ 预期结果。

    只用标题+首步骤会把「20字符合法」和「21字符超长」这类同点拆出的用例误判为重复。
    """
```

「收货人为空」和「手机号为空」这两条，标题结构几乎一样，0.85 会被合并成一条——但它们是两个不同的字段校验，合并就是覆盖丢失。0.95 只去掉几乎逐字相同的真重复。

**阈值是靠坏例子定的，不是拍的。** 这句话面试时可以强调。

### Q2.6 去重逻辑里为什么还有两个"直接返回 False"的豁免？

```python
# app/agent/quality_gate.py:177-193
def _is_duplicate(case, existing) -> bool:
    # 同一测试点拆出的合法/越界用例：标题高度相似但预期不同，属于两条合法用例
    if (case.test_point_id and case.test_point_id == existing.test_point_id
        and not _same_expected(case, existing)):
        return False
    # 不同测试点的用例即使步骤相同也保留：否则同页面的冒烟用例会被合并，
    # 导致其中一个测试点在覆盖矩阵里无对应用例
    if (case.test_point_id and existing.test_point_id
        and case.test_point_id != existing.test_point_id):
        return False
    return _similarity(case, existing) > 0.95
```

两条豁免对应两个真实故障：

1. **同点不同预期**：20 字合法 / 21 字超长，标题 95% 像，但预期不同，是两条用例。合并就丢边界。
2. **不同点同步骤**：两个测试点都走"打开列表 → 查询"，步骤完全一样。合并后其中一个测试点在覆盖矩阵里**没有任何用例挂靠**，覆盖率凭空掉一截。

> 去重不能只看文本相似度，必须带上**测试点归属**这个维度。纯文本去重会吃掉覆盖。

### Q2.7 LLM 幻觉怎么防？分几层？

三层，且我强调**第三层才是真正兜底的**：

| 层 | 手段 | 失效时的后果 |
|---|---|---|
| 输入约束 | 只喂代码事实，不给自由发挥空间 | 模型编造，但下游能查到 |
| 输出约束 | 选择器类型白名单 + 事实校验 + 锚点校验 | 报错拒收 |
| 执行约束 | 找不到元素 → `SELECTOR_NOT_FOUND`，不判 bug | 失败但归因正确 |

```python
# app/agent/execution_agent.py:227-231
elif stype == "ui_action":
    loc, attempted = _find_locator(page, step)
    if not loc:
        raise _StepError("SELECTOR_NOT_FOUND", f"元素未找到，尝试链: {attempted}")
```

注意错误消息里带了 **`attempted`（完整尝试链）**。这不是为了好看——人看日志时第一反应是"页面变了还是选择器错了"，把五级尝试链打出来才能区分。

### Q2.8 动态数据怎么处理？比如列表总数。

专门写了一个改写器 `count_assertion.py`。核心思路：**不写死"共 5 条"，改成执行前读基线、再断言"基线 + 1"**。

但它只在三个前提全满足时才动手，否则宁可不动：

```python
# app/agent/count_assertion.py:6-10
# 本模块只处理结构明确、可安全推导的场景：
# - 用例中存在创建/新增动作
# - 能在期望或页面事实中找到列表路由
# - 只有一个"共/总数/数量 N 条"断言
# 其余情况不改写，交给审核/人工处理，避免生成看似动态但不可执行的步骤。
```

运行时靠 `capture` 步骤抓变量，断言里写 `${base_count+1}`：

```python
# app/agent/execution_agent.py:27-47
def _resolve_variables(value, variables: dict[str, str]) -> str:
    """替换 ${var} 和简单整数加减；变量缺失或非数字时抛业务步骤错误，不静默跳过。"""
```

这里也有个设计取舍值得讲：**变量缺失直接抛 `CASE_INVALID`，不静默当成空串**。因为静默跳过会产出一个"看起来执行了、其实没验证"的假通过。

### Q2.9 规则兜底生成的用例质量会不会很差？规则占比多少？

规则生成器不是劣化版，它是**确定性穷举**：每个必填字段出一条"置空"负例，每个有 `@Size` 的字段出一条边界，每个 verified 的状态转换出一条流转用例。质量稳定但聪明度为 0——它不会想出"先加购再下单"这种跨页面场景。

规则占比我没有一个稳定的全局数字（每次生成波动很大，取决于 LLM 可用性和模型表现），所以**面试时不要报一个百分比**。可以给的是两个前端可见的口径：每条用例带 `source=ai/rule`，每批生成返回 `ruleCaseCount` 和 `fallbackReason`（`generation_service.py:563-566`），用户能自己看到这一批里规则补了多少。

### Q2.10 你们调模型踩过哪些具体的坑？

这块是真实的工程细节，讲出来很能加分：

```python
# app/llm/client.py:163-167
if ("glm-5.3" in model or "glm-5.2" in model) and "reasoning_effort" not in self.extra_body:
    # GLM-5.3/5.3-flash 强制思考且默认 max。长 JSON 任务会把 max_tokens
    # 全部花在 reasoning_content 上，导致 content 为空、finish_reason=length。
    extras["reasoning_effort"] = "low"
```

四类坑，四套兜底：

| 坑 | 现象 | 处理 |
|---|---|---|
| 推理模型吃满 token | `content` 空、`finish_reason=length` | 提预算重试一次（`client.py:192-203`） |
| 答案只在 `reasoning_content` 里 | `content` 为 None | 降级接受 reasoning（`client.py:247-257`） |
| 供应商不认扩展参数 | HTTP 400 / 422 | 依次去掉 json_mode、extra_body 重试（`client.py:266-303`） |
| 输出被截断 | JSON 不闭合 | 截到最后一个完整对象再闭合数组（`client.py:119-134`） |

还有一个：温度固定 0.2（`client.py:223`）。测试用例生成要的是稳定复现，不是创意。

```python
# app/llm/client.py:191-203
# 思考过程吃满 token：提升预算后重试一次，避免直接降级规则。
if self.last_finish_reason == "length" and max_tokens < 32768:
    bigger = min(max(max_tokens * 2, 8192), 32768)
```

> 顺序很重要：**先重试、再降级**。早期一遇到 length 就直接走规则，白丢一批本来能救回来的 LLM 用例。

---

## 3. UI 自动化执行（对应 bullet 3）

### Q3.1 "多级元素定位"具体几级？顺序为什么这么排？

五级，从精确到宽松：

```python
# app/agent/execution_agent.py:74-104
def _fallback_chain(step: dict) -> list[str]:
    """五级兜底链（6.3.3）。"""
```

1. `uiSelector` 精确匹配（placeholder / label / role / testid / text）
2. `target` 文本在 `button / a / [role=button]`
3. Element Plus 下拉选项（`.el-select-dropdown__item`，渲染到 body 的 popper 里）
4. 整页文本 `body :not(option):not(script):not(style)`
5. `normalize-space` XPath

排序原则是**误命中概率递增**。第 3 级单独拆出来是因为 Element Plus 的 select 下拉不在原 DOM 位置——它 teleport 到 body 末尾了，用普通选择器永远找不到。第 5 级是为了对付两字按钮被渲染成"登 录"（中间插了空格），精确匹配失败、`normalize-space` 能过。

### Q3.2 这套兜底链有什么副作用？会不会点错东西？

会，而且我知道在哪。

第 4 级 `body ... :has-text("取消")` 命中的可能是外层容器而不是按钮，Playwright 会点到容器的中心点，结果不可控。这是当前设计的最大脆弱点。

**为什么不删掉第 4 级**：删了之后在非 Element Plus 站点上召回率会掉很多，而测试平台的假红比假绿更消耗信任——假红会让人不信平台，假绿会让人漏 bug，两个都坏，但假红更容易发生。

**正确的改法**（如果重做）：把兜底从"字符串链"改成"候选打分"——每个候选算一个置信度（精确选择器 +1.0，文本匹配 +0.5，容器命中 +0.1），低于阈值不点、直接 `SELECTOR_NOT_FOUND`。现在是"第一个可见的就用"，这是能理解的简化，不是最优解。

### Q3.3 "分层断言"三层分别是什么？

```python
# app/agent/execution_agent.py:122-138
def _do_assert(page, expected: str) -> tuple[bool, str, str]:
    """三层断言（6.3.4）：URL → 引号锚点文本 → 诚实 skipped。"""
    # 第 1 层 URL 断言
    if re.search(r"URL|跳转|路由|页面地址|/[\w/]+", expected):
        path_m = re.search(r"(/[\w/{}]+)", expected)
        if path_m and path_m.group(1) in page.url:
            return True, "passed", f"URL 命中: {path_m.group(1)}"
    # 第 2 层 DOM 文本断言：引号锚点
    anchors = QUOTE_ANCHOR_RE.findall(expected)
    if anchors:
        text = page.inner_text("body")
        missing = [a for a in anchors if a not in text]
        if not missing:
            return True, "passed", f"文本锚点命中: {anchors}"
        return False, "failed", f"文本锚点缺失: {missing}"
    # 第 3 层 诚实 skipped
    return False, "skipped", "无可验证的断言锚点"
```

为什么不直接断言 DOM 结构：结构断言（`div > span:nth-child(3)`）和业务语义没有对应关系，改个样式就红，这是最典型的 flaky 测试来源。**断言语义（文案、路由），不断言结构。**

锚点规则和生成侧是同一个正则（`quality_gate.py:27` 与 `execution_agent.py:20` 一致），保证"生成时校验的锚点"就是"执行时断言的锚点"，不会出现两道规则不一致的裂缝。

**这里我主动交底一个缺陷**：URL 断言命中不了时，代码会**继续往下走**，如果没有 `「」`锚点，最终返回的是 `skipped` 而不是 `failed`。严格说应该是 failed——URL 是可验证的，没命中就是没通过。这是我已知要修的，修法是 URL 断言独立返回三态。

### Q3.4 为什么"没报错"不能算通过？

因为"没报错"只证明动作执行了，不证明结果对了。点了一个按钮没抛异常，页面完全可能弹了个"操作失败"。

所以状态汇总的顺序是"坏消息优先"：

```python
# app/agent/execution_agent.py:340-347
if record["errorCategory"]:
    record["status"] = "failed"
elif any(s["result"] == "failed" for s in step_results):
    record["status"] = "failed"
elif any(s["result"] == "skipped" for s in step_results):
    record["status"] = "skipped"
else:
    record["status"] = "passed"
```

`skipped` 的语义是"跑完了，但我没能力证明它对"。**这个状态存在的唯一目的就是不要伪装成 passed。**

### Q3.5 六类错误分类是哪六个？分类的价值到底是什么？

`app/common/execution_enums.py` 的 `ERROR_CATEGORIES`：`SELECTOR_NOT_FOUND / ASSERT_FAILED / NAVIGATION_FAILED / TIMEOUT / CASE_INVALID / ENV_ERROR`。

价值在于**分流给不同的人**：

| 错误 | 大概率是谁的问题 | 该谁修 |
|---|---|---|
| `SELECTOR_NOT_FOUND` | 生成器选择器错 / 页面改了 | 平台 or 前端 |
| `ASSERT_FAILED` | 业务 bug | 开发 |
| `CASE_INVALID` | 用例本身不合法 | 生成器 |
| `TIMEOUT` | 性能 or 环境 | 运维/开发 |
| `ENV_ERROR` | 浏览器/驱动 | 测试环境 |
| `NAVIGATION_FAILED` | 路由/服务不可达 | 开发/运维 |

没有分类的时候，30 条失败就是 30 条"用例失败"，只能一条条看日志。有分类之后，20 条失败一眼看出 9 条 `SELECTOR_NOT_FOUND`——这不是被打测系统坏了，是我的生成器错了。**归因错了，修复方向就全错。**

### Q3.6 截图和录像怎么采的？开销多大？

```python
# app/agent/execution_agent.py:195-198
browser = p.chromium.launch()
context = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(out))
page = context.new_page()
page.set_default_timeout(10000)
```

录像靠 `record_video_dir`，每步前后各一张 PNG（`step-01-before.png` / `step-01-after.png`），失败步骤额外保证有 after 图。视频文件名 Playwright 是随机的，执行完做一次重命名（`execution_agent.py:328-338`）。

开销：一张 1440×900 PNG 约 100–300KB，一条 8 步用例约 16 张图 + 一个几 MB 的 webm。所以 VLM 审核必须挑图（见 Q4.1），不能全传。

### Q3.7 为什么用同步 API，不用 async？并发怎么解决？

三个理由：
1. 执行跑在后台线程里（Pipeline 是后台线程），同步 API 天然不阻塞事件循环；
2. `sync_playwright` 代码线性、每一步失败即停，可读性对调试很关键；
3. 单机场景串行执行够用——真上规模时，瓶颈是浏览器实例数不是协程数。

代价也很清楚：**批量执行是串行、上限 100 条**，并且服务重启后可能留下 `running` 状态的僵尸记录（README 的"边界与约束"里已经写明）。要做的是任务队列 + worker 池 + 浏览器池，这是 Roadmap 里的事，不是现在的实现。

### Q3.8 "刷新页面"这种动作截图根本看不出来，你怎么证明它真的执行了？

这是我比较满意的一个设计。用 Navigation Timing 拿浏览器自己的证据：

```python
# app/agent/execution_agent.py:148-167
def _reload_and_verify(page) -> dict:
    """执行真实浏览器刷新，并用 Navigation Timing 生成可审计证据。"""
    page.reload(wait_until="load")
    navigation = page.evaluate(
        """() => {
          const entries = performance.getEntriesByType('navigation');
          const nav = entries && entries.length ? entries[entries.length - 1] : null;
          return { type: nav ? nav.type : '', url: location.href };
        }"""
    )
    navigation_type = str((navigation or {}).get("type") or "")
    if navigation_type != "reload":
        raise _StepError("NAVIGATION_FAILED", f"浏览器刷新未通过 Navigation Timing 验证，实际类型: {navigation_type}")
```

刷新的 `navigation.type` 是 `reload`，首屏是 `navigate`。这不是"我说刷新了"，是浏览器自己报的。

而且这个证据会写进步骤记录，VLM 也能看到（`review_agent.py:106-111` 的 prompt 规则 3 专门写了：看到 `performance.navigation.type=reload` 就要认可刷新动作，不要因为截图看不见刷新过程就判 suspect）。**确定性证据优先于视觉证据**。

### Q3.9 失败之后为什么立刻停？不重试吗？

```python
# app/agent/execution_agent.py:321-323
# 失败即停（不继续无意义步骤）
if result == "failed":
    break
```

失败后继续跑没有意义——前置动作失败了，后面的断言必然连带失败，只会制造噪声、拉长执行时间、多拍一堆无用截图。

**不自动重试是刻意的**。UI 自动化的经典反模式是"失败重试 3 次，任一次过就算过"，这会把真 bug 重试掉、把 flaky 洗成绿。我的选择是：失败就暴露，由人来判断。

---

## 4. 多模态 AI 审核（对应 bullet 4）

### Q4.1 截图怎么挑？为什么最多 6 张？

```python
# app/agent/review_agent.py:36-71
def _pick_images(steps: list[dict], max_images: int = MAX_IMAGES) -> list[dict]:
    """挑选关键截图：失败步骤（前/后）→ 最后一步 → 断言步骤 → 第一步，最多 6 张。"""
```

优先级：**失败步骤的前后图**（最可能有问题）→ 最后一步 → 断言步骤 → 第一步 → 剩余按序补齐。

上限 6 张（`review_agent.py:23`）的理由是三重约束：多模态 token 成本、单次请求延迟、以及"图太多模型注意力涣散"——实测给 16 张图时模型会漏看中间几张，反而降低审核质量。**少而准比全而糊好。**

### Q4.2 VLM 的输出结构是什么？

```python
# app/agent/review_agent.py:130-131
只输出 JSON：
{{"verdict":"ok|suspect","reason":"不超过 120 字的中文结论","images":[{"image":1,"verdict":"ok","analysis":"..."}],"issues":[{"image":1,"step":2,"bbox":[0.12,0.35,0.78,0.52],"problem":"具体问题"}]}}
```

三层：总 verdict（三态）、逐图分析（每张图给 ok/suspect + 一句话）、问题列表（带图号、步骤号、归一化 bbox、问题描述）。

逐图分析这个字段是产品需求的产物：人工复核的人拿到的不该是一个 `suspect` 标签，而是"第 3 张图里订单列表最后一行的状态还是待支付"。**没有定位的告警等于没告警。**

### Q4.3 bbox 坐标怎么保证画对位置？模型给的坐标不准怎么办？

模型经常搞混"归一化坐标"和"像素坐标"。代码做了一次鲁棒的归一化：

```python
# app/agent/review_agent.py:148-172
def _normalize_bbox(raw, path: str) -> list[float] | None:
    ...
    size = _png_size(path)
    # 模型偶尔返回像素坐标：按图片尺寸折算
    if any(v > 1 for v in vals):
        if size:
            w, h = size
            vals = [vals[0] / w, vals[1] / h, vals[2] / w, vals[3] / h]
        else:
            return None
    ...
    if x2 - x1 < 0.02 or y2 - y1 < 0.02:
        return None
```

三件事：① 任一值 > 1 就按像素处理，用 PNG 头读尺寸折算（`_png_size` 直接解 PNG header，不引 Pillow，少一个依赖）；② 坐标反了就交换；③ 框太小（<2%）直接丢弃——那种框一定是模型瞎猜的，画上去只会误导人。

### Q4.4 VLM 误判怎么办？它说 ok 就一定 ok 吗？

不一定，所以它的权力被代码限制了。一共有四道约束：

1. **prompt 里明确禁止从众**：`review_agent.py:114` —「不要因为步骤结果写着 passed 就直接判 ok，必须自己看截图」
2. **证据不足必须 suspect**：`review_agent.py:112` —「若截图不足以证明其他预期结果，也判 suspect，不要放过」
3. **漏图直接降级**：模型只分析了 4 张、实际给了 6 张 → 整体 suspect
4. **AI 不能把 failed 改成 passed**（见 Q4.5）

第 3 条的实现：

```python
# app/agent/review_agent.py:244-261
def _summarize(raw_verdict, image_reviews, issues, image_count):
    """汇总模型结论，任何异常证据都会把总 verdict 降级为 suspect。"""
    ...
    reviewed = {item.get("imageIndex") for item in image_reviews}
    if len(reviewed) < image_count:
        verdict = VERDICT_SUSPECT
        reasons.append(f"仅分析了 {len(reviewed)}/{image_count} 张关键截图")
```

> 这个设计的前提假设是：**模型的结论可以是保守的，但不能是乐观的**。少看一张图就说 ok，比多看一张图说 suspect 危险得多。

### Q4.5 AI 能直接判定用例通过吗？状态怎么迁移？

不能。这是整个项目的底线，代码里用一张显式映射表钉死了：

```python
# app/service/execution_service.py:173-190
def _decide_status(original_status: str, error_category: str | None, verdict: str) -> str:
    """AI 结论 → 最终状态。

    确定性执行结论优先：AI 可以确认通过或升级为人工复核，但不能把明确的
    failed/cancelled 改判为 passed。只有原始执行无确定失败时，AI ok 才能
    为 skipped（缺少文本锚点）提供补充证据。
    """
    if original_status in ("failed", "cancelled"):
        return original_status
    if original_status == "passed":
        return "passed" if verdict == VERDICT_OK else "needs_review"
    if original_status == "skipped":
        if verdict == VERDICT_OK:
            return "passed"
        if verdict in (VERDICT_SUSPECT, VERDICT_UNAVAILABLE):
            return "needs_review"
    return "needs_review"
```

迁移表：

| 确定性结果 \ AI | ok | suspect / unavailable |
|---|---|---|
| passed | passed | **needs_review**（降级） |
| skipped | **passed**（补证） | needs_review |
| failed | **failed**（不许改） | failed |

只有 `skipped → passed` 是"升级"，而且这个升级是合理的：执行器说"我找不到可断言的锚点"，VLM 说"但我从截图里看到了预期结果"——这是**补证据**，不是**覆盖事实**。

### Q4.6 这套设计里最容易被挑战的点是什么？

最可能被问："AI 既然能看截图，为什么不让它直接判？很多测试平台都是这么做的。"

我的回答是三个层次：

1. **不可复现**。同一个页面，模型两次可能给不同结论；确定性执行给的是同一答案。测试结论必须可复现，否则没法当发布依据。
2. **不可审计**。执行结论要能回答"为什么这条是失败"，答案应该是"步骤 4 断言 `「订单创建成功」` 不在页面文本里"，而不是"模型觉得有问题"。
3. **权责错位**。判断业务对不对是人的责任，模型只能提供线索。让它定生死，出了线上事故，责任归属是空的。

> 一句话：**模型负责"帮我看见"，不负责"帮我决定"。**

### Q4.7 成本和时间怎么控制？

四层：
1. 只挑 6 张关键图，不传录像（`review_agent.py:23`）；
2. 单图 > 8MB 直接跳过（`MAX_IMAGE_BYTES`）；
3. 审核是执行后的**增强步骤**，失败/超时 → `unavailable`，不影响已落库的确定性结论；
4. 批量审核串行、单次最多 20 条（`execution_service.py:660-668`），单条失败不中断批次。

要上规模的话，第一步是把审核挪到异步队列，不占执行线程——这是现在的明确短板，我不会说它已经解决了。

### Q4.8 视觉模型选型和 prompt 有什么讲究？

VLM 和文本 LLM 是**分开配置**的两套 profile（`LLM_VISION_*` 系列环境变量），因为多模态模型贵得多，不该让所有任务都走它。没配 VLM 时回退文本 LLM，都没有就返回 `None` 不审核——**不审核比乱审核好**。

prompt 里有两个针对"模型过度保守"的专门条款，都是踩坑加的：

```python
# app/agent/review_agent.py:116-119
确定性断言证据的补充规则：
- 若步骤记录包含「断言证据：文本锚点命中」或「断言证据：URL 命中」，说明 Playwright 已做过
  精确检查。除非截图出现矛盾（报错、空白页、错误页面、相反状态），不要仅因截图分辨率、
  文案在截图中不够醒目或"截图没有完整展示该文案"就判 suspect。
```

反过来的坑：早期 prompt 只写"证据不足判 suspect"，结果模型对每一条都判 suspect——因为截图本来就不可能完整展示所有细节。于是加了这条：**确定性断言已经验过的，视觉不要重复质疑，只在出现矛盾时推翻。**

> 这是双模型协作里最重要的一条经验：**两个判定器要划分清楚"谁负责证真、谁负责证伪"，否则会互相否定到全量 suspect。**

---

## 5. 横向压力题

### Q5.1 现在有 Midscene.js、Testim 这类 AI 测试工具，你这个有什么不一样？

它们主要解决"**怎么找到元素**"（用视觉/自然语言定位代替选择器）；我解决的是"**用例从哪来、结果凭什么可信**"。

具体差别三条：
1. **用例来源不同**：它们从自然语言指令出发，我从源码事实 + 需求文档出发。源码给了字段约束、状态机，这是自然语言里没有的信息。
2. **可信度定位不同**：它们的 AI 在**执行路径**上；我的 AI 被限制在生成和审核，**执行路径是确定性的**。
3. **我的诚实边界**：论元素定位的鲁棒性，Midscene 的视觉定位肯定比我强——我的五级兜底链是工程妥协。如果可以用，我会在执行层引入视觉定位，但结论层仍然不交给模型。

### Q5.2 覆盖率怎么算才不虚高？

两个口径分开算，这是我特意设计的：

- **规划覆盖**：用例的 `coverageRefs` 是否引用了真实接口 ID、verified 的状态转换 ID、业务规则 ID；
- **执行成功覆盖**：只统计 `status=passed` 且**属于当前实时用例**的执行记录。

第二条是关键。旧版本用例被新生成替换后，它的历史 passed 记录不再贡献覆盖率——否则反复生成、反复执行，覆盖率会越滚越高，但实际覆盖的东西没变。**覆盖率分母必须跟着用例生命周期走。**

### Q5.3 你自己觉得这个项目最大的问题是什么？

三个，按严重程度：

1. **没有精确率/召回率评估**。我能说"144 个测试通过""30 条用例 25 通过 5 待复核"，但我说不出"生成的用例有多少比例是真正有效的"。这是缺一个标注集的问题。
2. **兜底链会误命中**（Q3.2）。第 4/5 级是容错也是隐患。
3. **前端没有单元测试**，只有构建校验。

如果面试官问"你怎么知道自己做得好"，我的答案是：**我知道它在真实项目上把一个 20 条失败的批次修到了 0 失败，但我也知道我没有系统性的质量指标。前者是证据，后者是欠缺，两个都该说。**

### Q5.4 给你两周，你先改什么？

- **第一周**：修 URL 断言失败落在 `skipped` 的缺陷（Q3.3）；把兜底链改成候选打分 + 阈值拒点（Q3.2）；给 `verified` 拆成写入证据 + 守卫推导两维（Q1.5）。
- **第二周**：建一个小规模标注集（50 个测试点人工标期望用例数），跑出精确率/召回率，哪怕是粗的；然后把 AI 审核挪到异步队列。

顺序逻辑：**先修真问题，再补指标，最后谈性能。** 指标是用来证明改进的，得先有改进。

### Q5.5 用一句话概括这个项目

> 不是让 AI 写测试，而是**用代码事实约束 AI、用真实执行证据兜底，把 AI 关在"生成"和"解释"两个笼子里，不让它碰"判定"。**

---

## 6. 数字台账（只报这些，能核实）

| 数字 | 值 | 出处 |
|---|---|---|
| 后端测试数 | **144 collected** | `pytest --collect-only` 实测 |
| 数据表 | 23 张 | README / `app/entity/models.py` |
| 质量闸扣分规则 | lint −8/条（封顶 −40）、缺定位 −10/步、无覆盖点 −10、>15 步 −10、<40 丢弃 | `quality_gate.py:136-149` |
| 去重阈值 | 0.95 | `quality_gate.py:193` |
| 定位兜底级数 | 5 级 | `execution_agent.py:74-104` |
| 断言层数 | 3 层 | `execution_agent.py:122-138` |
| 错误分类 | 6 类 | `common/execution_enums.py` |
| VLM 单次图上限 | 6 张 / 单图 8MB | `review_agent.py:23-24` |
| LLM 温度 | 0.2 | `llm/client.py:223` |
| 修订轮次上限 | 2 轮，超限转 reject | README |
| V3 订单系统结果 | 25 passed / 5 needs_review / 0 failed（修复前 20 failed） | 你自己实测，讲的时候说清是**一次特定版本**的结果 |

> 不要报的：接口覆盖率、规则覆盖率的百分比（这两个值恒为 0，见下）、任何 LLM 生成占比的百分比。

---

## 7. 最后三条红线

1. **不要说"AI 全自动判定通过"**——`_decide_status` 里 failed 是不可逆的。
2. **不要说"VLM 输出置信度并参与判定"**——`confidence` 列已废弃，值恒为 NULL。
3. **不要说"源码是 AI 解析的"**——`spring.py` 第一行就写着「纯规则，零 LLM」。
