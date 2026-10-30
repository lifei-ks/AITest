# AITest 架构学习与面试讲解指南

> 面向准备面试、需要把 AITest 讲清楚的人。
>
> 读法建议：
> 1. 先背第 1 节的“两分钟版本”，确保任何场合都能开口。
> 2. 再看第 3～6 节的架构和设计决策，能画图。
> 3. 用第 9 节的 V3 真实故障复盘当“项目难点”。
> 4. 最后用第 12～13 节做模拟问答。
>
> 配套材料：详细缺陷清单见 [`CODE-REVIEW-2026-10-26.md`](./CODE-REVIEW-2026-10-26.md)。

---

## 1. 一句话定位与两分钟版本

### 一句话

**AITest 是一个面向 Web 业务系统的自动化测试生成与执行平台：它把需求文档和源码静态分析得到的“代码事实”转成结构化测试点和可执行用例，再经过确定性质量闸与 LLM 审核，最后用 Playwright 在真实浏览器执行，并产出覆盖矩阵和证据报告。**

### 两分钟版本（可直接背）

> 我做的是一个 AI 辅助的 Web 自动化测试平台。它解决的问题是：手工写 UI 用例成本高、和代码变更脱节，而纯 LLM 生成用例又不可复现、容易编造选择器和预期。
>
> 我的做法是把系统分成两层。底层是确定性内核：静态分析 Spring Controller、实体约束和 Vue 表单，得到路由、字段、选择器、接口、状态机这些“代码事实”；再用规则生成测试点和结构化用例，经过质量闸过滤掉复合场景和不可验证断言；最后用 Playwright 真实执行，用三层断言判定通过、失败或“证据不足”。
>
> 上层是 LLM 增强：测试点、用例参数、审核建议和截图语义可以调用大模型，但模型输出必须被代码事实约束，而且**模型不能决定执行是否通过**。确定性失败不能被 AI 改成通过。
>
> 平台支持规则、LLM、自动三种生成模式，LLM 不可用时自动降级规则；还有一键 Pipeline，从代码分析、测试点、用例生成、审核、执行到报告全链路打通。真实订单系统 V3 上，30 条用例从 20 条失败修到 25 条通过、5 条待复核、0 条失败。
>
> 我认为这个项目最有价值的不是“用了大模型”，而是**把不确定性关进笼子**：代码事实约束生成、质量闸约束结构、执行器约束证据，AI 只做增强。

---

## 2. 为什么需要这个系统

### 2.1 传统 UI 自动化的痛点

| 痛点 | 具体表现 |
|---|---|
| 编写成本高 | 一个业务表单要写定位、输入、提交、断言、等待 |
| 与代码脱节 | 页面字段/接口变了，用例不会自动更新 |
| 维护困难 | 选择器一变，大量用例红 |
| 覆盖靠人 | 不知道哪些接口/状态/规则没测 |
| 结果不可信 | 超时、元素找不到、断言太弱，绿了也不知道是不是真绿 |

### 2.2 纯 LLM 方案的问题

| 问题 | 后果 |
|---|---|
| 幻觉选择器 | 生成 `.btn-primary`、`#submit` 这类页面不存在的定位 |
| 幻觉业务规则 | 编造“金额必须大于 100”这类需求没写的约束 |
| 不可复现 | 同一需求两次生成结果不同，无法回归 |
| 自己判自己 | 让模型看截图说“通过”，没有确定性证据 |
| 成本/延迟 | 每次生成都调用模型，超时和限流会拖垮流程 |

### 2.3 AITest 的答案

```
代码事实（确定性）  →  约束 LLM  →  结构化用例
       ↓                              ↓
   质量闸（确定性）  →  真实执行（确定性）  →  证据
       ↓                              ↓
   AI 只做语义审核/解释，不决定通过/失败
```

一句话：**确定性的部分绝不交给模型，模型只做“语义增强”。**

---

