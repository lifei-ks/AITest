# AICase Core

**基于「被测系统事实 + 需求文档」的测试用例自动生成与真实浏览器自动执行平台。**

输入一个 Spring Boot + Vue 项目的源码和本期需求文档，自动产出：结构化测试点、可直接执行的测试用例（带具体参数）、真机执行证据（逐步截图 + 录屏）、AI 截图审核报告、覆盖矩阵、HTML 报告与脑图。

> 需求版本（V1.0 / V2.0 …）贯穿全链路：不同期需求文档对应不同测试用例，支持按版本筛选、隔离、回看历史。

## 核心能力

### 1. 代码静态分析（纯规则）
- 后端：接口端点、实体字段、`@NotBlank/@Size/@Pattern` 约束、枚举、业务规则
- 前端：页面与路由、表单字段、placeholder/选择器、UI 文案
- 状态机：从状态枚举 + 业务方法推断状态流转（带 `verified` 证据标记）

### 2. 需求版本管理
- 一期需求 = 一个版本（`V1.0 / V2.0 …`），支持新建、编辑、上传 PRD（txt/md/docx/pdf）
- 修改需求内容会作废旧测试点，等待重新解析

### 3. 测试点抽取（LLM 优先，规则兜底）
- LLM 阅读「本期需求文档 + 代码事实」，抽取最多 20 个测试点（主流程/校验/边界/状态/权限）
- 测试点带分类、优先级、页面路由、涉及字段、预期结果，并自动关联代码中的真实路由与字段
- 无 Key / LLM 失败时自动降级规则模式
- 已实测：对演示订单需求一次抽取 18 个测试点，全部 `source=llm`（置信度 0.95），路由/接口/字段均为被测系统真实值

### 4. 用例生成（按测试点，带具体参数）
- LLM 按测试点小批量生成（每批 5 条），要求回填真实路由/placeholder/按钮文案/具体输入值，禁止编造不存在的元素
- 某批失败只对该批降级规则，不影响整体；规则模式按正例/反例/边界/流转四类补足
- 强制用例首步为页面导航，保证可被执行器消费
- 三道质量闸：Lint 拦截、100 分制打分、相似度去重
- 已实测：按版本一次生成 18 条用例（质量闸 0 拦截），13 条 LLM 产出（置信度 0.85、质量分 100，选择器与测试数据均为真实值），5 条因批次失败按设计自动降级规则

### 5. 自动执行（Playwright）
- 结构化步骤 `ui_action / input / state_assert`
- 五级定位兜底（选择器 → 文本 → 全页 → XPath）+ 三层断言（URL → 文案锚点 → 诚实 skipped）
- 逐步截图 + WebM 录屏，失败即停并记录原因

### 6. AI 截图审核（多模态 LLM）
- 执行完成后自动取 6 张关键截图交给多模态模型审核
- 输出：结论 + 置信度 + 逐图分析 + 问题区域坐标（红框标注）
- `ok` 且无技术错误 → 自动通过；`suspect / unavailable` → 待复核
- 支持人工通过/判失败、单条/批量重新审核
- 多模态模型可复用文本 LLM，也可单独配置

### 7. 用例管理与版本隔离
- 列表筛选（含需求版本）、编辑、版本历史/回滚、批量删除、复制、CSV 导入导出
- 用例通过 `requirement_cases` 与版本映射；删除版本只删其自动生成的用例，手工用例保留
- 用例列表、脑图、执行结果均支持按需求版本区分

### 8. 覆盖矩阵 / 报告 / 脑图
- 覆盖矩阵：规划 vs 执行（接口 / 状态流转 / 业务规则）
- HTML 报告（含截图）、XMind / Markdown 脑图导出

