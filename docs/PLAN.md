# AICase Core 迭代开发计划

> 依据：`docs/TASK_BOOK.md`（唯一需求来源）+ `docs/VIBE_PROMPT.md`（分阶段执行方式）
> 开发方式：小步迭代，每个版本结束都是**可运行状态**，写迭代文档，等待用户审核后才进入下一迭代。
> 迭代文档位置：`docs/iterations/V{n}.md`（每个版本完成时创建）

---

## 技术栈与版本约定

**已探测本机环境**：Python 3.14.2 ｜ Node v24.14.1 ｜ npm 11.11.0（Windows / PowerShell）

### 虚拟环境（必须，已创建）

项目使用独立 venv：`d:/python_code/AITest/.venv`（Python 3.14.2，已创建）。所有后端命令都在 venv 内执行：

```powershell
# 激活（PowerShell）
.\.venv\Scripts\Activate.ps1
# 或不激活直接用解释器
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app
.\.venv\Scripts\python.exe -m pytest
```

- `.venv/` 加入 `.gitignore`，不提交
- 若 3.14 与某依赖不兼容，用 `py -3.12 -m venv .venv` 重建并在迭代文档记录
- Node 侧不建独立环境（npm 本身按项目 `node_modules` 隔离，无需 nvm）

### 后端（Python）

| 项 | 选型与版本 | 说明 |
|---|---|---|
| 语言 | Python 3.14.2（本机已装，满足 3.11+ 要求） | 全项目 `from __future__ import annotations`；若个别库与 3.14 不兼容，降级用 3.12 建虚拟环境并在迭代文档记录 |
| Web 框架 | `fastapi >= 0.115` + `uvicorn[standard] >= 0.30` | 自动生成 `/docs` |
| ORM | `sqlalchemy >= 2.0.30`（`Mapped` / `mapped_column` 风格） | 禁用老式 `Column()` |
| 数据库 | SQLite 默认；`DATABASE_URL` 可切 MySQL（`pymysql >= 1.1`） | SQLite 开 WAL + foreign_keys，路径用绝对路径 |
| 校验/配置 | `pydantic >= 2.7` + `pydantic-settings >= 2.2` | 配置从 `.env` 读 |
| 浏览器自动化 | `playwright >= 1.45`（chromium） | 需 `playwright install chromium` |
| 模板 | `jinja2 >= 3.1` | 渲染 HTML 报告 |
| 文档解析 | `pypdf >= 4.0`（PDF）+ `python-docx >= 1.1`（docx） | 仅 PRD 上传用 |
| 异步 | 标准库 `asyncio` + `asyncio.create_task` | 不引入 Celery/Redis/RabbitMQ |
| 测试 | `pytest >= 8.0` + `httpx`（FastAPI TestClient 依赖） | 测试用临时库，环境变量注入 `DATABASE_URL` |

**版本固定策略**：`requirements.txt` 写 `>=` 下限；V1 验收时用 `pip freeze` 把实测通过的组合记录到 `docs/iterations/V1.md`，后续版本如需升级必须重新全量跑测试。收口（V8）时按最终实测版本固定（`==`）。

### 前端（Node/npm）

| 项 | 选型与版本 |
|---|---|
| 框架 | `vue ^3.5`（一律 `<script setup>`） |
| 构建 | `vite ^5.4`（Node 24 兼容，选 5 系最稳） |
| UI | `element-plus ^2.8` |
| 状态 | `pinia ^2.2`（只存全局配置与当前项目） |
| 路由 | `vue-router ^4.4`（history 模式） |
| 请求 | `axios ^1.7`（统一响应拦截） |
| 图表 | `echarts ^5.5`（覆盖率、趋势、脑图 tree 渲染） |

**约定**：`package.json` 用 `^` 语义化版本；不引入 TASK_BOOK 未列出的依赖（不装 xmind 库——XMind 手写 zip；不装重型脑图库）。

### 明确不引入

Celery / Redis / RabbitMQ、Milvus 或任何向量库、用户登录/JWT/RBAC、openpyxl、重型脑图库（TASK_BOOK 2.2 OUT 清单）。

---

## 核心原则（全程有效）