## 3. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│ 输入层                                                            │
│  需求文档（PRD / Markdown / PDF / Word）   源码目录   被测站点 URL  │
└──────────────────────────────┬───────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 静态分析层（确定性）                                               │
│  scanner：按扩展名扫描                                             │
│  spring.py：Controller / 接口 / 实体 / 约束 / 状态流转             │
│  vue.py：页面 / 表单 / 字段 / 选择器 / 页面文案                    │
│  输出：BackendResult / FrontendResult / StateMachine              │
└──────────────────────────────┬───────────────────────────────────┘
                               ↓ 代码事实 JSON
┌──────────────────────────────────────────────────────────────────┐
│ 生成层                                                            │
│  测试点 Agent / 规则生成器  →  TestPoint                          │
│  用例 Agent / 规则生成器    →  结构化 TestCase                    │
│  质量闸：复合预期 / 多输入 / 接口化描述 / 打分 / 去重              │
│  生成版本快照：每次生成一个 CaseGeneration                        │
└──────────────────────────────┬───────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 审核层                                                            │
│  确定性审核：必填字段、复合预期、选择器/锚点事实校验               │
│  LLM 审核：语义合理性、风险建议（可降级）                          │
│  只有 review_status=pass 且 automation_status=automated 才执行     │
└──────────────────────────────┬───────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 执行层（确定性）                                                   │
│  Playwright Chromium                                              │
│  定位兜底链 → 输入/点击 → 三层断言 → capture 动态变量              │
│  截图 before/after + 录屏 + 步骤记录                              │
│  错误分类：SELECTOR_NOT_FOUND / ASSERT_FAILED / ...               │
└──────────────────────────────┬───────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────┐
│ 结果层                                                            │
│  执行状态机 → AI 截图审核（只做辅助） → 覆盖矩阵 → HTML 报告/脑图  │
│  Pipeline：7 个阶段持久化，可追踪、可恢复                          │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 两个核心分层

| 层 | 职责 | 特性 |
|---|---|---|
| 确定性内核 | 静态分析、状态机、质量闸、数量改写、执行器、覆盖统计 | 可复现、可审计、无模型依赖 |
| LLM Agents | 测试点、用例参数、审核建议、截图语义、结果分析 | 可选、可降级、输出必须结构校验 |

**面试时要强调：平台不会让模型直接决定“执行是否通过”。执行状态来自真实浏览器结果，模型只能解释证据。**

---

## 4. 端到端数据流

以订单系统 V3 为例：

| 阶段 | 输入 | 处理 | 输出/落库 |
|---|---|---|---|
| 代码分析 | 源码目录 | scanner → Spring/Vue 分析 | `code_analyses`（backend/frontend JSON）、`state_machines` |
| 需求解析 | V3 需求文档 | 规则/LLM 提取测试点 | `requirements`、`test_points` |
| 用例生成 | 测试点 + 代码事实 | 规则/LLM 生成结构化步骤 | `test_cases`、`requirement_cases`、`case_generations` |
| 质量闸 | 候选用例 | 复合/多输入/接口化/低分/重复过滤 | 通过的用例 + `quality_score`/`lint_issues` |
| 用例审核 | 用例 + 页面事实 | 确定性校验 + LLM 审核/修订 | `workflow_cases`、`workflow_case_reviews` |
| 自动执行 | 审核通过的用例 | Playwright 执行 | `execution_records`、`execution_steps`、截图/录屏 |
| 结果分析 | 执行记录 | 状态机 + AI 截图审核 + 根因归类 | `execution_reviews`、`workflow_result_analyses` |
| 报告 | 执行/覆盖数据 | Jinja2 渲染 + 覆盖矩阵 | HTML 报告、脑图 |

### 4.1 生成版本隔离

- 每次生成创建一个 `CaseGeneration` 快照（标题/类型/优先级/预期/来源/质量分/测试点）。
- 页面可以选择“实时用例（最新）”或某个历史生成版本。
- 实时用例被重新生成替换后，历史版本仍可只读查看。
- 早期没有版本记录的用例，首次查看时自动归档成 `legacy` 版本。
- 增量分析功能已移除；旧的 `incremental` 生成版本不再出现在历史列表。

---

## 5. 核心模块

### 5.1 静态分析器 `app/analyzer/`