### 9. 多 Agent 工作流 / Pipeline 一键执行
- 独立侧边栏「Pipeline 执行」：选项目 + 需求版本，一键完成「代码分析 → 测试点 → 用例 → 审核/修订 → 执行 → 结果分析 → 报告」
- 支持在 Pipeline 页面快速创建项目、创建需求版本并上传 PRD（.md/.txt/.pdf/.docx），创建后自动选中该需求
- 缺少代码分析时自动执行静态分析；已有成功分析时默认复用，可强制重新分析
- 4 个逻辑角色：测试点生成、可执行用例生成、独立用例审核/修订、证据驱动结果分析
- 审核使用「确定性校验 + LLM 建议」：复合场景/多输入/缺必填字段会被拦截；不通过不执行，超过修订上限转 `reject`，不静默丢弃
- 结果分析只写自己的分析表，**绝不覆盖真实执行状态**；证据不足一律 `unknown` 并交人工复核
- 数量断言不再写死：固定「共 N 条」会自动改写为 `capture` 基线 + `${base_count+1}`；无法建立基线的固定总数用例不执行
- 不接入工作流时，原有需求解析/生成/执行接口行为不变
- 详细设计、API、实验方法与限制见 [docs/MULTI_AGENT_WORKFLOW.md](docs/MULTI_AGENT_WORKFLOW.md)

### 10. 多 LLM 配置
- 设置页分「文本 LLM」和「多模态 VLM」两组，每组可配置多条，独立保存名称、API Key、Base URL、模型
- 测试点解析、用例生成可分别选择文本 LLM；Pipeline 高级配置里可分别选择测试点 LLM 和用例 LLM
- VLM 用于执行截图审核；没有可用 VLM 时回退默认文本 LLM
- 首次读取设置时会把 `.env` 的单套文本/多模态配置迁移成默认 profile；之后以设置页配置为准
- API 只返回掩码 Key；更新时回传掩码表示保留原 Key

## 架构

```
需求文档(本期) ──┐
                 ├─► 测试点(LLM 优先/规则兜底) ──► 用例(按测试点,LLM+规则)
代码源码 ──静态分析──┘                                          │
                 ┌──────────────────────────────────────────────┤
                 ▼                                              ▼
        结构化事实(端点/字段/约束/状态机/表单/选择器)       质量闸 → 落库(按版本隔离)
                                                                 │
                                           自动执行(Playwright)  │
                                                                 ▼
                                        逐步截图 + 录屏 ──► 多模态 AI 审核
                                                                 │
                                         通过 / 待复核(红框定位+逐图报告)
```

**确定性 / 不确定性分流**：
- 静态分析、状态机提取、质量闸、覆盖率统计、执行器 = **纯规则**（可复现、可审计）
- 需求→测试点、事实→用例参数、截图语义判断 = **LLM**（可选，可降级/摘除）

**多 Agent 工作流 / Pipeline**

```
需求版本 ─► 代码分析(缺则自动) ─► Agent1 测试点 ─► Agent2 用例 ─► Agent3 独立审核/修订(≤2轮)
                                                                    │
                                                          仅 pass + automated
                                                                    ▼
                                                    现有 Playwright 执行器（截图/录屏/步骤）
                                                                    ▼
                                                    Agent4 证据驱动结果分析（不改执行状态）
                                                                    ▼
                                                        workflow 报告 + 项目报告附加区块
```

## 技术栈

- 后端：FastAPI · SQLAlchemy 2.0 · Pydantic v2 · SQLite/MySQL · Jinja2
- 前端：Vue 3 · Element Plus · Pinia · Vue Router · ECharts
- 执行：Playwright（Chromium）
- LLM：OpenAI 兼容协议（`/chat/completions`），支持 JSON mode 与多模态图片输入

## 快速开始

> 环境：Python 3.11+、Node.js 18+

```bash
# 0. 安装依赖
python -m venv .venv
.venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cd frontend
npm install
cd ..

# 1. 配置环境变量
cp .env.example .env            # 按需填写 DATABASE_URL / LLM_*

# 2. 启动后端（自动建表；--reload 让后端代码改动后自动生效）
.venv/Scripts/python -m uvicorn app.main:app --port 8100 --reload --reload-dir app

# 3. 启动前端（默认代理到 8100）
cd frontend && npm run dev
# 打开 http://127.0.0.1:5173

# 4. 演示靶站（自动执行需要）
.venv/Scripts/python scripts/demo_site/serve.py   # 端口 8200
```

