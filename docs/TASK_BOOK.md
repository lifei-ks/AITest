# AICase Core 任务书（TASK BOOK）

> **本文件是唯一需求来源。** 实现过程中若本文件与你的经验冲突，以本文件为准；本文件未覆盖的细节，按"最小可用 + 可解释"原则自行决策，并在代码注释里写明理由。
>
> 版本：v1.0 ｜ 目标读者：执行编码的 AI Agent ｜ 交付形态：可本地一键启动的完整前后端项目

---

## 0. 一页纸速览

**要做的东西**：一个测试用例自动生成 + 真实浏览器自动执行的平台。

**输入**：一个被测项目的源码（Spring Boot 后端 + Vue 前端）和/或需求文档（PRD）。

**输出**：结构化测试用例（可编辑、可版本化）、真机执行证据（每步截图 + 每条用例 WebM 录屏）、覆盖矩阵、HTML 报告、XMind 脑图。

**五个功能模块（本项目的全部业务范围）**：

| 编号 | 模块 | 一句话职责 |
|---|---|---|
| M1 | 用例生成 | 读源码和文档 → 提取事实 → 生成可执行的结构化用例 |
| M2 | 用例管理 | 增删改查、版本、筛选、导入导出、质量分与覆盖状态 |
| M3 | 自动执行 | Playwright 驱动真实浏览器跑用例，逐步骤留证 |
| M4 | 脑图 | 把用例集渲染成可导出的树，用来快速评审覆盖结构 |
| M5 | 执行结果 | 历史、详情、批次、报告、覆盖率度量 |

**支撑能力（不计入五模块，但必须有，否则主流程跑不通）**：项目管理（含源码路径/Git 克隆/PRD 上传）、代码静态分析、LLM 配置。

**不做**：用户注册登录、项目组与 RBAC、任务队列与租约恢复、多租户、向量数据库、证据冲突裁决、MCP Server。理由：这五个模块自身的复杂度已经足够，加这些东西只会稀释重点。

**验收基线（硬指标）**：

```
不配置任何 LLM API Key 的情况下：
  pytest                 → 全部通过
  python scripts/e2e.py  → 生成 >= 12 条用例，执行通过率 >= 90%，
                           截图 >= 用例数×步骤数，每条用例 1 个 WebM，
                           HTML 报告可打开，覆盖率数据自洽
```

---

## 1. 项目定位与设计哲学

### 1.1 定位

不是"输入一句话让大模型编测试用例"的玩具，而是**基于被测系统事实**生成用例、并把"是否真的验证过"如实记录下来的工程工具。

### 1.2 唯一的架构原则：确定性 / 不确定性分流

这是整个项目最核心的设计决策，实现时必须严格遵守：

| 环节 | 方式 | 理由 |
|---|---|---|
| 代码静态分析（端点、实体、枚举、规则、表单、选择器、文案） | **纯规则**（正则 + 括号配平 + 状态机） | 结果必须可复现、可审计，不能每次跑出不同答案 |
| 状态机提取 | **规则为主 + 源码证据** | 每条转换必须能指到 `file.java:line`，指不到就不算 verified |
| 用例质量校验 | **纯规则**（Linter + 打分） | 质量闸不能自己也是概率模型 |
| 覆盖率统计 | **纯规则** | 度量必须确定 |
| 事实 → 自然语言用例 | **LLM**（可选） | 这一步确实需要语言能力 |
| 视觉定位（Agent 模式） | **LLM 多模态**（可选） | 兜底手段，不是主路径 |

**推论（重要）**：LLM 必须是**可摘除**的。所有 LLM 调用点都要有规则降级路径，且降级路径要能独立跑完整条链路。没配 Key 时系统必须完整可用，只是用例更"模板化"。

### 1.3 三条铁律

1. **不假绿**：验证不了就标 `skipped`，绝不因为"没报错"就判通过。
2. **不编造**：用例里出现的每个页面元素、每条提示文案，尽量来自静态分析提取的事实；确实靠推断的，`confidence` 要低。
3. **证据可回溯**：任何"已覆盖"的结论，都要能点回到具体的执行记录、步骤、截图。

---

## 2. 范围

### 2.1 IN（必须实现）

```
项目管理       项目 CRUD、本地路径 / Git 仓库、PRD 上传（md/txt/pdf/docx）
代码分析       Spring Boot + Vue 静态扫描，产出结构化事实 + 状态机
用例生成       规则模式（必跑通）+ LLM 模式（可选）+ 三道质量闸 + 流式输出
用例管理       列表/筛选/编辑/删除/版本/回滚/导入导出/质量分/覆盖回写
自动执行       单条 + 批量 + 批次查询 + 取消 + 双模式 + 三层断言 + 截图录屏
脑图           生成/预览/导出 XMind 与 Markdown
执行结果       历史/详情/批次/HTML 报告/覆盖矩阵
设置           LLM 供应商配置与连通性测试
内置演示靶站   一个可被自动执行的被测系统，供 e2e 与首次体验使用
```

### 2.2 OUT（明确不做，不要自行添加）

```
用户体系、登录鉴权、JWT、RBAC、项目组
任务队列、租约、心跳、故障恢复调度
Milvus / 向量检索 / RAG / 语义去重（可用简单的编辑距离去重替代）
证据冲突裁决、多维证据对账
MCP Server / MCP Bridge
Prometheus / Grafana
Testcontainers / Docker Compose 编排（提供 Dockerfile 即可）
App 模式 / 移动端
```

> 若你认为某项不做会导致主流程断裂，先说明理由再决定，不要默默扩大范围。

---

## 3. 技术栈与工程结构

### 3.1 后端

| 项 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 用 `from __future__ import annotations` 保持类型注解整洁 |
| Web | FastAPI + Uvicorn | 必须自动生成 `/docs` |
| ORM | SQLAlchemy 2.0（`Mapped` / `mapped_column` 风格） | 不用老式 `Column()` 声明 |
| 数据库 | SQLite 默认，`DATABASE_URL` 可切 MySQL | 必须补 `pymysql` 依赖 |
| 校验 | Pydantic v2 + pydantic-settings | 配置从 `.env` 读 |
| 浏览器 | Playwright（chromium） | 录屏用 `record_video_dir` |
| 定时/异步 | `asyncio` + 后台 Task | 执行任务用 `asyncio.create_task`，不引入 Celery |
| 模板 | Jinja2 | 渲染 HTML 报告 |
| 导出 | `openpyxl` 不引入；XMind 手写 zip（见 6.4） | 尽量少依赖 |