1. **不假绿**：验证不了就标 `skipped`，绝不因为"没报错"就判通过。
2. **不编造**：用例里的元素/文案尽量来自静态分析事实，推断的要降 `confidence`。
3. **证据可回溯**：任何"已覆盖"都能点回执行记录、步骤、截图。
4. **确定性/不确定性分流**：静态分析、质量闸、覆盖率统计纯规则零 LLM；LLM 只用于"事实→措辞"和"视觉兜底"，且**可摘除**（不配 Key 全链路可用）。
5. **每版本真实验证**：跑验收命令贴真实输出，不用"应该可以了"代替。

---

## 版本总览

| 版本 | 名称 | 对应 TASK_BOOK | 核心产出 | 审核 |
|---|---|---|---|---|
| V1 | 后端骨架 | M0 | 目录结构 + 配置 + 数据库 + 统一响应 + health | 用户审核 |
| V2 | 数据模型 + 项目管理 | M1 前半 | 8 张表 + 项目 CRUD + PRD 上传 | 用户审核 |
| V3 | 静态分析 + 状态机 | M1 后半 | Spring/Vue 解析器 + 状态机提取 + 样本源码 | 用户审核 |
| V4 | 规则模式用例生成 | M2 生成 | 四类用例生成 + 三道质量闸 + SSE | 用户审核 |
| V5 | 用例管理 | M2 管理 | 列表/编辑/版本/导入导出 + 前端工作台 | 用户审核 |
| V6 | 演示靶站 + 自动执行 | M3 | 靶站 + Playwright 执行器 + 三层断言 + 截图录屏 | 用户审核 |
| V7 | 脑图 + 执行结果 | M4 | 脑图树/XMind 导出 + 执行历史/详情 + HTML 报告 + 覆盖矩阵 | 用户审核 |
| V8 | LLM 增强 + 收口 | M2(LLM) + M5 | LLM 生成/视觉兜底（可降级）+ README + DDL + 全量验收 | 用户审核 |

前端在 V1 搭空壳、V5 起按页面增量交付（V5 用例工作台、V6 执行相关页、V7 脑图/报告页），避免一次糊出全部页面。

---

## V1 — 后端骨架（M0）

**范围**
- 目录结构严格按 TASK_BOOK 3.3 节创建（`app/`、`frontend/`、`scripts/`、`tests/` 等）
- `app/config.py`：pydantic-settings + `@lru_cache`，含 `database_url`、`output_dir`、`llm_*`
- `app/database.py`：engine / SessionLocal / Base / `session_scope()`；SQLite 开 WAL + foreign_keys
- `app/common/response.py`：`ok(data)` / `fail(code, message)`
- `app/common/errors.py`：`BizError` + 全局异常处理器（RequestValidationError → 400）
- `app/main.py`：health、CORS、静态挂载占位，启动自动 `create_all`
- `.env.example`、`requirements.txt`、`pytest.ini`
- 前端 Vue3 + Vite + Element Plus + Pinia 空壳，能调通 `/api/health`

**验收**
```
uvicorn app.main:app 启动成功
curl http://127.0.0.1:8100/api/health → {"code":0,...}
npm run dev 打开首页不报错，能显示 health 状态
```

**产出文档**：`docs/iterations/V1.md`

---

## V2 — 数据模型 + 项目管理（M1 前半）

**范围**
- 8 张表严格按 TASK_BOOK 第 4 节：`projects` / `code_analyses` / `state_machines` / `test_cases` / `test_case_versions` / `execution_records` / `execution_steps` / `mindmaps`
  - 重点字段：`test_cases.project_seq`、`lint_issues`；`execution_records.test_case_snapshot`（强制）
- 项目 CRUD 全套接口（列表/详情/更新/删除级联/targetUrl）
- PRD 上传：`.md/.txt` 直读，`.pdf` 用 pypdf，`.docx` 用 python-docx
- tests：项目 API 契约测试（临时库，环境变量注入 `DATABASE_URL`）

**验收**
```
pytest tests/test_api_projects.py -v 全绿
curl 创建/查询/更新/删除项目往返正确；PRD 上传后能读回纯文本
```

**产出文档**：`docs/iterations/V2.md`

---

## V3 — 静态分析 + 状态机（M1 后半）