## 配置（`.env`）

| 变量 | 说明 | 默认 |
|---|---|---|
| `DATABASE_URL` | SQLite 或 MySQL 连接串 | `sqlite:///./data/aicase.db` |
| `OUTPUT_DIR` | 截图/录屏/报告输出目录 | `./outputs` |
| `TARGET_URL_DEFAULT` | 被测站点默认地址 | `http://127.0.0.1:8200` |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 文本 LLM 初始配置；首次读取设置时迁移为默认 profile | 空 = 纯规则模式 |
| `LLM_VISION_API_KEY` / `LLM_VISION_BASE_URL` / `LLM_VISION_MODEL` | 多模态初始配置；留空自动复用默认文本 profile | 空 |

## 使用流程

```
1. 项目列表 → 新建项目（源码路径 / 被测地址）
2. 进入项目 → 触发代码分析
3. 「需求版本」→ 新建 V1.0，填写/上传本期需求文档 → 解析测试点
4. 「用例生成」→ 选版本 → 生成用例（自动/LLM/规则）
5. 「设置」→ 文本 LLM / VLM 可分别配置多条，并选择默认
6. 「测试用例」→ 执行单条/批量
7. 「执行结果」→ 查看截图、录屏、AI 审核报告；待复核的人工处理；同一用例多次执行时点「历史」进入独立页面查看全部记录
8. 侧边栏「Pipeline 执行」→ 可快速创建项目并上传需求文档 → 选需求版本 → 一键完成代码分析、测试点、用例、审核/修订、执行、结果分析、报告
9. 「脑图」/「下载 HTML 报告」按需导出
```

## 目录结构

```
app/              后端（routers/service/analyzer/agent/llm/entity/common/dto/evaluation/workflow）
app/agent/prompts/ 外置提示词（测试点/用例/审核/修复/结果分析，{{VAR}} 占位符）
frontend/         Vue 3 前端
scripts/          demo_site(演示靶站) / sample_src(样本源码) / e2e.py / analyze_cli.py / gen_mysql_ddl.py / evaluate_*.py
tests/            后端测试
migrations/mysql/ MySQL DDL（由 scripts/gen_mysql_ddl.py 生成）
docs/             任务书 / 计划 / 迭代记录 / 多 Agent 工作流设计
main.py           便捷入口
```

## 关键接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/projects` | 项目列表 / 新建 |
| POST | `/api/projects/{pid}/analyze` | 触发代码分析 |
| GET/POST | `/api/projects/{pid}/requirements` | 需求版本列表 / 新建 |
| POST | `/api/requirements/{rid}/analyze` | 解析测试点（auto/llm/rule） |
| POST | `/api/requirements/{rid}/generate` | 按版本生成用例 |
| GET | `/api/requirements/{rid}/generate/stream` | SSE 流式生成 |
| GET | `/api/projects/{pid}/testcases?requirementId=` | 用例列表（按版本过滤） |
| POST | `/api/projects/{pid}/testcases/{cid}/execute` | 执行单条用例 |
| POST | `/api/projects/{pid}/testcases/batch-execute` | 批量执行 |
| GET | `/api/projects/{pid}/executions?requirementId=` | 执行结果（默认按用例去重并展示最新一次） |
| GET | `/api/projects/{pid}/testcases/{cid}/executions` | 单个用例的执行历史 |
| POST | `/api/executions/{eid}/llm-review` | 单条 AI 审核 |
| POST | `/api/executions/batch-llm-review` | 批量 AI 审核 |
| POST | `/api/executions/{eid}/review` | 人工复核（pass/fail） |
| GET | `/api/meta/execution` | 执行状态、AI 结论和错误分类的完整枚举说明 |
| GET/POST | `/api/settings/llm-profiles` | 多 LLM 配置列表 / 新增 |
| PUT/DELETE | `/api/settings/llm-profiles/{id}` | 修改 / 删除配置 |
| POST | `/api/settings/llm-profiles/{id}/default` | 设为同类默认配置 |
| POST | `/api/settings/llm-profiles/{id}/test` | 测试该配置连通性 |
| POST | `/api/projects/{pid}/workflows` | 启动多 Agent 工作流（异步） |
| GET | `/api/projects/{pid}/workflows` | 项目工作流运行列表 |
| GET | `/api/workflows/{run_id}` | 工作流详情（阶段、审核轮次、执行、分析） |
| GET | `/api/workflows/{run_id}/report` | 工作流 HTML 报告 |
| GET | `/api/projects/{pid}/report?workflowRunId=` | 项目报告附带指定工作流汇总 |
| POST | `/api/projects/{pid}/testcases/ai-review` | 用例生成后的 AI 质检，输出问题与需求/源码依据 |
| POST | `/api/projects/{pid}/testcases/ai-fix` | 基于质检问题生成修复补丁，校验通过后落版本 |
| GET | `/api/projects/{pid}/mindmap/preview?requirementId=` | 脑图预览 |
| GET | `/api/projects/{pid}/coverage/matrix?requirementId=` | 规划/执行覆盖矩阵 |
| GET | `/api/projects/{pid}/report?requirementId=` | HTML 报告 |