| 文件 | 职责 | 关键点 |
|---|---|---|
| `scanner.py` | 按扩展名扫描源码 | 统一文件列表，过滤无关目录 |
| `spring.py` | 解析 Spring Controller/实体/约束/状态流转 | `@GetMapping` 等映射、`@NotBlank/@Size`、`throw new BizException("...")` |
| `vue.py` | 解析 Vue 页面/表单/选择器/文案 | `el-form`、`el-input` placeholder、按钮文案 |
| `models.py` | 分析结果数据类 | `BackendResult` / `FrontendResult` / `Endpoint` / `Page` / `FieldConstraint` |

**面试点**：静态分析不是“看懂所有代码”，而是提取“能约束生成”的事实：路由、字段、必填、选择器、接口、状态边。事实不全时，平台宁可少生成，也不编造。

### 5.2 Agent 层 `app/agent/`

| 文件 | 职责 |
|---|---|
| `test_point_agent.py` | LLM 从需求提取测试点 |
| `test_generator_agent.py` | 规则生成用例（兜底、列表/统计/批量分支） |
| `test_case_agent.py` | LLM 按测试点生成结构化用例 |
| `quality_gate.py` | 闸 0 + Linter + 打分 + 去重 |
| `state_machine_agent.py` | 从 Controller 代码提取状态流转 |
| `count_assertion.py` | 固定总数断言 → capture 基线 + `${base_count+1}` |
| `execution_agent.py` | Playwright 执行器 |
| `review_agent.py` / `case_review_agent.py` | 用例审核/修订 |
| `result_analysis_agent.py` | 执行结果根因分析 |

### 5.3 服务层 `app/service/`

| 服务 | 职责 |
|---|---|
| `analysis_service.py` | 触发代码分析，保存分析结果 |
| `requirement_service.py` | 需求版本、PRD 上传、测试点解析 |
| `generation_service.py` | 生成编排、LLM/规则切换、版本快照、质量闸、落库 |
| `execution_service.py` | 单条/批量执行、批次、AI 审核、执行记录 |
| `workflow_service.py` | 7 阶段 Pipeline 编排、审核、自动执行、报告 |
| `coverage_service.py` | 规划覆盖 vs 执行成功覆盖 |
| `report_service.py` | HTML 报告 |
| `settings_service.py` | 文本/视觉 LLM 多 Profile |

### 5.4 前端 `frontend/src/`

| 目录/文件 | 职责 |
|---|---|
| `views/ProjectDetailView.vue` | 项目详情 8 个 Tab：概览/分析/需求/生成/用例/覆盖/脑图/执行 |
| `views/PipelineView.vue` | 一键 Pipeline 执行 |
| `components/GenerationTab.vue` | 生成模式、LLM 选择、SSE 进度、版本选择 |
| `views/TestcasesView.vue` | 用例列表、筛选、编辑、版本、批量执行 |
| `components/ExecutionsTab.vue` | 执行记录、步骤、截图、审核 |
| `components/CoverageTab.vue` | 覆盖矩阵/执行成功矩阵 |
| `api/request.js` | Axios 统一信封与错误处理 |
| `stores/config.js` | 当前项目/配置状态 |

---

## 6. 关键设计决策（面试重点）

### 6.1 规则内核 + LLM 增强，而不是纯 LLM

- 规则可复现、可测试、无 Key 也能跑。
- LLM 只负责“语义增强”：测试点更自然、用例参数更合理、审核建议更丰富。
- LLM 不可用/超时/返回非法 JSON 时自动降级规则。
- 生成结果标注 `source=rule/ai`，用户能区分。

**面试话术**：纯 LLM 的问题不是“不够聪明”，而是“不可审计”。测试平台的第一诉求是可信，第二才是智能。

### 6.2 一个测试点 = 一个输入场景 + 一个可观察断言

质量闸会直接丢弃：

- 复合预期：一条用例里有两个「」锚点，或分号并列多个结果。
- 同一字段填两个不同值：证据只保留最后一次输入，失败无法定位。
- 接口化描述：“调用接口/验证数据库”无法用 UI 执行器验证。
- 太弱的断言：“显示正常/符合预期”。

**为什么**：一条用例只验证一个变量，失败时才能归因；截图/录屏证据才不会被后续步骤覆盖。

### 6.3 代码事实约束 LLM

生成 LLM 的 prompt 里只放：