**范围**
- `analyzer/scanner.py`：文件收集 + 技术栈识别（pom.xml/build.gradle → java-spring；package.json + .vue → vue）
- `analyzer/spring.py`：端点（类级 `@RequestMapping` 只作前缀，端点必须带 file+line）/ 实体与约束 / 枚举 / 业务规则（`if(...) throw`）/ UI 文案
- `analyzer/vue.py`：路由（先找 `path:` 再括号配平，禁用 `[^{}]*`）/ 表单（`<el-form(?![-\w])`）/ 字段 / 选择器 / 文案
- `agent/state_machine_agent.py`：`setStatus` 写入点为 verified 证据、守卫条件推 from/to（含 `if(!X) throw` 排除语义）、BFS 最短推进路径
- `scripts/sample_src/`：最小但真实的 Spring Boot + Vue 样本（1 Controller、1 ServiceImpl 含 5 处 setStatus + 4 处前置 throw、OrderStatus 枚举、2 个 Vue 页面）
- 分析结果落 `code_analyses` 表，`GET /analysis` 可读回
- 项目详情页（前端）：三个 tab 骨架 + 代码分析 tab（触发分析 + 结果分区展示）

**验收**
```
pytest tests/test_analyzer.py tests/test_state_machine.py -v 全绿
端点 >= 5 且路径无重复拼接；状态机 >= 1 个；转换 >= 4 条全部 verified=true
```

**产出文档**：`docs/iterations/V3.md`

---

## V4 — 规则模式用例生成（M2 生成）

**范围**
- `agent/test_generator_agent.py` 规则模式：`positive`（必填全填合法值）/ `negative`（逐个必填留空）/ `boundary`（约束刚好越界）/ `state`（BFS 前置推进 + trigger）
- 每条用例必须含非空 `structuredSteps`（type 只允许 `ui_action/input/state_assert`，禁止 `api_call`）、`expectedResults`、`testData`、`module`
- `agent/quality_gate.py` 三道闸：UiLanguageLinter → ReviewQualityChecker 打分（无预期直接丢、<40 丢）→ 编辑距离去重（>0.85），返回 `{generated, kept, dropped, dropReasons}`
- `llm/client.py`：OpenAI 兼容协议骨架，任何异常抛 `LlmUnavailable` → 降级规则模式（本版本只做降级路径，LLM 提示词 V8 完善）
- `service/generation_service.py` 编排 + 落库
- SSE：`GET /generate/stream`，事件 `started/progress/case/complete/cancelled/error`
- 覆盖自检：生成后反查 `coverageGaps`，规则模式自动补一轮（最多 2 轮）
- 生成接口 + 取消接口

**验收**
```
pytest tests/test_generator.py tests/test_quality_gate.py -v 全绿
LLM_API_KEY 置空时真实生成 >= 12 条，四类均有分布
有测试证明"无 expectedResults 的用例被丢弃"
```

**产出文档**：`docs/iterations/V4.md`

---

## V5 — 用例管理 + 前端工作台（M2 管理）

**范围**
- 列表：分页 + 筛选（module/type/priority/executionStatus/keyword）+ 排序；响应同时含 `items` 与 `testCases`
- 编辑：校验 structuredSteps 非空 → 落版本 → 重跑质量闸 → 更新
- 版本列表 + 回滚（回滚产新版本，不回退版本号）
- 批量删除、复制到其他项目、执行状态回写接口
- CSV 导出/导入（表头固定，多值 ` | ` 分隔）；XMind 导入（zip 读 content.json，children 兼容数组与 `{attached:[]}`）
- 前端用例列表页：顶部四张覆盖卡（数据来自 `/coverage/matrix`，本版本先返回真实结构）、表格多选、批量操作、编辑抽屉（宽 720，结构化步骤编辑器：增删/上下移/type 下拉）、版本对话框、导入导出、生成 tab（SSE 实时流）

**验收**
```
pytest tests/test_api_testcases.py -v 全绿
浏览器打开用例列表页：无白屏、覆盖卡有数字、编辑抽屉可用、SSE 生成过程可见
```

**产出文档**：`docs/iterations/V5.md`

---

## V6 — 演示靶站 + 自动执行（M3）

**范围**
- `scripts/demo_site/serve.py`：FastAPI + SPA 路由回退（端口 8200），三个页面严格按附录 A：
  - 提交成功 → sessionStorage + 500ms 后跳 `/orders/list`；列表页把新订单插第一行
  - 初始 4 行覆盖四个状态；四个操作严格校验前置状态，不满足显示报错文案
- `agent/execution_agent.py`：
  - uiSelector 映射按 6.3.3 逐条（placeholder 用 `[placeholder="..."]`，禁 `text=` 定位输入框）
  - 五级兜底链，最后一级 `normalize-space` XPath
  - 三层断言：URL → 引号锚点文本 → 诚实 skipped
  - 每步前后截图；`record_video_dir` 录 WebM；失败分类 6 枚举