### 3.2 前端

| 项 | 选型 |
|---|---|
| 框架 | Vue 3（`<script setup>`）+ Vite |
| UI | Element Plus |
| 状态 | Pinia（只存全局配置和当前项目） |
| 请求 | axios 封装，统一响应拦截 |
| 路由 | vue-router 4，history 模式 |
| 图表 | ECharts（覆盖率、执行趋势） |
| 脑图渲染 | 用 ECharts `tree` 或自研缩进树，二选一，不引入重型库 |

### 3.3 目录结构（必须照此组织）

```
aicase-core/
├── app/
│   ├── main.py                 # FastAPI 实例、路由注册、启动钩子
│   ├── config.py               # pydantic-settings，含 @lru_cache
│   ├── database.py             # engine / SessionLocal / Base / session_scope
│   ├── entity/models.py        # 8 张表
│   ├── dto/                    # 请求体、响应序列化
│   ├── common/
│   │   ├── response.py         # 统一响应信封
│   │   └── errors.py           # 业务异常 + 全局异常处理器
│   ├── routers/                # projects / analysis / generation / testcases
│   │                           # executions / mindmap / settings / stats
│   ├── service/                # 业务编排，路由层不写业务
│   ├── analyzer/               # 纯规则静态分析
│   │   ├── scanner.py          # 文件收集、技术栈识别
│   │   ├── spring.py           # Java 后端解析
│   │   ├── vue.py              # Vue 前端解析
│   │   └── models.py           # 分析结果 dataclass
│   ├── agent/
│   │   ├── state_machine_agent.py
│   │   ├── test_generator_agent.py
│   │   ├── execution_agent.py
│   │   └── quality_gate.py     # Linter + 打分，合并成一个文件
│   ├── llm/client.py           # 多供应商客户端（失败即降级）
│   ├── templates/report.html.j2
│   └── resources/skills/*.md   # 提示词片段
├── frontend/
│   └── src/{views,api,components,router,stores}
├── scripts/
│   ├── demo_site/              # 内置被测系统（含 SPA 路由回退）
│   ├── sample_src/             # 样本源码（Spring Boot + Vue）
│   └── e2e.py                  # 端到端自检
├── migrations/mysql/001_init.sql
├── tests/
├── outputs/                    # 运行产物（截图/录屏/报告），git 忽略
├── .env.example
├── requirements.txt
└── README.md
```

---

## 4. 数据模型

8 张表。字段类型写 Python 侧类型，DDL 由 ORM 生成。

### 4.1 `projects`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | str(36) PK | `uuid4().hex` |
| name | str(120) | 必填 |
| description | Text? | |
| source_type | str(20) | `local_path` / `git` |
| source_path | Text? | 本地绝对路径 或 克隆后的目录 |
| git_url / git_branch | str? | |
| tech_stack | Text? | JSON：`{backend:"java-spring", frontend:"vue", ...}` |
| prd_content | Text? | 需求文档纯文本 |
| target_url | Text? | 被测站点基址，默认 `http://127.0.0.1:8200` |
| created_at / updated_at | datetime | 默认 `utcnow` |

### 4.2 `code_analyses`

| 字段 | 类型 |
|---|---|
| id / project_id | str |
| status | str：`pending` / `running` / `success` / `failed` |
| backend_result / frontend_result | Text（JSON） |
| warnings | Text（JSON 数组） |
| error_message | Text? |
| created_at | datetime |

`backend_result` 结构（必须严格按此）：

```json
{
  "language": "java-spring",
  "endpoints": [{"id":"ep-1","method":"POST","path":"/api/orders/{id}/pay","controller":"OrderController","methodName":"pay","file":"...OrderController.java","line":42}],
  "entities": [{"name":"Order","file":"...","fields":[{"name":"amount","type":"BigDecimal"}],"fieldConstraints":[{"field":"remark","rule":"maxLength","value":200}]}],
  "enums": [{"name":"OrderStatus","values":[{"name":"CREATED","label":"待支付"}]}],
  "businessRules": [{"id":"br-1","description":"非待支付状态不可支付","guard":"!\"CREATED\".equals(status)","file":"...","line":58}],
  "uiTexts": ["订单创建成功","请输入收货人"]
}
```

`frontend_result` 结构：

```json
{
  "language":"vue",
  "pages": [{"name":"OrderCreate","route":"/orders/create","file":"...","forms":[{"fields":[{"label":"收货人","prop":"receiverName","type":"input","required":true,"placeholder":"请输入收货人","rules":["required"]}]}]}],
  "selectors": [{"type":"placeholder","value":"请输入收货人","page":"OrderCreate"}],
  "uiTexts": ["提交订单","订单创建成功"],
  "components": []
}
```

### 4.3 `state_machines`

| 字段 | 类型 |
|---|---|
| id / project_id | str |
| name | str |
| states | Text（JSON：`[{"name":"CREATED","label":"待支付"}]`） |
| transitions | Text（JSON，见下） |
| confidence | float |

单条 transition：

```json
{
  "id": "tr-1",
  "from": "CREATED",
  "to": "PAID",
  "trigger": "支付",
  "verified": true,
  "evidence": ["OrderServiceImpl.java#62", "OrderController.java#45"],
  "guard": "!\"CREATED\".equals(order.getStatus())"
}
```

> `verified=true` 的**唯一**判据：找到了写状态的那行代码（`setStatus(X)`）。找不到就 `false`，不要猜。