- 页面路由、字段、placeholder、必填约束
- 真实选择器类型（route/placeholder/text/label/role/testid）
- 真实接口、状态机、页面文案

LLM 步骤只允许这些选择器类型，禁止 css/xpath；生成后还会校验锚点数量、输入数量、复合预期。**模型不能凭空发明页面元素。**

### 6.4 三层断言 + 诚实 skipped

执行器断言顺序：

1. **URL 断言**：预期里有路由/跳转，检查当前 URL。
2. **文本锚点断言**：预期里有「」锚点，检查 `body.inner_text()`。
3. **skipped**：没有可验证锚点，返回 `skipped`，不判 passed。

**面试话术**：最大的诱惑是“没报错就算通过”，平台明确拒绝。验证不了就标 skipped，让 AI/人工复核，而不是假绿。

### 6.5 动态数据断言：capture 基线，而不是写死总数

共享数据环境里，“共 5 条订单”下次执行可能是 7 条。`count_assertion.py` 会：

- 找到唯一的总数断言；
- 确认前面有创建/新增动作；
- 插入 `capture` 步骤读取 `base_count`；
- 把断言改成 `共 ${base_count+1} 条订单`。

没有创建动作、无法确定列表路由、存在多个总数断言时，宁可丢弃该用例，也不生成不可执行的“动态”步骤。

### 6.6 执行失败分类，而不是一个“失败”

6 个稳定错误类别：

| 类别 | 含义 |
|---|---|
| `SELECTOR_NOT_FOUND` | 兜底链也找不到元素 |
| `ASSERT_FAILED` | URL/文本锚点不满足预期 |
| `NAVIGATION_FAILED` | 页面打不开/4xx/5xx |
| `TIMEOUT` | 操作/等待超时 |
| `CASE_INVALID` | 用例缺步骤/输入/选择器 |
| `ENV_ERROR` | 浏览器/驱动/环境问题 |

**价值**：可以按类别统计“最近失败是生成问题、应用问题还是环境问题”，而不是笼统说“挂了”。

### 6.7 执行状态机：AI 不能把 failed 改成 passed

`_decide_status` 的原则：

- 原始 `failed/cancelled` 保持不变。
- 原始 `passed`，AI 认为有问题 → `needs_review`。
- 原始 `skipped`，AI 确认证据 → 可以 `passed`，否则 `needs_review`。

**面试话术**：AI 可以“升级人工复核”，但不能“把失败洗成通过”。

### 6.8 生成版本快照，而不是覆盖

- 每次生成一个只读 `CaseGeneration`，实时用例始终指向最新。
- 历史版本可查看、可对比、可作为回归基线。
- 重新生成不会把旧用例“合并”到一起，解决重复和串版本问题。

### 6.9 Pipeline 阶段持久化

7 个阶段：`code_analysis → test_points → case_generation → case_review → execution → result_analysis → report`。

每个阶段落一条 `WorkflowStage`，记录状态、耗时、输入/输出摘要、错误。失败阶段保留，便于定位；服务重启有回收逻辑。

---

## 7. 数据模型

23 张表，按领域分组：

| 领域 | 表 |
|---|---|
| 项目/分析 | `projects`、`code_analyses`、`state_machines`、`mindmaps` |
| 需求 | `requirements`、`test_points`、`requirement_cases`、`case_generations` |
| 用例 | `test_cases`、`test_case_versions` |
| 执行 | `execution_records`、`execution_steps`、`execution_requirements`、`execution_reviews`、`execution_review_issues`、`execution_review_images` |
| 工作流 | `workflow_runs`、`workflow_stages`、`workflow_cases`、`workflow_case_reviews`、`workflow_result_analyses` |
| 配置/序列 | `llm_profiles`、`id_sequences` |

### 7.1 几个关键关系

```
Project 1 ── N Requirement 1 ── N TestPoint
Requirement N ── N TestCase        （requirement_cases 映射）
TestCase 1 ── N TestCaseVersion    （编辑/回滚快照）
TestCase 1 ── N ExecutionRecord 1 ── N ExecutionStep
ExecutionRecord 1 ── 1 ExecutionRequirement（执行瞬间固化版本）
ExecutionRecord 1 ── 1 ExecutionReview 1 ── N ReviewIssue/ReviewImage
WorkflowRun 1 ── N WorkflowStage
WorkflowRun 1 ── N WorkflowCase 1 ── N WorkflowCaseReview
```

