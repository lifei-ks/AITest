# 多 Agent 测试工作流

> 目标：在**不替换现有接口、表、执行器、模型服务和数据库**的前提下，把「测试点生成 → 用例生成 → 用例审核 → 结果分析」拆成 4 个逻辑角色，并由轻量编排器串成可追踪的闭环。

## 1. 设计边界

- 不引入新框架、不换模型服务、不改已有表结构；工作流只新增 5 张表。
- 4 个 Agent 是**逻辑角色**，共享现有 `LlmClient`、静态分析事实、质量闸、Playwright 执行器和存储层。
- 不接工作流时，原有「需求解析 / 用例生成 / AI 质检 / 执行 / 截图审核」接口行为不变。
- 只有审核结论为 `pass` 且 `automated` 的用例才会进入执行；`manual` 用例只审核、不执行；`reject` 用例不执行。
- 结果分析写入独立表，**绝不回写 `execution_records.status`**。

## 2. 架构

```
Requirement (版本快照)
   │
   ▼
代码分析（缺分析/强制重分析时执行；纯静态规则）
   │
   ▼
Agent 1  测试点生成（prompt: test_points.txt）
   │      LLM 优先；不可用/输出非法 → 规则兜底
   ▼
TestPoint[]（category/priority/pageRoute/fields/expected）
   │
   ▼
Agent 2  用例生成（prompt: test_cases_by_point.txt；项目级 project_cases.txt）
   │      复用 generation_service + 三道质量闸（复合预期/多输入/去重）
   ▼
TestCase[]（source=rule/ai/manual/import；structuredSteps）
   │
   ▼
Agent 3  独立审核（prompt: case_review_workflow.txt；修复 case_repair.txt）
   │      确定性校验（结构/单场景/必填/事实） + LLM 建议
   │      pass / revise / reject；revise 最多 maxRevisions(≤2) 轮
   ▼
仅 pass + automated
   │
   ▼
现有 execution_service.execute_single（Playwright，截图/录屏/步骤/AI 截图审核）
   │      结果状态是客观事实，后续不得修改
   ▼
Agent 4  证据驱动结果分析（prompt: result_analysis.txt）
   │      输入：执行状态/错误码/步骤/截图审核结论/预期
   │      输出：failure_category + evidence + confidence + action
   ▼
WorkflowRun.summary → workflow 报告 + 项目报告附加区块
```

页面入口：侧边栏「Pipeline 执行」。选择项目与需求版本后，一键执行以上全流程；高级配置可调整生成模式、修订轮次、用例上限和是否执行。

### 2.1 为什么是「薄 Agent」

- 生成器已经具备事实注入、单场景过滤、质量闸、规则兜底；把这些重写一遍会制造两套判定标准。
- 因此 Agent 负责：提示词、模型调用、结构化输出校验、独立错误语义。
- Service 负责：事实加载、落库、质量闸、执行、报告；编排器负责阶段状态与重试上限。

## 3. 4 个 Agent

### Agent 1：测试点生成

- 文件：`app/agent/test_point_agent.py`
- 提示词：`app/agent/prompts/test_points.txt`
- 输入：需求版本号/名称、需求正文（截断 12000 字）、代码事实（页面/字段/接口/状态机/UI 文案）
- 输出：`list[dict]`，字段经 `TestPointDraft` 校验：
  `title/category/priority/description/pageRoute/endpoint/fields/expected/confidence`
- 约束：一个测试点一个场景、一个「」锚点；边界合法值与越界值必须拆成两条；覆盖正常/异常/边界/状态/权限或安全（越权/未登录访问）；最多 30 条。
- 降级：LLM 不可用或输出为空 → 现有规则抽取；非法条目丢弃并告警，不写坏数据。
- 复用：`requirement_service.parse_points/save_points`，后台解析任务与工作流共用。

### Agent 2：可执行用例生成

- 文件：`app/agent/test_case_agent.py`
- 提示词：`app/agent/prompts/test_cases_by_point.txt`（按测试点）、`project_cases.txt`（项目级旧入口）
- 输入：需求版本、测试点批次（每批 5 条）、代码事实（页面 required/placeholder/selectors/uiTexts/endpoints/stateMachines）
- 输出：原始 `testCases[]`；语义过滤仍在 `generation_service._gen_llm_for_points`：
  - `testPointId` 必须属于本批测试点
  - 复合预期、多个输入、多个锚点 → 丢弃并交给规则生成器拆分
  - 缺第一步 route → 自动补页面路由
  - 质量闸：无预期/复合/多输入/低分/重复