### 4.4 `test_cases`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | str PK | 全局唯一，格式 `TC-{6位递增}` |
| project_seq | int | 项目内序号，从 1 开始，删除不回收 |
| project_id | str | 索引 |
| title | str(255) | |
| module | str(120) | 业务模块，如"订单" |
| type | str(20) | `positive` / `negative` / `boundary` / `state` |
| priority | str(10) | `P0`~`P2` |
| preconditions | Text | JSON 数组 |
| steps | Text | JSON 数组（人话，用于展示） |
| expected_results | Text | JSON 数组 |
| structured_steps | Text | JSON 数组（执行器消费，见 6.3.2） |
| test_data | Text | JSON 对象 |
| coverage_refs | Text | JSON：`{"endpointIds":[],"transitionIds":[],"ruleIds":[]}` |
| source | str(20) | `rule` / `ai` / `manual` / `import` |
| confidence | float | 0~1 |
| quality_score | int | 0~100 |
| lint_issues | Text | JSON 数组 |
| execution_status | str(20) | `not_run` / `passed` / `failed` / `skipped` |
| created_at / updated_at | datetime | |

### 4.5 `test_case_versions`

| 字段 | 类型 |
|---|---|
| id / test_case_id | str |
| version | int（从 1 递增） |
| snapshot | Text（完整用例 JSON） |
| change_note | str? |
| created_at | datetime |

### 4.6 `execution_records`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | str | `EX-{uuid8}` |
| project_id / test_case_id | str | 索引 |
| test_case_title | str | 冗余存一份，用例删除后历史仍可读 |
| test_case_snapshot | Text | **执行瞬间的用例完整快照（强制）** |
| batch_id | str? | 索引 |
| status | str | `running` / `passed` / `failed` / `skipped` / `cancelled` |
| mode | str | `programmatic` / `agent` |
| target_url | str | |
| start_time / end_time | datetime | |
| duration_ms | int | |
| error_category | str? | 见 6.3.6 |
| error_message | Text? | |
| video_path | str? | WebM 相对路径 |
| passed_steps / total_steps | int | |

> `test_case_snapshot` 不是可选项。用例被改或被删后，历史报告必须还能精确复现当时的证据链。

### 4.7 `execution_steps`

| 字段 | 类型 |
|---|---|
| id / execution_id | str（索引） |
| step_index | int |
| action / target | str |
| step_type | str |
| selector_used | str?（实际生效的选择器） |
| result | str：`passed` / `failed` / `skipped` |
| screenshot_before / screenshot_after | str?（相对路径） |
| error | Text? |
| duration_ms | int |

### 4.8 `mindmaps`

| 字段 | 类型 |
|---|---|
| id / project_id | str |
| title | str |
| tree | Text（JSON，见 6.4.1） |
| statistics | Text（JSON：`{"modules":5,"cases":12,"leaves":82}`） |
| status | str |
| created_at | datetime |

---

## 5. API 契约

### 5.1 统一响应信封（全局强制）

所有接口（含错误）返回：

```json
{ "code": 0, "message": "ok", "data": {} }
```

- 成功：`code = 0`
- 业务失败：`code = 400xx`（4xxxx 段），HTTP 状态码同步设置
- 未捕获异常：`code = 50000`，`message` 不含堆栈（堆栈写日志）

前端 `request.js` 的判定：`code === 0` 为成功，否则 `ElMessage.error(message)` 并 reject。

**分页响应统一**：`{"total": 120, "page": 1, "pageSize": 20, "items": [...]}`，另外在**用例列表**额外带 `testCases` 别名（同一份数据，兼容不同页面读法，见 7.3）。

### 5.2 接口清单

#### 项目与文档

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects` | 列表，支持 `keyword` |
| POST | `/api/projects` | `{name, description?, sourceType, sourcePath?, gitUrl?, gitBranch?}` |
| GET | `/api/projects/{pid}` | 详情（含 techStack、分析状态） |
| PUT | `/api/projects/{pid}` | 更新（含 `targetUrl`） |
| DELETE | `/api/projects/{pid}` | 级联删除分析/用例/执行/脑图 |
| PUT | `/api/projects/{pid}/prd` | `{content}` |
| POST | `/api/projects/{pid}/prd/upload` | `multipart`，支持 `.md/.txt/.pdf/.docx` |
| GET | `/api/projects/{pid}/prd` | 读回 |

#### 代码分析

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{pid}/analyze` | 触发，异步，立即返回 |
| GET | `/api/projects/{pid}/analysis` | 当前分析结果 + status |
| GET | `/api/projects/{pid}/analysis/stream` | SSE，事件 `progress` / `done` / `error` |
| GET | `/api/projects/{pid}/state-machines` | 状态机列表 |

#### 用例生成

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{pid}/generate` | `{mode:"rule"\|"llm"\|"auto", modules?:[], types?:[], maxCases?:int}`；异步 |
| GET | `/api/projects/{pid}/generate/stream` | SSE，见 6.1.5 |
| POST | `/api/projects/{pid}/generate/cancel` | 置取消标记 |
| GET | `/api/projects/{pid}/coverage/matrix` | 覆盖矩阵，见 6.5.5 |

#### 用例管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{pid}/testcases` | `page&pageSize&module&type&priority&executionStatus&keyword` |
| POST | `/api/projects/{pid}/testcases` | 手工新建 |
| GET | `/api/projects/{pid}/testcases/{cid}` | 详情 |
| PUT | `/api/projects/{pid}/testcases/{cid}` | 更新，自动落版本 |
| DELETE | `/api/projects/{pid}/testcases/{cid}` | |
| DELETE | `/api/projects/{pid}/testcases/batch` | `{ids:[]}` |
| PUT | `/api/projects/{pid}/testcases/{cid}/execution-status` | `{status}`，执行器回写用 |
| GET | `/api/projects/{pid}/testcases/{cid}/versions` | |
| POST | `/api/projects/{pid}/testcases/{cid}/versions/{vid}/rollback` | |
| POST | `/api/projects/{pid}/testcases/{cid}/review` | 重新跑质量闸，返回 issues + score |
| GET | `/api/projects/{pid}/testcases/export?format=csv` | CSV 下载 |
| POST | `/api/projects/{pid}/testcases/import` | CSV 上传 |
| POST | `/api/projects/{pid}/testcases/import-xmind` | XMind 上传 |
| POST | `/api/projects/{pid}/testcases/copy-to` | `{ids:[], targetProjectId}` |