### 7.2 为什么大量 JSON 存 Text

- 分析结果、步骤、覆盖 refs 结构变化快，用 JSON 避免频繁加列。
- 代价：无法用 SQL 精确查询内部字段，快照可能撞 MySQL TEXT 上限。
- 面试可主动说：这是“演进速度优先”的取舍，规模上来后会把热点字段拆列或换 JSON 类型。

---

## 8. 执行器细节

### 8.1 执行器步骤

当前支持四种，接口步骤是规划中的第五种：

| 类型 | 作用 |
|---|---|
| `ui_action` | 打开路由、点击、刷新 |
| `input` | 填输入框 |
| `state_assert` | URL/文本锚点断言 |
| `capture` | 从页面文本提取变量 |
| `api_request`（规划中） | 直接验证接口契约，当前尚未实现 |

### 8.2 定位兜底链

```
uiSelector 精确匹配
  → 按钮/链接/role=button 文本
  → 下拉选项（Element Plus）
  → 页面任意文本
  → normalize-space XPath（处理“登 录”这种两字按钮）
```

### 8.3 证据

- 每步截图 before/after；
- 整条用例录屏；
- 步骤记录 action/target/selector/result/error；
- 执行记录保存用例完整快照，用例后来被改/删也能回看。

### 8.4 当前能力边界（要诚实讲）

- 不擅长表格行勾选、复杂拖拽、文件上传、iframe、Canvas。
- 不直接验证接口契约；批量取消这类点目前只做 UI 入口冒烟。
- 动态数值比较能力有限（支持 capture + 简单加减，不支持任意表达式）。
- 这些不是“没想到”，而是当前执行器只承诺可验证的部分，验证不了就 skipped/待复核。

---

## 9. 真实故障复盘：V3 订单系统 30 条用例

这是面试里最能证明“你真的做过”的素材。

### 9.1 现象

V3 需求生成 30 条用例，执行结果：

- 失败 20 条
- 通过 5 条
- 待复核 5 条

错误分布：`SELECTOR_NOT_FOUND` 9、`ENV_ERROR` 8、`ASSERT_FAILED` 2、`TIMEOUT` 1。

### 9.2 根因（不是被测系统坏了，是生成器假设错了）

1. **列表页点“提交”**
   规则生成器把所有页面当表单页。订单列表实际按钮是“查询/重置”，没有“提交”。

2. **Element Plus 下拉框定位错误**
   订单状态的 placeholder 是 `span`，不是 `input[placeholder]`。旧步骤点不中，后续误点表格里的状态标签；`Locator.fill` 还会直接在 select 上报错。

3. **金额解析过粗**
   “只填最低金额 100”因为描述里还有“闭区间”，被误判成区间，顺手填了最高金额；“重置”点没有先生成金额条件。

4. **状态触发词子串误匹配**
   状态机触发词“支付”命中了“待支付”里的“支付”，批量取消用例被替换成“执行支付”的状态用例。

5. **LLM 生成执行器不支持的步骤**
   “勾选待支付订单复选框”“通过 API 注入不存在 ID”，执行器没有这些能力，必然失败。

### 9.3 修复

- 生成器按页面类型分支：列表筛选、统计页、批量取消、普通表单、状态流转。
- 列表筛选固定点“查询/重置”；金额按“最低/最高/区间/均填/负数/非数字”分别解析。
- 状态下拉改用 `.el-select__wrapper` + `.el-select-dropdown__item`。
- 状态匹配先剔除“待支付/已支付”等标签，再按最长触发词匹配。
- LLM 步骤增加“勾选/复选框/API/注入”拒收，交给规则兜底。
- 执行器兜底链增加下拉选项。
- 统计页六状态逐个断言、金额两位小数用 capture 校验。
- 质量闸不再合并不同测试点的用例，避免冒烟用例被误删导致覆盖缺失。

### 9.4 结果