## 覆盖矩阵口径

- **规划覆盖**：用例 `coverageRefs` 中实际引用到当前代码事实的接口、已验证状态转换和业务规则。
- **执行覆盖**：只统计执行记录 `status=passed` 的用例，并优先读取执行瞬间的用例快照，避免后来编辑用例导致历史覆盖漂移。
- 失败、跳过、待复核、执行中和已取消均不计入执行覆盖。
- 覆盖矩阵支持 `requirementId` 参数，页面选择需求版本时只统计该版本用例。
- 完整结构可通过 `GET /api/meta/execution` 查看。

## 执行状态、AI 审核与错误分类

用例执行状态：

| 值 | 含义 |
|---|---|
| `not_run` | 尚未执行 |
| `running` | 正在真实浏览器中执行 |
| `passed` | 确定性检查全部通过，或证据经 AI/人工复核确认符合预期 |
| `failed` | 出现确定错误，或人工复核确认不符合预期 |
| `skipped` | 存在无法验证的步骤，缺少充分证据 |
| `needs_review` | 证据不足、AI 判断有疑点或审核不可用，需要人工确认 |
| `cancelled` | 执行被中止，不能视为通过 |

AI 审核结论：

| 值 | 含义 |
|---|---|
| `ok` | 截图和步骤证据支持预期；不能覆盖确定性的执行失败 |
| `suspect` | 截图异常、与预期不符，或证据不足以证明预期 |
| `unavailable` | 模型未配置、调用失败、响应非法或没有可用截图 |

自动执行错误分类：

| 值 | 含义 |
|---|---|
| `SELECTOR_NOT_FOUND` | 元素未找到，记录尝试过的选择器链 |
| `ASSERT_FAILED` | URL 或引号锚定文案断言不通过 |
| `NAVIGATION_FAILED` | 页面打不开或返回 4xx/5xx |
| `TIMEOUT` | 页面操作或等待超过默认超时 |
| `CASE_INVALID` | 用例缺结构化步骤、输入值等必需字段 |
| `ENV_ERROR` | 浏览器启动、驱动或其他环境问题 |

确定性执行失败不会被 AI 的 `ok` 改判为通过；AI 审核只能确认无确定失败的结果，或把不确定结果升级为 `needs_review`。

## 数据模型

启动时由 SQLAlchemy `create_all` 自动建表（也提供 `migrations/mysql/001_init.sql`，共 23 张表），核心表：