- 手工用例：`source in (manual, import)` 标记 `manual`，不进入执行；无结构化步骤的生成用例标记 `blocked`。

### Agent 3：独立用例审核

- 文件：`app/agent/case_review_agent.py`
- 提示词：`case_review_workflow.txt`；修复 `case_repair.txt`；旧页面质检仍用 `case_review.txt`
- 输入：用例快照、需求摘要、源码事实、确定性校验问题、确定性质量分
- 确定性校验（代码裁定）：
  - blocking：无结构化步骤、无预期、复合预期、同一字段多个输入
  - fixable：路由/placeholder/label/inputValue/必填字段/断言锚点缺失
  - advisory（非阻塞）：源码事实中找不到的 text 按钮文案、断言锚点、testData 字段
- LLM 建议：三态 `pass/revise/reject`、问题、未覆盖场景、建议。
- 分数：`score_case`（现有质量闸）− LLM blocker 25/警告 8/信息 2；blocking 时上限 30。
- 轮次：
  - `revise` 且有预算 → 调 `case_review_service.repair_case`（补丁必须通过源码事实校验）→ 重新审核；
  - 最多 `maxRevisions` 轮（0~2）；达到上限仍不通过 → 最终 `reject`，记录原因，不执行；
  - 首审和每一轮审核都写入 `workflow_case_reviews`，`workflow_cases` 记录 `first_review_id/final_review_id/revision_count`；
  - 单条审核异常也写 `reject` 记录，不静默丢弃。
- 提示词外置后，修复调用复用工作流的同一个 LLM 客户端；`repair_case(..., client=...)` 为可选参数，原 API 不传时行为不变。

### Agent 4：证据驱动结果分析

- 文件：`app/agent/result_analysis_agent.py`
- 提示词：`app/agent/prompts/result_analysis.txt`
- 输入证据：
  - 执行状态/错误码/错误文本/目标地址/耗时/步骤计数
  - 每步结果、错误、选择器、截图前后路径（最多 20 步）
  - 已有截图审核结论/问题/逐图分析
  - 用例预期、测试点、需求版本摘要
- 输出：`failure_category`、`failure_reason`、`evidence[]`、`confidence`、`recommended_action`、`needs_human_review`
- 分类：`product_defect / test_script_issue / test_data_issue / environment_issue / unknown / none`
- 保守校正（代码强制）：
  - `execution_status` 只由数据库快照回填，模型输出同名字段被忽略；
  - `passed` 只能得到 `none`（截图审核 suspect 时为 `unknown`）；非通过不能返回 `none`；
  - 声称缺陷/脚本/数据/环境问题时，evidence 必须引用真实步骤号、错误码、审核结论或预期文案，否则降级 `unknown`；
  - `unknown`/`product_defect` 一律 `needs_human_review=true`，`unknown` 置信度上限 0.5；
  - `ASSERT_FAILED` 默认 `unknown`，不直接判产品缺陷；
  - `ENV_ERROR` 若失败文本指向 locator/元素类型/元素未找到，归为 `test_script_issue`（仍需人工复核）；
  - 无证据时 `unknown` + 置信度 ≤ 0.3；
  - LLM 不可用/输出非法 → 规则兜底，`source=rule`。

## 3.5 数据依赖与总数断言

共享测试数据会让「共 5 条」「总数变为 5 条」这类固定聚合断言必然失真：执行前可能已有 7 条，执行后就是 8 条。

处理策略（按优先级）：

1. **数据隔离**：理想情况下每个用例自备数据或调用项目提供的 reset 接口；当前平台不假设被测系统有 reset 能力，因此不能只依赖固定种子。
2. **运行时基线（已实现）**：新增 `capture` 步骤，在操作前读取当前总数；断言使用 `${base_count+1}`。执行器支持 `${var}`、`${var+1}`、`${var-1}`，变量缺失/非数字会明确失败，不会静默跳过。
   ```json
   {"type":"capture","action":"读取当前列表总数","target":"列表总数","uiSelector":{"type":"text","value":""},"expected":"","inputValue":"","variable":"base_count","pattern":"共\\s*(\\d+)\\s*条订单"}
   ```
   ```json
   {"type":"state_assert","action":"校验总数增加 1","uiSelector":{"type":"text","value":"「共 ${base_count+1} 条订单」"},"expected":"「共 ${base_count+1} 条订单」"}
   ```