- 失败 0 条
- 通过 25 条
- 待复核 5 条（AI 审核正确指出：批量取消成功/部分成功、取消/新建/重置后的统计变化，需要数据变更或表格勾选能力；当前只做了 UI 入口/页面指标验证）

### 9.5 面试怎么讲

> “这次故障让我确认了一件事：生成式测试的大部分失败，不是应用 bug，而是生成器对页面的假设错了。我先按错误分类统计，发现 17/20 是定位/环境问题，再回到生成器修页面分支和选择器，而不是逐条改用例。修完后 0 失败，剩下 5 条我主动标成待复核，因为执行器确实没有能力证明批量取消和统计变化，我没有把它们伪造成通过。”

这段话同时体现了：**归因能力、工程取舍、诚实。**

---

## 10. 质量保障与评测

### 10.1 测试

- 后端 144 个测试，覆盖 API、分析器、生成器、质量闸、状态机、执行一致性、工作流。
- 前端目前没有单元测试，只有 `vite build`；这是已知短板。
- 关键生成器行为有回归测试：列表筛选点查询、金额解析、Element Plus 下拉选择器、六状态断言、两位小数 capture。

### 10.2 覆盖矩阵

- **规划覆盖**：用例的 `coverageRefs` 是否引用真实接口/状态转换/业务规则。
- **执行成功覆盖**：只统计 `status=passed` 且属于当前实时用例的执行记录。
- 旧版本/被替换用例不会继续贡献覆盖率。
- 按模块输出：用例数、通过、失败、待复核、未执行、通过率。

### 10.3 评测指标

| 指标 | 含义 |
|---|---|
| 生成数/保留数/丢弃原因 | 质量闸效果 |
| LLM/规则占比、fallbackReason | 模型可用性 |
| 执行通过率/失败分类 | 真实质量 |
| needs_review 比例 | 证据充分性 |
| 规划覆盖率/执行成功率 | 测试完整性 |
| 平均耗时/阶段耗时 | 性能 |

**面试点**：不要只报“通过率”。要同时报“待复核”和“覆盖”，否则通过率可以通过少生成、弱断言刷高。

---

## 11. 已知问题与改进路线

详细清单见 [`CODE-REVIEW-2026-10-26.md`](./CODE-REVIEW-2026-10-26.md)。面试时挑 2～3 个讲，不要背全部。

### 最值得主动讲的

1. **用例编辑契约不一致（P0）**
   前端发 camelCase，后端 DTO 只认 snake_case，预期/步骤保存会丢。修复方向：统一 CamelModel 或让 service 兼容两种键。

2. **Pipeline 必填字段审核过严（P0）**
   全项目所有页面的必填字段被套到每条用例，列表/统计用例会被误判。修复方向：按用例首步路由找对应页面。

3. **执行证据隔离（P1）**
   截图目录按用例 id 而不是 execution_id，重复执行会覆盖历史证据。修复方向：`outputs/{project_id}/{execution_id}/`。

4. **报告 XSS 与证据目录路径（P1）**
   Jinja2 未开 autoescape；项目名直接拼目录。修复方向：autoescape + project_id 目录 + 字符清洗。

5. **前端请求竞态（P1）**
   切项目/需求时旧响应覆盖新数据。修复方向：AbortController + 请求序号 + 切换清空。

### 下一步路线

- 先修两个 P0，保证“编辑保存”和“Pipeline 审核”可信。
- 再做数据一致性：回滚、证据隔离、删除级联。
- 安全收口：报告转义、目录边界、API 归属校验。
- 执行器增强：接口步骤、表格勾选、数值比较、重试策略。
- 工程化：前端测试、lint、CI、按需引入、队列化执行。

---

## 12. 面试讲解话术

### 12.1 白板 8 分钟顺序

1. 画输入：需求 + 源码 + URL。
2. 画静态分析：Spring/Vue → 代码事实。
3. 画生成：测试点 → 用例 → 质量闸。
4. 画审核：确定性 + LLM，只有 pass+automated 才执行。
5. 画执行：Playwright → 三层断言 → 截图/录屏。
6. 画结果：状态机 → 覆盖矩阵 → 报告。
7. 用一句话收尾：**确定性内核保证可信，LLM 只做增强。**

