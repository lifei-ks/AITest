# AI 质检 Agent 改造报告

- 日期：2026-10-29
- 目标：把“页面 AI 质检”从一次 LLM 调用，改成独立、可解释、可追溯的审核 Agent
- 验证：后端 171 个测试通过，前端 `npm run build` 通过

## 1. 改造前后

| 维度 | 改造前 | 改造后 |
|---|---|---|
| 审核方式 | 单次 LLM 调用 | 确定性检查 + LLM 语义审核 + 合并 |
| 逐步检查 | 无 | 每一步检查页面、字段、参数、变量、断言 |
| 上下文 | 全项目事实，按需求缓存 | 按测试点缓存，逐步检查只使用相关事实 |
| 结论 | ok / suspect / fail | reject / revise / pass，确定性 blocking 优先 |
| 分数 | 页面质量分与 AI 结论无直接关系 | 规则基础分 + LLM 新增问题扣分，blocking 封顶 30 |
| 结果 | 只在前端内存 | 最近一次结果写入 TestCase，刷新后仍可查看 |
| 前端展示 | 只显示 verdict 和问题 | 增加“逐步检查”明细，显示每一步的检查项和原因 |

## 2. 当前审核流程

```text
用例 + 需求 + 测试点
  ↓
加载源码事实（按测试点缓存）
  ↓
确定性检查层（case_review_checks）
  ├─ 硬门槛
  ├─ 首步路由
  ├─ 页面/字段/选择器
  ├─ 输入参数与字段约束
  ├─ capture 变量与引用顺序
  ├─ 断言锚点与固定总数
  ├─ 必填字段覆盖
  └─ 步骤顺序与状态流转
  ↓
LLM 语义审核（可选）
  ├─ 测试点意图覆盖
  ├─ 步骤语义是否合理
  ├─ 数据与预期是否匹配
  └─ 缺失场景与歧义
  ↓
合并层 merge_review
  ├─ 确定性 blocking 优先
  ├─ LLM 只追加去重后的新问题
  ├─ 规则分 - 新增问题扣分
  └─ blocking 时封顶 30
  ↓
落库 TestCase.last_review_* + 返回 stepResults
```

## 3. 每一步检查什么

### 3.1 页面和条件

| 检查 | 例子 |
|---|---|
| 首步必须是导航 | 第一步必须是 `ui_action + route` |
| 路由必须存在 | `/orders/new` 必须出现在源码页面路由中 |
| 字段必须属于当前页面 | 输入收货人时，当前页面必须是新增订单页 |
| 元素必须来自事实 | placeholder/label/text 必须能在源码事实中找到 |

### 3.2 参数是否传递正确

| 检查 | 例子 |
|---|---|
| input 必须有值 | `inputValue` 不能为空 |
| 字段匹配 | `请输入收货人` 必须对应 `receiverName` |
| 长度约束 | `size=20` 时输入 21 字符是 blocker |
| 数值范围 | `max/min` 约束不满足时报错 |
| 格式约束 | 手机号、金额、日期等 pattern 不匹配时报错 |
| 必填字段 | 正向表单提交前必须填齐所有 required 字段 |
| 多输入 | 同一字段不能先填 20 字符再填 21 字符 |

### 3.3 变量和数据流

| 检查 | 例子 |
|---|---|
| capture 变量名合法 | `[A-Za-z_][A-Za-z0-9_]*` |
| capture 有捕获组 | pattern 必须包含 `(...)` |
| 变量先定义后使用 | `${base_count+1}` 前必须有 `capture base_count` |
| 变量不能重复定义 | 同一用例内同名变量只能定义一次 |
| testData 字段有效 | key 必须对应页面字段或事实中的文案 |

### 3.4 断言是否有效

| 检查 | 例子 |
|---|---|
| 断言不能为空 | `state_assert.expected` 必须有值 |
| 只能有一个锚点 | 不能一条断言同时判断多个结果 |
| 锚点必须来自事实 | 文案要在 uiTexts、需求或测试点中出现 |
| 固定总数要有基线 | “共 5 条”需要 capture + 基线+1 |
| 变量锚点必须可解析 | `${var}` 必须由前面的 capture 定义 |