3. **唯一行标识**：创建类用例优先断言新订单的唯一收货人/订单号/状态，不依赖全局总数。
4. **固定总数无基线 → 不执行**：没有创建动作或无法确定列表路由时，生成阶段丢弃该用例，审核阶段标 reject 并说明原因；不伪造通过。
5. **自动改写**：生成和审核阶段都会检测「共/总数/数量 N 条」；对包含创建动作且能确定列表路由的用例，自动插入 `capture` 并把断言改写为「基线+1」。旧用例在 Pipeline 审核时也会生成新版本，不需要手工改步骤。

边界：当前只处理单个、`+1` 的创建型总数断言；删除/过滤导致的 `-1`、金额等非计数聚合、多总数同时出现的情况不会自动改写，会进入审核/人工处理。

## 4. 编排器与状态

- 文件：`app/service/workflow_service.py`
- 阶段固定顺序：
  `code_analysis → test_points → case_generation → case_review → execution → result_analysis → report`
- 运行状态：`pending / running / completed / partial / failed / cancelled`
  - `cancelled` 为预留枚举；当前未提供取消 API（不假装支持）。
- 阶段状态：`pending / running / completed / failed / skipped`
- 用例状态：`pending / pass / revise / reject`；自动化状态：`automated / manual / blocked`
- 失败与保留：
  - 每个阶段独立提交；阶段失败时前序阶段产物保留，后续阶段标 `skipped`；
  - 用例生成阶段失败会阻止后续阶段；执行阶段目标不可达时标记该阶段 failed，但运行仍生成报告（`partial`）；
  - 单条审核/执行/分析失败只影响该用例，并写入失败记录；
  - 执行记录本身失败不是工作流失败，仍继续做证据分析。
- 重试/上限：审核修订 ≤2 轮；用例数 ≤ `maxCases`；无自动重试目标不可达的执行阶段。

## 5. 数据模型（仅新增表）

| 表 | 作用 | 关键字段 |
|---|---|---|
| `workflow_runs` | 一次运行 | project/requirement 快照、status、current_stage、config、summary、时间 |
| `workflow_stages` | 阶段记录 | stage、status、attempt、payload、error_message、model、duration |
| `workflow_cases` | 本次运行的用例索引 | case_id、test_point_id、position、automation_status、review_status、revision_count、quality_score、first/final_review_id、execution_id、analysis_id |
| `workflow_case_reviews` | 每一轮审核 | round、review_status、quality_score、issues、missing_coverage、suggestions、deterministic_issues、source、model |
| `workflow_result_analyses` | Agent 4 结果 | execution_id、execution_status 快照、failure_category、reason、evidence、confidence、recommended_action、needs_human_review、source、model、raw_response |
| `llm_profiles` | 多套 LLM 配置 | kind(text/vision)、name、api_key、base_url、model、enabled、is_default |
| `case_generations` | 用例生成版本快照 | requirement_id、mode、profile_id、case_count、summary、snapshot |

所有 JSON 字段仍用 `Text` 存字符串；不修改已有表。MySQL DDL 已重新生成：`migrations/mysql/001_init.sql`（27 张表，新增 `llm_profiles`、`id_sequences`、`case_generations` 及 4 张增量分析表）。

## 6. API

请求/响应统一使用现有 `{code,message,data}` 信封。

### 启动

`POST /api/projects/{pid}/workflows`

```json
{
  "requirementId": "req-id",
  "mode": "auto",
  "autoExecute": true,
  "maxRevisions": 2,
  "maxCases": 20,
  "reuseExistingCases": true,
  "reanalyzePoints": false,
  "targetUrl": "http://127.0.0.1:8200",
  "analyzeCode": true,
  "reanalyzeCode": false,
  "reviewEnabled": true,
  "analyzeEnabled": true
}
```

立即返回运行记录；后台线程执行，前端轮询详情。

### 查询