- `projects` / `code_analyses` / `state_machines`
- `requirements`（需求版本）/ `test_points`（测试点）
- `test_cases` / `test_case_versions` / `requirement_cases`（版本↔用例映射）
- `execution_records` / `execution_steps` / `execution_requirements`（执行↔版本快照）
- `execution_reviews` / `execution_review_images`（逐图分析）/ `execution_review_issues`（问题定位）
- `workflow_runs` / `workflow_stages` / `workflow_cases` / `workflow_case_reviews` / `workflow_result_analyses`（多 Agent 工作流）
- `llm_profiles`（文本/VLM 多套 LLM 配置）
- `id_sequences`（跨进程 TC 编号与项目内序号序列）
- `case_generations`（每次用例生成的版本快照）

## 验证

```bash
pytest                                   # 后端全量测试（临时库）
cd frontend && npm run build             # 前端构建
.venv/Scripts/python scripts/e2e.py      # 端到端自检
.venv/Scripts/python scripts/analyze_cli.py <源码路径>   # 命令行静态分析
.venv/Scripts/python -m pytest -q tests/test_workflow.py tests/test_evaluation.py  # 工作流与评测
```

实验脚本（会改写生成用例，需 `--allow-write`）：

```bash
.venv/Scripts/python scripts/evaluate_workflow.py --project-id <pid> --requirement-id <rid> --mode rule --allow-write
.venv/Scripts/python scripts/evaluate_result_analysis.py --samples labels.jsonl --mode rule
```

## 已知边界（诚实声明）

- 静态分析基于启发式正则，覆盖常见写法，非语法级精确解析。
- LLM 生成的截图问题红框坐标为模型估算，存在偏移；后端已做裁剪/校验，不会越界。
- 执行依赖被测站点已在 `target_url` 运行、且本机已安装 Playwright Chromium。
- 项目分析当前支持 `local_path`，Git 仓库拉取未实现。
- 单次批量 AI 审核上限 20 条，逐条串行调用，耗时随条数线性增长。
- 多 Agent 工作流新增的测试默认使用离线 LLM 和假执行器；真实执行复用现有 Playwright 执行器，需要被测站点与 Chromium 已就绪。工作流不会自动重试目标不可达的执行阶段。
- Pipeline 快速创建项目仍按本机源码路径登记；源码压缩包上传和 Git 自动拉取尚未实现。
- 共享测试数据没有通用自动 reset；数量变化使用 `capture` 运行时基线，固定总数且无创建动作的用例会被拒绝/丢弃。
- LLM API Key 保存在服务端 `.env` / 数据库，接口只返回掩码；请限制服务器与数据库访问权限。
- 推理模型由客户端自动适配：GLM-5.3/5.2 自动降 `reasoning_effort=low`；content 为空时接受 reasoning_content 中的合法 JSON；`finish_reason=length` 时提升 token 预算重试一次。只有仍拿不到最终答案才规则兜底。
- LLM 返回的 `expectedResults` 若是单个字符串会整体保留，不会逐字符拆分；历史逐字符脏数据在读取时自动归一化，已有用例可用 `scripts/fix_expected_results.py` 修复。
- 测试点生成会过滤“展示 N 个字段/默认 N 条数据”这类静态页面结构测试点，字段存在性由用例事实和运行时断言覆盖。
- 用例生成使用 SSE：页面显示逐批进度、逐条用例和来源（LLM/规则）；LLM 降级时页面明确提示规则兜底原因。
- 每次生成保存版本快照；生成页和测试用例页都始终显示版本下拉，提供「实时用例（未归档）」和按时间倒序的生成快照。改造前已有的实时用例首次查看时自动归档为「历史归档」版本，不会把多次生成混在一起；live 用例始终只保留最新一次，避免跨版本重复。测试用例页查看历史版本时只读，执行/编辑/删除/AI 质检需切回实时用例。
- 评估实验的 `cost` 为 `null`：客户端虽然记录了响应 usage 用于诊断，但未把它纳入成本估算；只报告真实调用次数与耗时。
- 工作流按单需求版本串行执行；同一需求顺序做基线与方案对比属于配对观察，不是独立随机样本。

## 发布到 GitHub

```bash
git init -b main
git add .
git commit -m "chore: initial commit"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

> `.env`、`data/`、`outputs/`、`node_modules/`、`dist/` 等已在 `.gitignore` 中排除，不会误提交敏感信息。