### 3.5 哪些不能静态判断

以下问题交给执行阶段，不在审核阶段直接判 blocker：

- 元素运行时是否可见、是否被遮挡；
- 动态渲染是否完成；
- 后端接口是否真正成功；
- 共享数据在执行时是否发生变化；
- 页面跳转是否真的发生。

这些会标记为 `unknown` / warning，由真实执行和截图证据确认。

## 4. Agent 获得的上下文

### 4.1 用例本身

```text
title / module / type / priority / preconditions
structuredSteps / expectedResults / testData / coverageRefs
qualityScore / qualityBreakdown / lintIssues
```

### 4.2 需求与测试点

```text
requirement: version / name / excerpt
testPoint: id / title / category / priority / description
           pageRoute / endpoint / fields / expected
```

### 4.3 源码事实

```text
pages: 页面、路由、表单、字段、placeholder、label
selectors: 可定位选择器
uiTexts: 页面文案
endpoints: 接口
entities: 实体和字段约束
businessRules: 业务规则
enums: 枚举
stateMachines: 状态机与迁移
```

事实缓存按 `(requirement.id, point.id)` 隔离，避免同一需求下不同测试点
互相污染。

## 5. 结论与分数

### 5.1 结论

| 结论 | 条件 |
|---|---|
| `reject` | 存在确定性 blocker，或 LLM 判断 fail |
| `revise` | 存在确定性 warning，或 LLM 判断 suspect |
| `pass` | 没有 blocker/warning，LLM 也没有新增问题 |

确定性 blocker 不能被 LLM 的 `ok` 覆盖。

### 5.2 分数

```text
基础分 = score_case_detailed：
  断言强度 30
  + 数据独立性 25
  + 选择器稳定性 20
  + 覆盖绑定 15
  + 步骤规模 10

最终分 = 基础分
  - 确定性 fixable 问题（每个 5 分，最多 20）
  - LLM 新增问题（blocker 25 / warning 8 / info 2）
  - blocking 时封顶 30
```

注意：

- 页面列表里的“质量分”是规则分，不是 LLM 自评分；
- LLM 不能直接修改分数，只能通过“新增问题”影响扣分；
- 确定性问题与 LLM 问题按 `code` 去重，避免同一个问题扣两次；
- LLM 不可用时，仍返回确定性结论。

### 5.3 审核 Agent 内的标题去重

审核 Agent 现在会在同一个项目内做一次低成本的标题重复检查，不消耗 LLM token：

```text
标题去空白/标点/大小写
  → SequenceMatcher 相似度
  → 再比对 route、inputValue、expectedResults
```

判定规则：

| 情况 | 标注 |
|---|---|
| 标题相似，输入值不同 | 不算重复（避免误杀 20/21 字符边界） |
| 标题相似，输入、预期、路由相同 | `NEAR_DUPLICATE` warning |
| 标题相似，预期不同 | `TITLE_SIMILAR` info |
| 不同测试点/不同输入场景 | 只展示候选，不删除 |

审核结果只做标注，不自动删除用例；由人工确认是否合并或删除。

## 6. 代码改动

| 文件 | 改动 |
|---|---|
| `app/agent/case_review_checks.py` | 新增逐步确定性检查与 `merge_review` |
| `app/agent/case_review_agent.py` | 审核输入加入 `deterministicChecks`，解析 `stepResults` |
| `app/agent/prompts/case_review.txt` | 要求逐步审核、复用确定性结论、避免重复报告 |
| `app/service/case_review_service.py` | 接入确定性检查、修复 facts 缓存 key、落库最近一次审核 |
| `app/entity/models.py` | TestCase 新增 `last_review_*` 字段 |
| `app/database.py` | SQLite 启动时自动补齐审核字段 |
| `migrations/mysql/003_case_review.sql` | MySQL 审核字段迁移 |
| `app/service/generation_service.py` | `case_to_dict` 返回最近一次审核结果 |
| `frontend/src/views/TestcasesView.vue` | 展示“逐步检查”，刷新后仍显示最近一次 AI 结论 |
| `tests/test_case_review_checks.py` | 逐步检查与合并规则测试 |
| `tests/test_case_review_service.py` | facts 缓存隔离、LLM 不可用降级、审核落库测试 |
| `tests/test_case_review_agent.py` | stepResults 解析测试 |
| `tests/test_case_review_checks.py` | 标题去重、20/21 字符不误杀、相似标题仅提示 |