- `GET /api/projects/{pid}/workflows?limit=20`：运行列表
- `GET /api/workflows/{run_id}`：run + `stages[]` + `cases[]`（含每轮审核、执行摘要、分析）
- `GET /api/workflows/{run_id}/report`：HTML 报告
- `GET /api/projects/{pid}/report?workflowRunId={run_id}`：项目报告附加工作流区块

`cases[]` 中每项包含：`caseId/title/automationStatus/reviewStatus/revisionCount/qualityScore/firstReview/finalReview/reviews/execution/analysis`。

## 7. 提示词外置

- 目录：`app/agent/prompts/*.txt`，UTF-8。
- 占位符：`{{UPPER_SNAKE_CASE}}`；**不**使用 `str.format`，避免 JSON 花括号冲突。
- 缺失变量立即抛 `PromptError`，不会把 `{{X}}` 发给模型。
- 默认按 mtime 热加载；可用 `AITEST_PROMPT_DIR` 指向灰度目录。
- 测试 `test_all_external_prompts_render_without_placeholders` 校验所有模板可完整渲染。
- 原提示词内容原样迁移；现有用例质检/修复仍走同一提示词文件，行为保持一致。

## 8. 运行与配置

### 8.1 页面

侧边栏「Pipeline 执行」→ 左侧选择项目 → 选择需求版本 → 一键执行。

- 页面右上角「快速创建项目 + 需求」：填写项目名称/源码路径/被测地址，创建需求版本并上传 `.md/.txt/.pdf/.docx` 需求文档或粘贴文本；创建后自动选中该需求版本，再点「一键执行 Pipeline」
- 当前源码分析读取本机目录，暂不支持上传源码压缩包或自动拉取 Git 仓库
- 默认：自动生成、自动分析代码、自动执行通过用例、启用审核与结果分析、修订 ≤2 轮
- 「高级配置」可调整生成模式、修订轮次、用例上限、复用/重解析、代码分析开关
- 缺少成功代码分析时自动执行静态分析；已有成功分析默认复用，`reanalyzeCode=true` 可强制重跑
- 页面每 2 秒轮询运行状态；可展开用例查看每轮审核问题、未覆盖场景、执行状态、分析证据和报告

### 8.2 多 LLM 配置

设置页分「文本 LLM」「多模态 VLM」两组，每组可配置多条；测试点解析、用例生成、Pipeline 高级配置可分别选择文本 profile。

- 首次读取设置时，把 `.env` 的单套文本/多模态配置迁移成默认 profile；之后以设置页/`llm_profiles` 为准
- 未选择 profile 时使用同类默认 profile；没有可用 profile 时回退 `.env`
- 截图审核使用默认 VLM profile；没有可用 VLM 时回退默认文本 profile
- API 只返回掩码 Key；更新时传回掩码表示保留原 Key
- 生成接口：`profileId`（测试点解析/用例生成）；Pipeline：`testPointLlmProfileId` / `caseLlmProfileId`

### 8.3 环境变量

沿用现有 `.env`（仅作为初始/兜底配置）：

| 变量 | 作用 |
|---|---|
| `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` | 4 个 Agent 的文本 LLM；为空时测试点/用例/审核走规则或确定性降级，结果分析走规则 |
| `LLM_VISION_*` | 执行后的截图审核（既有能力，不属于 4 Agent 闭环） |
| `TARGET_URL_DEFAULT` | 工作流未显式传 `targetUrl` 时的默认被测地址 |
| `AITEST_PROMPT_DIR` | 可选，覆盖提示词目录 |

### 8.4 命令行实验

```bash
# 实验 A：基线生成 vs 生成+审核（会改写生成用例，必须确认）
.venv/Scripts/python scripts/evaluate_workflow.py \
  --project-id <pid> --requirement-id <rid> --mode rule \
  --max-revisions 2 --output evaluation_a.json --allow-write

# 实验 B：失败分类（rule 不调用模型；llm 使用 .env）
.venv/Scripts/python scripts/evaluate_result_analysis.py \
  --samples labels.jsonl --mode rule --output evaluation_b.json
```

## 9. 实验设计

### 实验 A：用例质量