#### 执行

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{pid}/testcases/{cid}/execute` | `{targetUrl, mode}`，同步等待完成（便于前端直接刷新） |
| POST | `/api/projects/{pid}/testcases/batch-execute` | `{caseIds:[], targetUrl, mode}`，异步，返回 `batchId` |
| GET | `/api/projects/{pid}/executions` | `page&pageSize&status&batchId&testCaseId` |
| GET | `/api/executions/{eid}` | 详情（含 steps 摘要、videoUrl） |
| GET | `/api/executions/{eid}/steps` | 步骤级证据 |
| GET | `/api/executions/{eid}/video` | 返回 `video/webm` 流 |
| GET | `/api/executions/{eid}/file?path=` | 截图等静态证据（**必须做路径穿越校验**） |
| POST | `/api/executions/{eid}/cancel` | |
| GET | `/api/batches/{bid}` | 批次聚合：总数/通过/失败/进行中 + 明细 |
| POST | `/api/batches/{bid}/cancel` | |
| GET | `/api/executions/{eid}/report` | 单条 HTML 报告 |
| GET | `/api/batches/{bid}/report` | 批次 HTML 报告 |

#### 脑图

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects/{pid}/mindmap/generate` | 重新生成树 |
| GET | `/api/projects/{pid}/mindmap/preview` | 返回树 + 统计 |
| GET | `/api/projects/{pid}/mindmap/download?format=xmind\|md` | 文件下载 |

#### 设置与统计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/settings` | Key 掩码返回（`sk-1234***`），未配置返回空串而非 null |
| PUT | `/api/settings` | 更新 |
| POST | `/api/settings/test-llm` | 连通性测试；未配置返回 **400**（配置缺失不是服务故障） |
| GET | `/api/stats/overview` | 首页数字卡：项目数/用例数/执行数/通过率 |
| GET | `/api/health` | 健康检查 |

---

## 6. 模块详细规格

### 6.1 M1 用例生成

#### 6.1.1 前置：静态分析（纯规则，必须零 LLM）

**后端（Spring Boot）解析要求**：

| 目标 | 实现要点 |
|---|---|
| 端点 | 识别 `@GetMapping/@PostMapping/@PutMapping/@DeleteMapping` 及其 `path/value`。**必须处理类级 `@RequestMapping("/api/orders")` 作为前缀**，不能让类级注解自身变成一个端点（这是最常见的坑） |
| 实体字段 | 解析 `private <Type> <name>;`，并收集 `@NotBlank/@Size/@Max/@Min/@Pattern` 约束 |
| 枚举 | 解析 `enum X { A, B; }`，识别状态枚举（名字含 status/state，或取值多为 `CREATED/PAID/...`） |
| 业务规则 | 找 `if (...) throw new XxxException("...")`，提取守卫条件与异常消息 |
| UI 文案 | 收集 `throw` 里的中文消息 + `@NotNull(message="...")` |

**前端（Vue）解析要求**：

| 目标 | 实现要点 |
|---|---|
| 路由 | 从 `router/index.js` 或 `router.js` 里提取 `{ path: '/x', component: X }`。**不要用 `[^{}]*` 这类正则**，`meta:{title}` 会打断匹配——先定位 `path:` 再向左找 `{`，用括号配平 |
| 表单 | `<el-form>`（正则必须写 `<el-form(?![-\w])`，否则 `<el-form-item>` 会被当成表单） |
| 字段 | `v-model` + `prop` + `label` + `placeholder` + `:rules` |
| 选择器 | 由 label/placeholder 反推，产出 `uiSelector` 候选 |
| 文案 | 收集 `ElMessage.success('...')` 等中文串 |

**分析结果必须落 `code_analyses` 表**，前端能被 `GET /analysis` 完整读回。

#### 6.1.2 状态机提取

```
1. 找状态枚举 → states
2. 找所有 setStatus(X) / setXxxStatus(X) 赋值点 → 候选"写入点"
3. 对每个写入点，向上找最近的方法边界，判断方法对应的动作（由方法名/端点路径推断 trigger）
4. 在同一方法内向上找状态守卫（含状态字面量的 if，如 "CREATED".equals(status)）
   - 若守卫块内是 throw → 这是前置校验
   - 语义判定：if(守卫) throw 表示"来源态必须满足守卫"；if(!守卫) throw 表示"来源态不能是守卫"
     （用"是否以 ! 开头"做异或，别硬编码方向）
5. from = 由守卫推出的来源态集合（无守卫则为全部状态 − 目标态）
6. 逐条落 evidence（文件名#行号），有写入点才 verified=true
7. 用 BFS 计算"从初始态到目标态的最短推进路径"，供生成流转用例时前置步骤使用
```

**必须处理的排除语义**：`if ("SHIPPED".equals(s) || "COMPLETED".equals(s)) throw` → 来源态 = 全集 − {SHIPPED, COMPLETED} − 目标态。

#### 6.1.3 两种生成模式

**规则模式（无 LLM，必须能独立产出 >= 12 条用例）**

| 类型 | 生成逻辑 |
|---|---|
| 正例 `positive` | 取表单必填字段全部填合法值 → 点提交 → 预期成功文案（来自 `uiTexts`） |
| 反例 `negative` | 逐个必填字段留空 → 点提交 → 预期该字段的必填提示 |
| 边界 `boundary` | 有 `maxLength/pattern/min` 约束的字段 → 构造刚好越界的值 → 预期校验提示 |
| 流转 `state` | 每个 verified transition 一条：按 BFS 路径先推进到 from 态，再执行 trigger |

**LLM 模式（可选增强）**

- 输入上下文：分析结果（端点/实体/规则/表单/选择器/文案）+ PRD 摘要 + 已有用例标题（避免重复）
- 输出：严格 JSON，用 Pydantic 模型校验；校验失败重试 1 次，仍失败则跳过该条（不阻塞整体）
- **降级**：任何异常（无 Key / 超时 / 429 / JSON 非法）→ 记 warning，自动落回规则模式

**上下文约束（写进提示词，能显著提升可执行率）**：
- 只允许出现分析结果里真实存在的页面、元素、文案
- 步骤 type 只允许 `ui_action` / `input` / `state_assert`，**禁止 `api_call`**
- 第一条 UI 用例必须先是"打开目标页面"的 `ui_action`
- `state_assert` 的预期必须有引号锚定的可见文案