## 7. 示例

### 7.1 输入用例

用例 `TC-000123`：新增订单，收货人输入 21 个字符，断言“共 5 条订单”。

```json
{
  "id": "TC-000123",
  "title": "新增订单-收货人边界",
  "type": "positive",
  "expectedResults": ["「共 5 条订单」"],
  "structuredSteps": [
    {
      "type": "ui_action",
      "action": "打开新增订单页面",
      "uiSelector": {"type": "route", "value": "/orders/new"}
    },
    {
      "type": "input",
      "action": "输入收货人",
      "target": "收货人",
      "uiSelector": {"type": "placeholder", "value": "请输入收货人"},
      "inputValue": "aaaaaaaaaaaaaaaaaaaaa"
    },
    {
      "type": "ui_action",
      "action": "点击提交",
      "target": "提交",
      "uiSelector": {"type": "text", "value": "提交"}
    },
    {
      "type": "state_assert",
      "action": "验证列表总数",
      "expected": "「共 5 条订单」"
    }
  ]
}
```

### 7.2 审核结果

```json
{
  "caseId": "TC-000123",
  "verdict": "reject",
  "score": 30,
  "reason": "确定性检查发现阻断问题",
  "issues": [
    {
      "code": "VALUE_CONSTRAINT",
      "severity": "blocker",
      "problem": "输入值不满足字段约束",
      "reason": "长度 21 超过上限 20",
      "evidence": "field:收货人",
      "suggestion": "调整输入值或拆成边界用例"
    },
    {
      "code": "FIXED_COUNT_BASELINE",
      "severity": "warning",
      "problem": "固定总数断言没有运行时基线",
      "reason": "没有 capture 基线，数据变化时断言会误判",
      "evidence": "step[4].expected",
      "suggestion": "改为 capture + 基线+1，或断言唯一行标识"
    }
  ],
  "stepResults": [
    {
      "stepIndex": 1,
      "stepType": "ui_action",
      "verdict": "pass",
      "checks": [
        {"code": "FIRST_STEP_ROUTE", "status": "pass", "detail": "第一步是导航"},
        {"code": "ROUTE_EXISTS", "status": "pass", "detail": "路由 /orders/new"}
      ],
      "issues": []
    },
    {
      "stepIndex": 2,
      "stepType": "input",
      "verdict": "fail",
      "checks": [
        {"code": "FIELD_EXISTS", "status": "pass", "detail": "命中字段：收货人"},
        {"code": "VALUE_CONSTRAINT", "status": "fail", "detail": "长度 21 超过上限 20"}
      ],
      "issues": [
        {
          "code": "VALUE_CONSTRAINT",
          "severity": "blocker",
          "problem": "输入值不满足字段约束",
          "reason": "长度 21 超过上限 20",
          "suggestion": "调整输入值或拆成边界用例"
        }
      ]
    },
    {
      "stepIndex": 4,
      "stepType": "state_assert",
      "verdict": "warning",
      "checks": [
        {"code": "FIXED_COUNT_BASELINE", "status": "warning", "detail": "固定总数断言没有运行时基线"}
      ],
      "issues": [
        {
          "code": "FIXED_COUNT_BASELINE",
          "severity": "warning",
          "problem": "固定总数断言没有运行时基线",
          "reason": "没有 capture 基线，数据变化时断言会误判",
          "suggestion": "改为 capture + 基线+1"
        }
      ]
    }
  ],
  "llmAvailable": true,
  "model": "deepseek-chat"
}
```

### 7.3 用户看到什么

前端 AI 质检弹窗会显示：

- 结论：失败（reject）
- 规则质量分：30
- 第 2 步：输入收货人，长度超过字段上限 20，阻断
- 第 4 步：固定总数断言没有运行时基线，警告
- 每条问题都有原因、依据和建议

用户可以直接点“AI 修复”，或者按步骤修改后重新质检。