- 基线：`generation_service.generate`（现有 LLM/规则生成 + 质量闸，无工作流审核）。
- 方案：同一需求复用同一批用例，运行工作流审核/修订（`autoExecute=false`），指标只统计 `reviewStatus=pass`。
- 指标定义（实现见 `app/evaluation/metrics.py`）：
  - 必填字段完整率：仅统计 `positive` 用例（反例/边界会故意留空被测字段）；每条正向用例填写的页面必填字段数/必填字段数，再取平均；无必填字段页面记 1.0，并输出 `filled/required` 审计明细和实际统计范围。必填字段来源与生成器一致：前端 `required` + 后端 `notblank/notnull/notempty`。
  - 测试点覆盖率：至少被一条用例引用的测试点数/测试点总数。
  - 重复率：`(用例数 - 去重场景数)/用例数`；指纹=标题+全部步骤（动作/目标/输入/断言）+预期。
  - 可验证预期率：预期+断言去重后恰好 1 个「」锚点，且不含“显示正常/符合预期”等模糊词。
  - 关键场景遗漏率：`negative/boundary/state` 测试点中无任何用例覆盖的比例，并列出遗漏项。
  - 质量分：统一使用现有质量闸分（baseline/proposed 同一把尺）；工作流审核分单独输出为 `reviewQualityScore`，不参与对比。
  - 人工质量分：仅当传入 `--human-scores {caseId: 0~100}` 时计算，否则 `null`。
  - 调用次数/耗时：真实 `chat_json` 调用次数与墙钟时间；`estimatedCost` 固定 `null`（未把响应 usage 纳入成本估算）。
- 输出：`baseline / proposed / diff`，每个数值含绝对值与百分比；基线为 0 时百分比为 `null`。
- 诚实声明：同一需求顺序执行属于配对观察，不是独立随机样本；严谨实验应对照两份数据库副本或两份需求版本。

### 实验 B：失败分类

- 样本 JSONL：`{"id","evidence","gold_label","human_label","no_evidence"}`。
- 打标字段只在脚本内使用；传给 Agent 前深拷贝 `evidence` 并删除 `gold_label/gold/human_label/label`，测试覆盖标签隔离。
- 指标：
  - accuracy、逐类 precision/recall/F1/support、macro-F1（仅对有 gold 样本的类取平均）
  - unknown 检出率（真值 unknown 中预测为 unknown 的比例）
  - 无证据结论率（无步骤且无截图审核的样本中，给出非 unknown/none 结论的比例，越低越保守）
  - 人工一致率（有 `human_label` 的样本中 pred==human 的比例）
  - 平均/总耗时、实际来源分布（llm/rule）
- 没有样本时输出 `samples=0` 与 `null`，不伪造指标。

## 10. 测试与实测记录

本次实际执行：

```bash
pytest -q
# 150 passed, 1 warning（StarletteDeprecationWarning）

cd frontend && npm run build
# ✓ built（仅有既有 chunk >500kB 提示）
```

工作流测试（`tests/test_workflow.py`）覆盖：

- 所有外置提示词可渲染、无残留占位符
- 测试点 Agent 丢弃非法条目、字段回填，并过滤“展示 N 个字段/默认 N 条数据”等静态结构测试点
- 用例生成 SSE：逐批进度、候选/落库用例逐条展示、LLM→规则兜底事件和来源标记；每次生成保存版本快照，生成页与测试用例页的版本下拉都按时间倒序排列，并始终提供「实时用例（未归档）」选项；改造前旧用例首次查看时自动归档为「历史归档」，不会与后续生成混在一起；测试用例页的历史版本只读，执行/编辑/删除/AI 质检需切回实时用例
- 结果分析：无证据不冒充缺陷、模型无证据结论降级、locator 失败归为脚本问题
- 离线 LLM 的完整闭环：阶段全部完成，审核通过用例被执行，分析不覆盖 `ExecutionRecord.status`
- 无代码分析的项目：Pipeline 自动执行 `code_analysis`，再继续测试点/用例
- 固定总数断言：自动改写为 capture + 基线+1；7 条基线执行后断言 8 条的真实浏览器验证通过
- 无创建动作/无法定位列表的固定总数用例：不执行，明确记录原因
- 修订上限：始终要求修订时最多 2 轮，最终 reject，执行/分析阶段 skipped
- 阶段失败保留：测试点阶段 completed、用例生成 failed、后续 skipped、报告 completed
- API：启动、轮询、详情、工作流报告、项目报告附加区块
- 服务重启：遗留 running 运行和阶段被标记为 failed/skipped，不留下假运行状态
- Pipeline 快速接入：建项目 → 建需求版本 → 上传 PRD 后内容可读