- 执行服务：单条同步、批量异步（上限 100、串行、可取消）、批次查询
- 执行前强制写 `test_case_snapshot`
- 证据文件接口路径穿越校验（realpath 必须在 outputs/ 下）
- `scripts/e2e.py` 第一版：生成 → 执行 → 断言证据
- 前端：行内执行、批量执行入口

**验收**
```
python scripts/e2e.py：>= 12 条用例通过率 >= 90%，
截图 >= 用例数×步骤数，每条用例 1 个 video.webm
自证不假绿：改 expected 为不存在文案 → failed；改成无引号抽象描述 → skipped
```

**产出文档**：`docs/iterations/V6.md`

---

## V7 — 脑图 + 执行结果 + 报告（M4）

**范围**
- 脑图：四层树（模块→类型→用例→前置/步骤/预期），id 前缀严格 `module-*/type-*/case-*/pre-*/steps-*/exp-*/s-*`，statistics 含 modules/types/cases/leaves
- XMind 导出（手写 zip：content.json + metadata.json + manifest.json）、Markdown 导出（markmap 兼容）
- 执行历史：分页筛选 + 顶部统计卡 + 14 天趋势（ECharts）
- 执行详情页：概览 + `<video>` 录屏 + 步骤时间线（before/after 并排可放大，失败步骤默认展开）
- 批次聚合接口 + 批次页
- HTML 报告：Jinja2 单文件自包含、截图相对路径引用、头部通过率大字 + 失败分类分布 + 覆盖矩阵
- 覆盖矩阵 6.5.5：规划覆盖 vs 执行覆盖两口径，**执行覆盖必须过滤 `status='passed'`**，rate 一律 0~1
- 前端：脑图页（ECharts tree）、执行历史页、执行详情页、批次页、设置页（LLM 配置掩码 + 连通性测试）

**验收**
```
pytest -q 全量通过
python scripts/e2e.py 含报告与覆盖率断言
故意让一条用例失败 → 执行覆盖率下降（自洽性验证）
XMind 导出文件能被 XMind 打开；report.html 打开排版正常
```

**产出文档**：`docs/iterations/V7.md`

---

## V8 — LLM 增强 + 收口（M2 LLM + M5）

**范围**
- LLM 生成模式：上下文约束提示词（只允许真实元素/文案、禁 api_call、首步必须是打开页面）、严格 JSON + Pydantic 校验、失败重试 1 次后跳过、覆盖缺口作为下轮输入
- Agent 执行模式：无 uiSelector 时截图 → 视觉定位；LLM 不可用退回 programmatic
- `migrations/mysql/001_init.sql`：从 ORM 元数据生成，InnoDB + utf8mb4
- requirements 固定版本、`npm run build` 产物由后端挂载
- README：定位、架构图、三步启动、五模块说明、LLM 局限诚实声明
- 全量验收：全新环境安装 → 三步启动 → pytest + e2e 全绿

**验收**
```
pytest 全量通过；e2e 通过
把 LLM Key 配上再摘掉，两种情况下规则链路产出完全一致
README 三步启动实测可行
```

**产出文档**：`docs/iterations/V8.md`

---

## 迭代文档模板（每个版本完成时按此写 `docs/iterations/V{n}.md`）

```markdown
# V{n} 迭代记录 — {名称}

## 本版目标
（一段话，来自 PLAN.md）

## 实现内容
- 模块/文件清单（新增/修改）
- 关键设计决策（决策：... 理由：...）

## 真实验证输出
（粘贴验收命令的真实运行结果，不概括）

## 已知问题 / 未覆盖
（诚实列出）

## 下一步建议
（下一迭代范围提醒）

## 待用户审核确认的点
（需要用户拍板的决策，逐条列出）
```

---

## 工作约定

- 一次只做一个版本，完成后停，等用户审核 `docs/iterations/V{n}.md` 再继续。
- 遇到 TASK_BOOK 未写清的细节：选最简单方案，代码注释写"决策：... 理由：..."，并在迭代文档的"待审核确认的点"里列出。
- 不引入 TASK_BOOK 之外的依赖（不用 Celery/Redis/Milvus/向量库，不做登录 RBAC）。
- 文件 > 400 行要拆分；每模块顺手补测试，测试不依赖 LLM Key，用临时库隔离。
- 纠偏时参照 `VIBE_PROMPT.md` 第三节的纠偏提示词。