## 8. 面试怎么讲

### 8.1 60 秒版本

> 我把 AI 质检从一个“让模型看一遍”的接口，拆成了一个独立审核 Agent。
> 它先做确定性检查，再让 LLM 做语义审核，最后由合并层给结论。
> 确定性检查负责页面、字段、选择器、参数、变量、断言和状态流转；
> LLM 只补充测试点覆盖、步骤意图和数据一致性这类语义问题。
> 这样客观错误不依赖模型，模型也不会推翻确定性 blocker。
> 结果按步骤落库，前端能看到“第几步、什么条件、为什么有问题”。

### 8.2 三分钟 STAR

**Situation**：原来的 AI 质检是一次 LLM 调用，缺少确定性校验，facts 还可能串到别的测试点，结果也不落库。

**Task**：做一个真正独立的审核 Agent，能逐步判断条件和参数，给出可解释的原因，并且能追溯。

**Action**：

1. 新增 `case_review_checks`，把审核拆成页面、字段、参数、变量、断言、状态、步骤顺序等确定性检查。
2. 修复 facts 缓存 key，按 `(requirement.id, point.id)` 隔离。
3. 把确定性结果传给 LLM，要求它只补充语义问题，不能否定 blocker。
4. 新增 `merge_review`，确定性结论优先，LLM 问题按 code 去重，避免重复扣分。
5. 把结果落库到 `TestCase.last_review_*`，前端增加逐步检查展示。
6. 补了 171 个后端测试和前端构建验证。

**Result**：明显缺字段、参数越界、变量未定义、固定总数无基线这类问题，不依赖 LLM 就能发现；LLM 只负责语义补充；结论可解释、可追溯。

### 8.3 面试可能追问

**问：为什么不让 LLM 直接判断？**

答：LLM 不适合做确定性判断。字段是否存在、长度是否越界、变量是否先定义后使用，这些用规则更稳定、更便宜、可复现。LLM 只做规则做不了的语义判断。

**问：怎么避免 LLM 和规则重复扣分？**

答：每条问题有 `code`，合并时按 code 去重；确定性 blocking 优先，LLM 只能追加新问题。

**问：LLM 不可用怎么办？**

答：确定性检查仍然返回结果，接口标记 `llmAvailable=false`，用户可以继续看到客观问题。

**问：哪些问题不能静态判断？**

答：元素运行时可见性、动态渲染、接口真实返回、共享数据变化，这些标记为 unknown 或 warning，交给执行阶段和截图证据。

**问：分数怎么算？**

答：规则分是五项分项评分；LLM 新增问题按 blocker 25、warning 8、info 2 扣分；blocking 时最终分封顶 30。页面的质量分和 AI 结论分开展示。

**问：怎么评估效果？**

答：看审核准确率、误报率、漏报率、人工一致率、单条审核耗时和 LLM 成本；用人工标注集校准问题权重。

**问：下一步怎么扩展？**

答：按用例路由/字段裁剪 facts、批量审核并发化、增加完整审核历史表、把证据引用结构化到 page/field/rule ID。

## 9. 当前限制

- 批量审核仍是串行调用，最多 20 条，后续可以加 3~5 路并发。
- 目前只保存最近一次审核结果，没有完整的审核历史表。
- 源码事实还没有完全按用例裁剪，prompt 可能偏大。
- 运行时可见性、接口返回和共享数据变化仍由执行阶段验证。

## 10. 验证

```text
后端：179 passed
前端：npm run build 通过
```

新增测试覆盖：

- 首步不是导航 → reject
- 输入缺少值 → reject
- 字段不在事实中 → reject
- 长度超过约束 → reject
- 变量未定义 → reject
- 固定总数无基线 → revise
- 反例留空不误判必填
- 确定性 reject 不被 LLM ok 覆盖
- LLM 只追加新问题并扣分
- 同一需求多测试点 facts 隔离
- LLM 不可用时返回确定性结果
- 审核结果落库
- 相同标题、相同输入/预期 → `NEAR_DUPLICATE`
- 20/21 字符标题相似但输入不同 → 不标重复
- 相似标题、预期不同 → `TITLE_SIMILAR` info