#### 6.1.4 三道质量闸（全规则）

```
闸 1  UiLanguageLinter
      - 拦截"调用接口 / 发送请求 / 验证数据库 / 检查返回值"等接口化描述
      - 拦截无 uiSelector 的抽象动作（"操作订单"、"进行支付流程"）
      - 拦截"页面显示正常 / 符合预期"这类不可验证的视觉断言
      → 命中即写入 lintIssues，可自动修复（把 api_call 步骤删除）

闸 2  ReviewQualityChecker 打分（0~100）
      起步 100；每条 lintIssue −8（最多 −40）
      无 uiSelector 的步骤每条 −10
      expectedResults 为空 → 直接丢弃（这是硬门槛，不是扣分）
      coverageRefs 全空 −10
      步骤数 > 15 −10
      < 40 分 → 丢弃

闸 3  去重
      标题 + 首步骤的归一化编辑距离 > 0.85 视为重复，保留 qualityScore 高的
```

被丢弃的用例要在生成统计里体现：`{generated, kept, dropped, dropReasons:{...}}`。

#### 6.1.5 流式输出（SSE）

`GET /api/projects/{pid}/generate/stream`，事件类型固定：

| 事件 | data | 说明 |
|---|---|---|
| `started` | `{"message":"已接受生成任务"}` | 立即发，避免界面空转 |
| `progress` | `{"message":"正在分析表单 ...","current":3,"total":6}` | |
| `case` | `{"testCase":{...}}` | 每产出一条推一条，前端追加到列表 |
| `complete` | `{"generated":14,"kept":12,"dropped":2}` | |
| `cancelled` | `{"message":"已取消"}` | 与 error 区分 |
| `error` | `{"message":"..."}` | |

前端用原生 `EventSource`；`error` 事件若 `e.data` 为空，视为连接层断开，降级为轮询 3 次。

#### 6.1.6 覆盖自检与补齐

生成结束后，用覆盖矩阵反查缺口：
- 有 verified transition 但没有对应用例 → 记入 `coverageGaps`
- 有端点但无任何用例引用 → 记入 `coverageGaps`

规则模式下自动补一轮（最多 2 轮）；LLM 模式下把 gaps 作为下一轮提示词输入。

### 6.2 M2 用例管理

#### 6.2.1 列表

- 分页默认 20，`page` 从 1 开始
- 筛选：`module`（下拉，取实际存在的值）、`type`、`priority`、`executionStatus`、`keyword`（标题模糊）
- 支持排序：`projectSeq` 升序 / `qualityScore` 降序 / `createdAt` 降序
- 每行展示：序号、标题、模块、类型、优先级、质量分（颜色分级）、执行状态、操作按钮

#### 6.2.2 编辑

- 表单编辑：标题/模块/类型/优先级/前置条件（多行文本，一行一条）/步骤/预期结果
- **结构化步骤编辑器**（核心交互）：每行一个步骤，字段 `type`（下拉）、`action`、`target`、`uiSelector.type`、`uiSelector.value`、`expected`、`inputValue`；支持上下移动、删除、插入
- 保存时：
  1. 校验 `structuredSteps` 非空（否则 400）
  2. 落一条 `test_case_versions`（version = 上一个 + 1）
  3. 重跑质量闸，更新 `qualityScore` / `lintIssues`
  4. 更新 `updatedAt`

#### 6.2.3 版本与回滚

- 每次 PUT 都产版本（含内容未变的幂等更新也要产，实现简单）
- 回滚：把指定版本快照写回用例，并**再产一个新版本**（不回退版本号）

#### 6.2.4 导入导出

**CSV 导出**列固定：
```
序号,标题,模块,类型,优先级,前置条件,步骤,预期结果,质量分,执行状态
```
多值字段用 ` | ` 分隔。

**CSV 导入**：按表头映射，缺列报 400 并指出缺哪列；导入后 `source=import`。

**XMind 导入**：解析 `.xmind`（zip，内含 `content.json`）。兼容 `children` 是数组或 `{attached:[...]}` 两种结构。层级约定：`模块 → 类型 → 用例标题 → 前置/步骤/预期`。

#### 6.2.5 执行状态回写

执行器只通过 `PUT .../execution-status` 改状态，不直接改用例正文。手工改状态也走同一接口。

### 6.3 M3 自动执行

#### 6.3.1 整体流程

```
1. 取用例快照（深拷贝）→ 写 execution_record(status=running)
2. 创建 Playwright context（record_video_dir=outputs/{eid}，viewport 1440x900）
3. 逐步骤执行，每步前后截图
4. 结束：保存视频 → 更新 record（status/duration/errorCategory/passedSteps）
5. 回写用例 executionStatus（可关）
```

异常必须分类，不能一律 `failed / 未知错误`。

#### 6.3.2 `structuredSteps` 契约

```json
{
  "order": 1,
  "type": "ui_action",
  "action": "点击支付按钮",
  "target": "支付按钮",
  "uiSelector": {"type": "text", "value": "支付"},
  "expected": "",
  "inputValue": ""
}
```

`type` 枚举与行为：

| type | 行为 |
|---|---|
| `ui_action` | 点击。优先用 `uiSelector`；`uiSelector.type == "route"` 时视为导航 |
| `input` | 填值。必须带 `uiSelector.value` 与 `inputValue`，缺任一 → 该步 `skipped` + 记 `CASE_INVALID` |
| `state_assert` | 断言。走三层断言（6.3.4） |

> 历史教训：不要允许 `api_call`。执行器对它的处理只能是 skip，既然如此，生成侧就不该产出它。

#### 6.3.3 `uiSelector` 的 type 与 Playwright 映射（必须逐条实现）

| type | 映射 |
|---|---|
| `route` | `page.goto(base + value)` |
| `placeholder` | `[placeholder="..."]` ← **不要用 `text=`，永远命不中 input** |
| `text` | `button:has-text("X"), a:has-text("X"), [role="button"]:has-text("X"), input[value="X"], summary:has-text("X")` 按此顺序取第一个可见的 |
| `label` | `label:has-text("X") >> xpath=following::input[1]` |
| `role` | `[role="X"]` 或 `page.get_by_role` |
| `css` / `xpath` | 原样使用 |
| `testid` | `[data-testid="X"]` |