真实 Pipeline 冒烟（临时 SQLite + 无预置代码分析 + `scripts/demo_site` + 本机 Chromium；LLM 离线、跳过视觉审核以避免外部调用）：

- 运行状态 `completed`，7 个阶段全部 completed
- `code_analysis` 自动完成：12 接口 / 3 页面 / 1 状态机
- 3 条测试点 → 4 条用例 → 审核全部 pass → 真实执行 4 条
- 执行结果：3 passed / 1 failed
- 失败用例：输入「备注」时 locator 命中了 `<form>` 而不是输入框，执行器记录 `ENV_ERROR`；Agent 4 依据失败步骤文本归为 `test_script_issue`（置信度 0.7，需人工复核）
- 同一次运行中 3 条通过用例被归为 `none`，未编造缺陷

> 该冒烟只验证 Pipeline 链路与保守分类；未用它产出实验 A/B 的正式指标。

评测 CLI 冒烟：

- 实验 A 在临时 SQLite + 演示源码上离线跑通，输出 baseline/proposed/diff；基线/方案均 4 条用例、覆盖 1.0、必填完整率 1.0（1 条正向用例 4/4 字段）。这是链路验证，不是效果结论。
- 实验 B 用 4 条合成标注样本离线跑通，输出 accuracy/macro-F1/unknown 检出率/无证据结论率/来源分布。合成样本仅用于验证输出格式，仓库不内置或宣称任何真实评测分数。

## 11. 局限与已知边界

- 工作流是**串行**的，没有并发调度/取消 API；`cancelled` 仅为预留状态。
- 目标地址不可达时，执行阶段直接失败，不自动重试；不会生成假执行记录。
- 单次运行只处理一个需求版本；`maxCases` 会截断生成结果，超出的用例不参与本次审核/执行。
- 手工/导入用例只做确定性标记，不执行；生成但无结构化步骤的用例会 `reject/blocked`。
- 审核修订会更新原用例（并生成 `test_case_versions` 版本）；执行历史仍保留执行瞬间快照，但其他运行引用同一用例时会看到最新版本。
- 固定总数自动改写只覆盖“创建动作 + 单个计数 +1 + 能确定列表路由”的场景；删除/过滤、金额聚合、多个总数同时出现时不会自动改写，会进入审核或拒绝。
- `capture` 从页面 body 文本按 pattern 提取，pattern 必须足够具体（如 `共\s*(\d+)\s*条订单`）；提取不到会明确失败，不会把变量当作 0 或跳过。没有数据隔离时，建议优先断言唯一行标识。
- 共享数据环境没有通用的自动 reset；如果被测系统需要固定种子数量，应由项目提供数据准备/清理能力，平台不会假设 `/api/demo/reset` 之类接口存在。
- 审核的 advisory 项（源码分析未采集到的按钮文案/锚点）不会单独阻塞执行，因为无法证明其错误；若需更严格，应完善静态分析或开启 LLM 审核。
- 修订质量取决于 LLM；LLM 不可用时只能做确定性审核，`fixable` 问题会转 `reject` 而非执行。
- 结果分析仍是启发式 + 可选 LLM；`product_defect` 永远要求人工复核，不替代缺陷管理流程。
- 长 LLM 调用、Playwright 执行和静态扫描前会主动结束只读事务并释放数据库连接，避免远程 MySQL/网络设备关闭空闲连接后写回报 2013；阶段异常会用新连接落库，不再出现 PendingRollback 掩盖原始错误。
- LLM 默认超时为 240 秒；超时后审核会降级为确定性规则、结果分析会降级为规则分析，不会因为模型超时直接伪造结果。
- 推理模型（如 GLM-5.3-flash）默认推理强度会把输出预算全花在 reasoning_content 上；客户端对 GLM-5.3/5.2 自动降 `reasoning_effort=low`，并兼容 content 分片、reasoning_content 中的合法 JSON、`finish_reason=length` 时的预算提升重试。只有仍拿不到最终答案才停止后续 LLM 批次并规则兜底。
- LLM 返回的 `expectedResults` 若是单个字符串会整体保留，不会逐字符拆分；历史逐字符脏数据在读取时自动归一化，已有用例可用 `scripts/fix_expected_results.py` 修复。
- 如果远程数据库/网络本身中断，阶段仍可能失败；失败状态会保留，重新发起运行即可，不会留下假成功。
- 启动恢复只回收长时间无活动的 running 运行（默认 2 小时），避免多 worker 启动时误杀仍在执行的 Pipeline；崩溃运行可能保持 running 到超过该阈值。
- 实验 A 顺序执行存在顺序/数据耦合；实验 B 指标依赖标注质量与样本量。仓库不预置任何“提升百分比”。
- `estimatedCost` 为 `null`，因为当前客户端未把响应 usage 纳入成本估算；不要把它解读为 0 成本。