### 12.2 项目亮点（挑 3 个讲）

- **不假绿**：三层断言 + skipped + AI 不能把 failed 改 passed。
- **可复现**：规则内核、代码事实约束、质量闸、生成快照。
- **可归因**：6 类执行错误 + 覆盖矩阵 + 真实 V3 复盘。

### 12.3 不要这样说

- 不要说“AI 全自动生成并判断通过”——平台明确不让模型判绿。
- 不要说“通过率 100%”——真实结果是 25 通过 + 5 待复核，待复核是诚实的结果。
- 不要说“支持所有页面”——表格勾选、iframe、Canvas、文件上传目前不支持。
- 不要把“增量分析”当现有功能讲——已经移除，历史表保留但不再使用。

---

## 13. 高频追问与回答

### Q1：为什么不用纯 LLM 生成用例？

因为测试平台的第一诉求是可信和可复现。纯 LLM 会编造选择器、编造业务规则、同一需求两次结果不同，而且让模型看截图判通过没有确定性证据。我的方案是规则内核 + 代码事实约束 + LLM 语义增强，模型不可用时还能降级规则跑通。

### Q2：怎么防止 LLM 幻觉？

三层约束：

1. Prompt 只给代码事实（真实路由/字段/placeholder/接口/状态机）。
2. 只允许 route/placeholder/text/label/role/testid 这些可验证选择器，禁止 css/xpath。
3. 生成后质量闸和审核再校验：复合预期、多输入、锚点数量、必填字段、源码事实。

### Q3：怎么保证不是“假绿”？

- 断言必须有 URL 或「」文本锚点；没有就 skipped。
- 每一步截图 before/after，整条录屏。
- AI 审核只能把 passed 降为 needs_review，不能把 failed 升为 passed。
- 覆盖矩阵只统计 passed 且属于当前实时用例的执行。

### Q4：为什么选 Playwright，不选 Selenium？

Playwright 自带自动等待、浏览器上下文、录屏、多浏览器，API 更现代；执行器需要稳定定位和证据，Playwright 更合适。Selenium 生态更大，但等待和录屏要自己补很多。

### Q5：动态数据怎么处理？

用 capture 读取基线，再断言 `${base_count+1}`；或者断言唯一订单号/收货人。没有创建动作、无法确定列表路由、多个总数断言时，宁可丢弃用例，也不写死数字。

### Q6：用例不稳定（flaky）怎么办？

- 定位用 wait_for 而不是立即 count；
- 断言用稳定锚点，不用整页文本；
- 共享数据用唯一值；
- 失败按 6 类错误归因；
- 对确实无法稳定验证的场景标 skipped/待复核，而不是重试到绿。

### Q7：为什么用 FastAPI + SQLAlchemy？

Python 生态和 Playwright/LLM SDK 契合；FastAPI 的 Pydantic 校验、依赖注入、SSE 支持好；SQLAlchemy 2 同时支持 SQLite 和 MySQL，方便本地零配置、生产切换。

### Q8：为什么默认 SQLite，又支持 MySQL？

本地/演示零配置，SQLite 足够；生产多进程/并发写需要 MySQL。代码里对 collation、连接释放、TEXT 上限都踩过坑，所以做了兼容处理。

### Q9：并发和规模怎么扩展？

当前批量执行是串行、线程内跑 Playwright，适合单机演示。规模上来要：任务队列（Celery/RQ）、执行 worker 池、浏览器池、SSE/轮询分离、取消状态持久化、MySQL 连接池和心跳回收。

### Q10：怎么支持 React 或其他语言？

分析器是插件式的：`scanner` 给文件列表，`spring.py`/`vue.py` 产出统一的 `BackendResult/FrontendResult`。加 React 就是加一个 analyzer，把页面/表单/选择器映射到同一套模型；执行器不需要改。

### Q11：怎么评估平台本身的效果？

不只看通过率。看：生成保留率/丢弃原因、LLM 占比/降级率、执行失败分类、待复核比例、规划覆盖率、执行成功率、阶段耗时。V3 案例里我从 20 失败修到 0 失败，但保留 5 条待复核，就是为了不刷绿。

### Q12：最难的一个 bug？