**兜底链（必须实现，按顺序尝试）**：
1. `uiSelector` 精确匹配（可见 + 可交互）
2. 用 `target` 文本在 `button/a/[role=button]` 里找
3. 用 `target` 文本在整页找（排除 `option`、`script`、`style`）
4. **忽略空白的 XPath 兜底**：`//button[normalize-space(.)="支付"]`（
   Element Plus 的按钮会把两字文案渲染成"登 录"，这是必须处理的真实情况）
5. 全部失败 → 该步 `failed`，`error_category = SELECTOR_NOT_FOUND`，记录尝试过的选择器链

**点击后稳定性**：点击后 `await page.wait_for_timeout(300)` 再截图；若该步 `expected` 里含跳转语义，额外 `wait_for_url` 最多 3s。

#### 6.3.4 三层断言（这是"不假绿"的落地点）

```
第 1 层  URL 断言
  触发条件：expected 含 "URL"/"跳转"/"路由"/"页面地址"，或含形如 /xxx 的路径
  判定：page.url() 包含目标路径 → passed；不含 → 继续第 2 层（可能是 SPA 内部渲染）

第 2 层  DOM 文本断言
  从 expected 里抽取引号锚点：「X」/ "X" / 'X'
  逐个在 body.innerText 里查找；全部命中 → passed
  一个锚点都没有 → 跳到第 3 层（不要拿整句去模糊匹配，必然假绿）

第 3 层  诚实 skipped
  返回 (False, "skipped", "无可验证的断言锚点")
  ⚠️ 关键：绝不因为"没抛异常"就判 passed
```

`state_assert` 步骤的最终 result 是 `passed` / `failed` / `skipped` 三态之一。`skipped` **不算通过**，在报告里单独统计。

#### 6.3.5 双模式

| 模式 | 定位策略 |
|---|---|
| `programmatic`（默认） | 只用 `uiSelector` + 兜底链，零 LLM |
| `agent`（可选） | 有 `uiSelector` 仍走 DOM（更准）；无 `uiSelector` 时截图 → LLM 视觉定位 → 返回坐标或选择器；LLM 不可用则退回 `programmatic` |

#### 6.3.6 失败分类（`error_category`）

```
SELECTOR_NOT_FOUND   元素没找到（附尝试链）
ASSERT_FAILED        断言不通过（附期望 / 实际文本片段）
NAVIGATION_FAILED    页面打不开或 4xx/5xx
TIMEOUT              步骤超时（默认 10s）
CASE_INVALID         用例本身缺字段（如 input 缺 inputValue）
ENV_ERROR            浏览器启动失败等环境问题
```

每类在前端用不同颜色标签，报告里分组统计。

#### 6.3.7 批次

- 单批上限 **100** 条，超出返回 400
- 批次串行执行（并发会互相抢浏览器资源，且录屏难对齐）
- `GET /api/batches/{bid}` 返回：

```json
{
  "batchId": "B-xxx",
  "total": 12, "passed": 11, "failed": 1, "skipped": 0,
  "running": false,
  "durationMs": 182340,
  "items": [{"executionId":"EX-1","testCaseId":"TC-1","title":"...","status":"passed","durationMs":12000}]
}
```

- 取消：置标记，执行器在每个步骤开始前检查

#### 6.3.8 产物目录

```
outputs/{execution_id}/
├── step-01-before.png
├── step-01-after.png
├── ...
└── video.webm
```

报告：`outputs/{batch_id}/report.html`（截图用相对路径引用，不要 base64，体积会爆）。

### 6.4 M4 脑图

#### 6.4.1 树结构（固定四层）

```json
{
  "id": "root",
  "title": "订单系统 · 测试用例集（12 条）",
  "children": [
    {"id": "module-order", "title": "订单（12）", "children": [
      {"id": "type-positive", "title": "正例（7）", "children": [
        {"id": "case-TC-1", "title": "TC-1 使用合法信息创建订单", "children": [
          {"id": "pre-TC-1", "title": "前置条件：已登录"},
          {"id": "steps-TC-1", "title": "步骤：3 步", "children": [{"id":"s-TC-1-1","title":"1. 打开 /orders/create"}]},
          {"id": "exp-TC-1", "title": "预期：提示「订单创建成功」"}
        ]}
      ]}
    ]}
  ]
}
```

**id 前缀是前端契约**（用于配色和展开状态）：`module-*` / `type-*` / `case-*` / `pre-*` / `steps-*` / `exp-*` / `s-*`。擅自改前缀会导致节点没颜色。

#### 6.4.2 预览接口

`GET /api/projects/{pid}/mindmap/preview` 返回：

```json
{"tree": {...}, "statistics": {"modules": 1, "types": 4, "cases": 12, "leaves": 82}}
```

前端用 ECharts `tree` 渲染（`layout: 'orthogonal'`，`expandAndCollapse: true`）。

#### 6.4.3 导出

- **XMind**：手写 zip，含 `content.json`（`[{"title":"root","children":{"attached":[...]}}]`）+ `metadata.json` + `manifest.json`。这是 XMind ZEN 的最小可打开结构。
- **Markdown**：markmap 兼容的缩进列表。

### 6.5 M5 执行结果

#### 6.5.1 执行历史

- 分页列表，字段：执行 ID、用例标题、状态、模式、耗时、开始时间、批次
- 顶部统计卡：总数 / 通过 / 失败 / 通过率
- 趋势图：最近 14 天每日通过与失败数（ECharts 柱线组合）
- 筛选：status、batchId、testCaseId

#### 6.5.2 执行详情页

三段式布局：

1. **概览**：状态徽标、耗时、模式、目标 URL、错误分类与消息
2. **录屏**：`<video :src="/api/executions/{eid}/video" controls>`（若存在）
3. **步骤时间线**：每步一行，展开后左右并排展示 before/after 截图，标注选择器、耗时、错误

截图点击可放大（Element Plus `el-image` 的 `preview-src-list`）。