## 12. 文件清单

后端：

- `app/entity/models.py`：新增工作流表、LLM profile、序号表、生成版本快照表
- `app/workflow/schemas.py`：`WorkflowCreate/TestPointDraft/ReviewIssue/CaseReviewResult/ResultAnalysisResult/FailureAnalysisLLM`
- `app/agent/prompt_loader.py`：`{{VAR}}` 模板加载/热更新/缺变量报错
- `app/agent/prompts/*.txt`：7 个外置提示词
- `app/agent/test_point_agent.py`、`test_case_agent.py`、`result_analysis_agent.py`
- `app/agent/count_assertion.py`：固定总数 → capture + 基线+1
- `app/agent/case_review_agent.py`：提示词外置 + 工作流三态审核
- `app/service/requirement_service.py`：`parse_points/save_points`，Agent 1 接入
- `app/service/generation_service.py`：Agent 2 接入
- `app/service/case_review_service.py`：可选注入 LLM 客户端；修复覆盖度映射的 ORM/dict 兼容修复
- `app/service/settings_service.py`：多 LLM profile、.env 迁移、默认/连通性
- `app/service/workflow_service.py`：7 阶段编排器（含自动代码分析）
- `app/routers/workflows.py`：工作流 API
- `app/service/report_service.py`：报告增加工作流区块
- `app/main.py`、`app/routers/mindmap.py`：路由注册/报告参数
- `app/evaluation/metrics.py`：指标纯函数

前端：

- `frontend/src/api/workflows.js`
- `frontend/src/components/WorkflowTab.vue`（含 Pipeline 模式）
- `frontend/src/views/PipelineView.vue`：独立 Pipeline 执行页面
- `frontend/src/views/SettingsView.vue`：文本/VLM 多配置管理
- `frontend/src/components/QuickStartDialog.vue`：快速创建项目 + 需求版本 + 上传 PRD
- `frontend/src/App.vue`、`frontend/src/router/index.js`：侧边栏「Pipeline 执行」入口
- `frontend/src/views/ProjectDetailView.vue`：移除原工作流 tab，统一走独立页面

测试与脚本：

- `tests/test_workflow.py`、`tests/test_evaluation.py`、`tests/test_count_assertion.py`、`tests/test_llm_profiles.py`
- `scripts/evaluate_workflow.py`、`scripts/evaluate_result_analysis.py`
- `migrations/mysql/001_init.sql`（已包含新表）


## 增量需求分析（多期迭代）

- 每个需求版本可建立一份 `requirement_snapshots` 接口/规则快照；下一期以该快照为明确基线。
- 变更识别对比 HTTP 方法、规范化路径、参数/必填性/约束、鉴权、关联业务规则，输出 `added / modified / deleted / unchanged / uncertain`。
- 文档未提及的旧接口只标记 `uncertain`，不会直接删除；明确删除或人工确认后才进入 `pending_delete`，历史用例与执行记录保留。
- 只对新增/修改测试点生成；未变化接口复用历史用例内容（克隆到当前版本，不调用 LLM）；修改接口保留旧用例并生成变化部分。
- 回归推荐按直接受影响、间接受影响、未发现影响分级，记录推断原因，支持人工采纳/忽略。
- 新增表：`requirement_snapshots`、`requirement_change_runs`、`requirement_changes`、`requirement_case_changes`；已有库执行 `migrations/mysql/002_incremental.sql`。
- 已知限制：基线快照应在一期代码分析后建立；参数/鉴权/规则匹配和影响推荐是规则推断，不是完整调用链分析；无前端页面的新增接口可能生成 manual/待补充用例。