V3 状态流转误匹配：触发词“支付”是“待支付”的子串，批量取消用例被替换成支付用例。修复不是改一条用例，而是先剔除状态标签，再按最长触发词匹配。这个 bug 让我意识到：生成器的匹配逻辑必须考虑中文子串和业务语义。

### Q13：为什么把增量分析删了？

它是多期需求的差异分析，维护成本高，当前使用场景不需要；删除后保留了生成版本快照，历史数据不破坏，旧表不主动 DROP。面试里可以讲“知道什么时候删功能也是一种工程判断”。

### Q14：安全上有什么考虑？

当前是本地单用户工具，没有账号体系；但代码审查发现了报告 XSS、项目名路径穿越、API 未校验项目归属等问题。生产化要加认证/授权、报告转义、目录边界、LLM Key 加密和审计。

### Q15：如果给你两周，你先做什么？

第一周修两个 P0（编辑保存、Pipeline 必填字段）和数据一致性（证据隔离、回滚、删除级联）；第二周做安全收口（报告转义/目录/归属校验）和执行器增强（接口步骤、表格勾选、数值比较），同时补前端测试和 CI。

---

## 14. 代码阅读路线

### 第一遍：跑通主链路（2～3 小时）

1. `README.md`：产品定位、快速开始、架构图。
2. `app/main.py`：路由注册、启动建表、CORS。
3. `app/routers/projects.py` → `analysis.py` → `requirements.py` → `generation.py` → `executions.py`。
4. `frontend/src/views/ProjectDetailView.vue`：8 个 Tab 如何组织。

### 第二遍：看确定性内核（半天）

1. `app/analyzer/spring.py`、`vue.py`：代码事实怎么来。
2. `app/agent/quality_gate.py`：三道闸。
3. `app/agent/count_assertion.py`：动态总数。
4. `app/agent/execution_agent.py`：定位/断言/证据。
5. `app/service/coverage_service.py`：覆盖口径。

### 第三遍：看 LLM 与 Pipeline（半天）

1. `app/agent/test_point_agent.py`、`test_case_agent.py`：prompt 与结构校验。
2. `app/llm/client.py`：多供应商适配、reasoning、重试。
3. `app/service/workflow_service.py`：7 阶段编排。
4. `app/service/generation_service.py`：生成编排与版本快照。
5. `tests/`：看关键不变式怎么被测试。

### 最值得先读的 8 个文件

```
app/agent/execution_agent.py
app/agent/quality_gate.py
app/agent/count_assertion.py
app/service/generation_service.py
app/service/workflow_service.py
app/service/execution_service.py
app/analyzer/spring.py
frontend/src/components/GenerationTab.vue
```

---

## 15. 术语表

| 术语 | 含义 |
|---|---|
| 代码事实 | 从源码提取的路由、字段、约束、选择器、接口、状态机 |
| 测试点 | 一个待验证的业务场景，如“最低金额为负数报错” |
| 结构化用例 | 带 `structuredSteps` 的可执行用例 |
| 质量闸 | 复合预期/多输入/接口化/打分/去重 |
| 三层断言 | URL → 文本锚点 → skipped |
| capture | 从页面文本提取运行时变量 |
| 实时用例 | 当前最新生成的用例集合 |
| 生成版本 | 每次生成留下的只读快照 |
| 规划覆盖 | 用例是否引用真实接口/状态/规则 |
| 执行成功覆盖 | 只统计 passed 的当前实时用例执行 |
| Pipeline | 从代码分析到报告的 7 阶段自动流程 |
| needs_review | 证据不足或有疑点，需要人工复核 |

---

## 附录：一分钟自检清单

面试前用这 8 个问题自测：

1. 一句话说清项目是做什么的？
2. 画出“输入 → 分析 → 生成 → 质量闸 → 审核 → 执行 → 报告”？
3. 说出至少 3 个“为什么不用纯 LLM”的理由？
4. 解释“不假绿”靠什么机制？
5. 解释动态总数断言怎么改？
6. 讲一个真实 bug 的根因和修复？
7. 说出 2 个已知问题和改进方向？
8. 如果面试官问“通过率多少”，能同时说出通过/待复核/覆盖吗？