#### 6.5.3 报告

Jinja2 模板，**单文件自包含 HTML**（无 CDN 依赖），内容：

```
头部：项目名、批次、时间、总通过率（大字号）
统计条：总数 / 通过 / 失败 / skipped / 平均耗时 / 失败分类分布
用例明细：每条折叠面板 → 步骤表 + 截图缩略图（相对路径）+ 错误
覆盖矩阵：接口 / 状态转换 / 规则 三块进度条 + 未覆盖清单
页脚：生成时间、版本
```

#### 6.5.4 覆盖率两个口径（必须都实现）

| 口径 | 定义 |
|---|---|
| 规划覆盖 | 存在引用该目标（端点/转换）的用例，不管有没有跑 |
| 执行覆盖 | **有执行记录且该次执行 passed**，才算覆盖 |

> 这是本项目最容易被做错的地方：如果失败也算覆盖，就会出现"通过率 33%、覆盖率 100%"的自相矛盾。实现时执行覆盖的 SQL 必须带 `status='passed'`。

#### 6.5.5 覆盖矩阵响应

```json
{
  "scoped": {"endpoints": 5, "transitions": 5, "rules": 10},
  "apiEndpoint": {"total": 5, "plannedCovered": 4, "executedCovered": 3, "plannedRate": 0.8, "executedRate": 0.6, "uncovered": [{"method":"DELETE","path":"/api/orders/{id}"}]},
  "stateTransition": {"total": 5, "plannedCovered": 5, "executedCovered": 5, "plannedRate": 1.0, "executedRate": 1.0},
  "businessRule": {"total": 10, "plannedCovered": 2, "executedCovered": 2, "plannedRate": 0.2, "executedRate": 0.2},
  "stateMachines": [
    {"id":"sm-1","name":"OrderStatus","states":[{"name":"CREATED","label":"待支付"}],
     "transitions":[{"from":"CREATED","to":"PAID","trigger":"支付","verified":true,"planned":true,"plannedCaseIds":["TC-3"],"executed":true,"executedCaseIds":["TC-3"]}]}
  ]
}
```

`rate` 一律 0~1 小数，**不要混用百分比**（前端会 `* 100`，混用就出 8000%）。

---

## 7. 前端页面规格

### 7.1 路由

| 路径 | 页面 | 说明 |
|---|---|---|
| `/` | 重定向 `/projects` | |
| `/projects` | 项目列表 | 卡片式，含新建按钮、空状态引导 |
| `/projects/:id` | 项目详情 | 三个 tab：概览 / 代码分析 / 生成 |
| `/projects/:id/testcases` | 用例列表 | 主工作台 |
| `/projects/:id/mindmap` | 脑图预览 | |
| `/projects/:id/executions` | 执行历史 | |
| `/executions/:eid` | 执行详情 | |
| `/batches/:bid` | 批次结果 | |
| `/settings` | 设置 | |

### 7.2 项目详情页

- **概览 tab**：技术栈标签、源码路径、PRD 状态、分析状态、数字卡（端点/页面/状态机/用例）
- **代码分析 tab**：触发分析按钮 + SSE 进度条；结果分区展示「端点表格 / 实体 / 状态机图 / 表单 / 选择器 / 文案」；状态机用 ECharts `graph` 或手写 SVG 画有向图（verified 实线、unverified 虚线）
- **生成 tab**：模式选择（规则/LLM）、模块与类型多选、生成按钮 + 实时流（进度条 + 增量用例卡片）、结束展示统计（生成/保留/丢弃 + 丢弃原因）

### 7.3 用例列表页（最复杂的一页）

- 左侧筛选区，右侧表格
- 顶部四张覆盖卡：接口覆盖 / 状态转换覆盖 / 规则覆盖 / 执行通过率（各带进度环）
- 表格支持多选、批量删除、批量执行
- 行内「执行」按钮：点击后弹出目标 URL 输入（默认取项目的 `targetUrl`）→ 执行 → 行内状态实时变
- 「编辑」打开右侧抽屉（`el-drawer`，宽 720），内含结构化步骤编辑器
- 「版本」打开对话框，列表 + 回滚按钮
- 导入导出按钮在工具栏

> 前端读取列表时**同时兼容** `data.items` 与 `data.testCases`（后端两者都返回）。这不是冗余，是为了避免不同页面各读一个键导致白屏。

### 7.4 执行历史页

统计卡 + 趋势图 + 表格。点击行跳详情。批次 ID 可点，跳批次页。

### 7.5 执行详情页

概览卡 + 录屏 + 时间线（见 6.5.2）。失败步骤默认展开。

---

## 8. 非功能要求

| 项 | 要求 |
|---|---|
| 启动 | `uvicorn app.main:app` 单命令可用；启动时自动 `create_all` |
| 演示靶站 | `python scripts/demo_site/serve.py` 单命令可用，端口 8200 |
| 前端 | `npm i && npm run dev` 可用；`npm run build` 产物能被后端挂载为静态站点 |
| 日志 | 结构化：`时间 级别 模块 消息`，执行链路带 `executionId` |
| 异常 | 全局 handler 捕获，返回统一信封；不向客户端泄漏堆栈 |
| 安全 | 证据文件接口必须做路径穿越校验（`realpath` 必须在 `outputs/` 下） |
| 性能 | 单条用例执行 < 60s；100 条用例的列表查询 < 200ms（SQLite） |
| 隔离 | 测试通过环境变量注入 `DATABASE_URL` 指向临时库，绝不污染开发库 |

---

## 9. 里程碑与验收

每个里程碑结束都必须是**可运行状态**，不允许"半成品攒到最后一起联调"。

### M0 骨架（0.5 天）
- [ ] 目录结构、config、database、统一响应、全局异常
- [ ] `GET /api/health` 返回 `{"code":0,...}`
- [ ] 前端空壳能跑起来，能调通 health
- **验收**：`curl /api/health` 返回 code=0；浏览器打开首页不报错

### M1 项目 + 分析（1 天）
- [ ] 项目 CRUD、PRD 上传
- [ ] 静态分析能对 `scripts/sample_src` 产出结果
- [ ] 状态机 >= 1 个，转换 >= 4 条，全部 `verified=true`
- **验收**：`pytest tests/test_analyzer.py tests/test_state_machine.py` 全绿

### M2 生成 + 管理（1.5 天）
- [ ] 规则模式生成 >= 12 条，`pytest` 覆盖四类用例都存在
- [ ] 三道质量闸生效（有测试证明"无预期结果的用例被丢弃"）
- [ ] 用例列表/编辑/版本/导出/导入全部可用
- [ ] SSE 流式输出在前端可见
- **验收**：`pytest tests/test_generator.py tests/test_api_testcases.py` 全绿

### M3 执行（1.5 天）
- [ ] 内置靶站可跑
- [ ] 单条执行产出截图 + 录屏 + 步骤记录
- [ ] 三层断言生效（有单测：URL 不匹配但文本匹配 → passed；无锚点 → skipped）
- [ ] 12 条用例执行通过率 >= 90%
- **验收**：`python scripts/e2e.py` 主链路全绿

### M4 脑图 + 结果（1 天）
- [ ] 脑图树四层结构正确，统计数对得上
- [ ] XMind 能被 XMind 打开
- [ ] 执行历史/详情/批次/HTML 报告可用
- [ ] 覆盖矩阵自洽（通过率低时执行覆盖不能是 100%）
- **验收**：`python scripts/e2e.py` 含报告与覆盖率断言；人工打开报告确认

### M5 收口（0.5 天）
- [ ] README（含架构图、启动步骤、截图）
- [ ] `.env.example`、`requirements.txt`、`migrations/mysql/001_init.sql`
- [ ] 全量 `pytest` 通过
- **验收**：全新目录 clone → 按 README 三步启动 → e2e 通过

---

## 10. 禁止事项与已知坑

### 10.1 禁止

1. 不要为了让"执行通过率"好看，把断言改成"只要没异常就算通过"。
2. 不要在路由层写业务逻辑；路由只做参数校验和调用 service。
3. 不要用 `text=` 选择器定位输入框。
4. 不要用 `%` 和 `0~1` 混存覆盖率。
5. 不要在生成侧产出 `api_call` 步骤。
6. 不要把截图 base64 内嵌进报告。
7. 不要在测试里连开发库。
8. 不要在没有 `setStatus` 证据的情况下把状态转换标 `verified`。

### 10.2 已知坑（会真实遇到，提前避开）

| 现象 | 根因 | 解法 |
|---|---|---|
| 端点路径变成 `/api/orders/api/orders` | 类级 `@RequestMapping` 被当成端点 | 先定位类声明位置，注解在类前的只作前缀 |
| Vue 路由一条都解析不出来 | `path: '...'` 被 `meta:{...}` 里的 `{}` 打断 | 先找 `path:`，向左找 `{`，括号配平 |
| 一个表单被解析出 5 份 | `<el-form-item>` 匹配到了 `<el-form` | 正则加 `(?![-\w])` |
| 按钮"登 录"点不到 | Element Plus 两字按钮中间插了空格 | 兜底 XPath 用 `normalize-space(.)` |
| 点完按钮断言过了但页面没跳 | 断言只匹配了当前页已有的文案 | 三层断言 + 跳转类预期必须校验 URL |
| 执行历史 0 条 | 列表接口没返回 `items` | 统一分页结构 |
| 覆盖矩阵报"执行 100%"但用例全失败 | 执行覆盖没过滤 `passed` | SQL 加 `status='passed'` |
| 重启后数据"丢"了 | SQLite 相对路径随工作目录变 | `DATABASE_URL` 用绝对路径或固定 `data/` |
| Playwright 报浏览器不存在 | 没装驱动 | README 里写明 `playwright install chromium` |

---

## 附录 A 内置演示靶站规格（必须照做）

`scripts/demo_site/serve.py`：FastAPI 静态服务 + **SPA 路由回退**（`/orders/create` → `orders.html`，`/orders/list` → `list.html`，未知 → `index.html`）。端口 8200。

三个页面：

| 路由 | 内容 |
|---|---|
| `/` | 首页，两个入口链接 |
| `/orders/create` | 表单：收货人（必填，max 20）、金额（必填，数字）、备注（选填，max 200）；提交按钮「提交订单」 |
| `/orders/list` | 表格：订单号、收货人、金额、状态；行内按钮：支付 / 发货 / 完成 / 取消 |

**状态约束（与用例的前置推进严格对齐）**：

| 操作 | 允许的当前状态 | 成功后 |
|---|---|---|
| 支付 | 待支付 | 已支付 |
| 发货 | 已支付 | 已发货 |
| 完成 | 已发货 | 已完成 |
| 取消 | 待支付 | 已取消 |

不满足前置时报错提示，例如「当前状态不可支付」。

**关键行为（不实现会导致大批用例失败）**：

1. `submitOrder()` 校验通过后 → 写 `sessionStorage('newOrder')` → **500ms 后 `location.href='/orders/list'`**
2. 列表页初始化时读 `sessionStorage`，把新订单**插到第一行**，然后清除
3. 操作成功后更新该行状态文案，并显示成功提示（文案必须出现在页面文本里，供断言锚定）
4. 初始数据至少 4 行，覆盖 待支付/已支付/已发货/已完成 四个状态（这样每个流转用例都能找到起点）

---

## 附录 B 交付清单

```
[x] 完整源码（后端 + 前端），npm run build 产物可被后端挂载
[x] requirements.txt / requirements-dev.txt
[x] .env.example（含 LLM_* 与 DATABASE_URL）
[x] migrations/mysql/001_init.sql（由 ORM 生成，InnoDB + utf8mb4）
[x] scripts/sample_src/（Spring Boot + Vue 样本，供分析演示）
[x] scripts/demo_site/（靶站，供执行演示）
[x] scripts/e2e.py（端到端自检，输出 PASS/FAIL 明细）
[x] tests/（分析 / 状态机 / 质量闸 / 生成 / 断言 / API 契约）
[x] README.md（架构图、三步启动、截图）
[x] 示例产物：一份 report.html + 若干截图（可提交到 outputs/sample/）
```

---

**开始实现前，先复述你理解的范围与里程碑顺序，确认后再动手。**
