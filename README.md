# AICase Core

<p align="center">
  <strong>把源码事实和需求文档，变成可执行的浏览器测试。</strong><br/>
  Source-aware test generation · Multi-agent review · Evidence-based execution
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white" />
  <img alt="Vue" src="https://img.shields.io/badge/Vue-3-42B883?logo=vuedotjs&logoColor=white" />
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-Chromium-2EAD33?logo=playwright&logoColor=white" />
  <img alt="Tests" src="https://img.shields.io/badge/tests-138%20passed-brightgreen" />
</p>

<p align="center">
  <a href="#核心能力">核心能力</a> ·
  <a href="#架构">架构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#配置">配置</a> ·
  <a href="#使用">使用</a> ·
  <a href="#api">API</a> ·
  <a href="#设计原则">设计原则</a>
</p>

---

## 这是什么

AICase Core 是一个面向 Web 应用的 AI 测试平台：输入一个本地 Spring Boot + Vue 项目路径和一期需求文档，平台会自动完成测试点设计、可执行用例生成、独立审核、真实浏览器执行、截图审核、结果分析与报告输出。

它解决的不是“让模型写几条测试用例”，而是测试落地过程中更难的几件事：

- **用例要能跑**：生成结果必须落到真实路由、字段、placeholder 和按钮文案上，而不是看起来合理的自然语言。
- **结果要有证据**：每次执行都有逐步截图、录屏、步骤状态和错误信息，不靠一句“测试通过”。
- **版本要能隔离**：需求会变、模型会换、规则/LLM 会交替使用；每次生成都必须是一个可回看的独立版本。
- **失败要可解释**：区分产品缺陷、脚本问题、数据问题、环境问题和证据不足，而不是把所有失败都归为“用例失败”。

平台默认面向 Java Spring Boot + Vue 3，执行层使用 Playwright Chromium，LLM 是可选的增强能力，不配置也能以纯规则模式完整运行。

---

## 核心能力

### 01 · 源码感知的测试设计

静态分析提取后端接口、实体字段、校验约束、枚举、业务规则，以及前端页面、路由、表单、placeholder、UI 文案和可定位选择器。这些事实会注入测试点与用例生成，降低模型编造页面元素和输入字段的概率。

### 02 · 可执行的结构化用例

用例不是一段描述，而是可被执行器消费的结构化步骤：

```json
{
  "type": "input",
  "action": "输入收货人",
  "target": "收货人",
  "uiSelector": { "type": "placeholder", "value": "请输入收货人" },
  "inputValue": "张三"
}
```

生成阶段强制首步导航、单输入单主断言、必填字段补齐，并经过复合场景拦截、UI 语言检查、质量评分和相似度去重。

### 03 · 多 Agent 审核与修订

测试点生成、用例生成、独立审核、结果分析拆成 4 个逻辑角色。审核采用“确定性校验 + LLM 建议”：

- 确定性规则负责可执行性、事实一致性、复合场景和多输入；
- LLM 负责语义建议、覆盖缺口和修订补丁；
- 只有 `pass` 且 `automated` 的用例才进入 Pipeline 执行；
- 修订最多 2 轮，超过上限转 `reject`，不静默丢弃。

### 04 · 真实浏览器执行与证据链

Playwright 逐步执行用例，支持多级定位兜底、URL/文案/状态断言、截图、WebM 录屏和步骤级错误记录。执行状态由真实结果落库，失败不会伪造通过。

### 05 · 截图审核与证据驱动分析

可选多模态 LLM 对关键截图进行复核，输出结论、置信度、逐图问题和红框坐标。结果分析 Agent 读取执行状态、步骤、错误码、截图审核结论和用例预期，输出失败分类与建议；它只写自己的分析表，**绝不覆盖真实执行状态**。

### 06 · 生成版本隔离

每次成功生成都会保存独立快照。实时用例只保留最新一次，历史版本按时间倒序回看、只读对比；规则生成和 LLM 生成不会混在同一个列表里。旧数据首次查看时自动归档为「历史归档」，不把多次生成伪装成一次。

### 07 · 多 LLM 与模型适配

文本 LLM 与多模态 VLM 分开配置，可维护多套 profile，并按测试点/用例生成分别选择。客户端兼容不同模型的响应差异：

- `content` 为空时接受 `reasoning_content` 中的合法 JSON；
- GLM-5.3 / 5.2 自动降低推理强度，避免思考过程吃满 token；
- `finish_reason=length` 时提升 token 预算重试；
- 供应商不支持扩展参数时自动去掉重试；
- 只有重试后仍无可用结果才降级规则。

### 08 · 一键 Pipeline

独立侧边栏「Pipeline 执行」从代码分析一路跑到执行报告：

```
代码分析 → 测试点 → 用例生成 → 审核/修订 → 自动执行 → 结果分析 → 报告
```

支持快速创建项目、上传需求文档、选择测试点 LLM 和用例 LLM；缺少代码分析时自动补跑。

---

## 架构

```mermaid
flowchart LR
  subgraph Input["输入"]
    R[需求版本 / PRD]
    S[源码目录]
  end

  subgraph Deterministic["确定性内核"]
    A[静态分析]
    Q[质量闸]
    X[Playwright 执行器]
  end

  subgraph Agents["LLM Agents"]
    T[测试点生成]
    G[用例生成]
    V[独立审核 / 修订]
    D[结果分析]
  end

  R --> T
  S --> A
  A -->|代码事实| T
  A -->|代码事实| G
  T --> G
  G --> Q
  Q --> V
  V -->|pass + automated| X
  X -->|截图 / 录屏 / 步骤| D
  D --> P[报告 / 覆盖矩阵 / 脑图]
```

**确定性 / 不确定性分流**

| 层 | 职责 | 特性 |
|---|---|---|
| 确定性内核 | 静态分析、状态机、质量闸、数量改写、执行器、覆盖统计 | 可复现、可审计、无模型依赖 |
| LLM Agents | 测试点、用例参数、审核建议、截图语义、结果分析 | 可选、可降级、输出必须经过结构校验 |

平台不会让模型直接决定“执行是否通过”。执行状态来自真实浏览器结果，模型只能解释证据。

---

## 快速开始

### 环境要求

- Python 3.11+（当前环境 Python 3.14）
- Node.js 18+
- SQLite（默认）或 MySQL 8+
- Playwright Chromium（执行用例时需要）

### 1. 安装

```bash
git clone <repository-url> AICase
cd AICase

python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium

cd frontend
npm install
cd ..
```

### 2. 配置

```bash
# Windows
copy .env.example .env
# Linux / macOS
# cp .env.example .env
```

最小可运行配置只需要数据库；不配置 LLM 时，测试点与用例走纯规则模式。

```dotenv
DATABASE_URL=sqlite:///./data/aicase.db
OUTPUT_DIR=./outputs
TARGET_URL_DEFAULT=http://127.0.0.1:8200
```

### 3. 启动

```bash
# 后端（Windows；Linux/macOS 用 .venv/bin/python）
.venv/Scripts/python.exe -m uvicorn app.main:app \
  --host 127.0.0.1 --port 8100 --reload --reload-dir app

# 前端
cd frontend
npm run dev
```

- Web UI：<http://127.0.0.1:5173>
- API 文档：<http://127.0.0.1:8100/docs>
- 健康检查：<http://127.0.0.1:8100/api/health>

Windows 也可以直接运行：

```powershell
powershell -File scripts/dev.ps1
```

### 4. 准备被测站点

| 靶站 | 端口 | 启动方式 |
|---|---|---|
| 静态演示站（默认目标） | 8200 | `.venv/Scripts/python.exe scripts/demo_site/serve.py` |
| 完整订单系统（Spring Boot + Vue） | 8080 / 5273 | 见 [`scripts/order-system/README.md`](scripts/order-system/README.md) |

使用完整订单系统时，把项目「被测地址」设为 `http://127.0.0.1:5273`。

---

## 配置

| 变量 | 说明 | 默认 |
|---|---|---|
| `DATABASE_URL` | SQLite / MySQL 连接串 | 项目根目录下 `data/aicase.db` |
| `OUTPUT_DIR` | 截图、录屏、报告输出目录 | `./outputs` |
| `TARGET_URL_DEFAULT` | 被测站点默认地址 | `http://127.0.0.1:8200` |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 文本 LLM 初始配置 | 空 = 纯规则 |
| `LLM_VISION_API_KEY` / `LLM_VISION_BASE_URL` / `LLM_VISION_MODEL` | 多模态 VLM 初始配置 | 空 = 回退文本 LLM |
| `AITEST_PROMPT_DIR` | 外置提示词目录覆盖 | `app/agent/prompts` |
| `VITE_BACKEND_TARGET` | 前端开发代理的后端地址 | `http://127.0.0.1:8100` |

首次读取设置时，`.env` 中的单套 LLM 配置会迁移成数据库默认 profile；之后以「设置」页维护的多套配置为准。

### MySQL

```sql
CREATE DATABASE aicase DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
```

```bash
mysql -u root -p aicase < migrations/mysql/001_init.sql
```

```dotenv
DATABASE_URL=mysql+pymysql://user:password@127.0.0.1:3306/aicase?charset=utf8mb4
```

DDL 当前包含 23 张表。SQLite 会在后端启动时自动建表；MySQL 建议使用 DDL 初始化，并保持字符串列排序规则一致。

---

## 使用

1. **新建项目**：填写本机源码路径和被测地址。
2. **代码分析**：提取接口、字段、约束、页面、路由与状态机。
3. **需求版本**：新建 `V1.0`，填写或上传 PRD，解析测试点。
4. **生成用例**：选择需求版本、生成模式和 LLM，按测试点生成。
5. **审核与执行**：AI 质检、单条/批量执行，查看截图、录屏和步骤证据。
6. **Pipeline**：一键跑完分析 → 测试点 → 用例 → 审核 → 执行 → 分析 → 报告。
7. **导出**：覆盖矩阵、HTML 报告、XMind / Markdown 脑图、CSV。

### 页面

| 路由 | 页面 |
|---|---|
| `/dashboard` | 总览看板 |
| `/projects` | 项目列表 |
| `/projects/:id` | 项目详情（代码分析 / 需求版本 / 用例生成 / 测试用例 / 脑图 / 执行结果） |
| `/pipeline` | Pipeline 一键执行 |
| `/testcases` | 全局用例列表 |
| `/executions` | 全局执行结果 |
| `/settings` | 多 LLM 配置 |

用例页面支持按需求版本和生成版本切换。历史版本只读；执行、编辑和 AI 质检需要切回实时用例。

---

## API

基础地址：`http://127.0.0.1:8100`。所有接口使用统一信封：

```json
{ "code": 0, "message": "ok", "data": {} }
```

完整 OpenAPI 见 `/docs`。

```bash
# 健康检查
curl http://127.0.0.1:8100/api/health

# 创建一次 Pipeline 运行
curl -X POST http://127.0.0.1:8100/api/projects/<pid>/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "requirementId": "<rid>",
    "mode": "auto",
    "autoExecute": true,
    "maxRevisions": 2,
    "maxCases": 20
  }'
```

| 分组 | 代表接口 |
|---|---|
| 项目 | `GET/POST /api/projects`、`GET/PUT/DELETE /api/projects/{pid}` |
| 分析 | `POST /api/projects/{pid}/analyze`、`GET /api/projects/{pid}/analysis` |
| 需求 | `GET/POST /api/projects/{pid}/requirements`、`POST /api/requirements/{rid}/analyze` |
| 测试点 | `GET /api/requirements/{rid}/test-points` |
| 生成 | `POST /api/requirements/{rid}/generate`、`GET /api/requirements/{rid}/generate/stream` |
| 版本 | `GET /api/requirements/{rid}/generations`、`GET /api/requirements/{rid}/generations/{gid}` |
| 用例 | `GET/POST /api/projects/{pid}/testcases`、`POST .../ai-review`、`POST .../ai-fix` |
| 执行 | `POST /api/projects/{pid}/testcases/{cid}/execute`、`GET /api/executions/{eid}` |
| 工作流 | `POST/GET /api/projects/{pid}/workflows`、`GET /api/workflows/{run_id}` |
| 报告 | `GET /api/projects/{pid}/report`、`GET /api/projects/{pid}/coverage/matrix` |
| 设置 | `GET/POST /api/settings/llm-profiles`、`POST .../test`、`POST .../default` |

用例生成使用 SSE，事件包括 `started / progress / case_draft / case / fallback / complete / error`，页面会显示批次进度、逐条候选、来源和降级原因。

---

## 项目结构

```text
AICase/
├─ app/
│  ├─ routers/            API 路由
│  ├─ service/            生成、审核、执行、报告、版本与设置
│  ├─ analyzer/           Spring / Vue 静态分析
│  ├─ agent/              Agent 与质量闸
│  │  └─ prompts/         外置提示词
│  ├─ llm/                OpenAI 兼容客户端与响应归一化
│  ├─ entity/models.py    23 张表 ORM 模型
│  ├─ workflow/           工作流契约
│  ├─ evaluation/         实验指标
│  └─ main.py             FastAPI 入口
├─ frontend/
│  └─ src/
│     ├─ views/           看板、项目、Pipeline、用例、执行、设置
│     ├─ components/      标签页与上传组件
│     ├─ api/             API 封装
│     └─ utils/           共享工具
├─ scripts/
│  ├─ demo_site/          静态演示靶站
│  ├─ order-system/       完整订单系统靶站
│  ├─ e2e.py              端到端自检
│  ├─ evaluate_*.py       对照实验
│  └─ gen_mysql_ddl.py    MySQL DDL 生成
├─ tests/                 pytest
├─ migrations/mysql/      初始化 DDL
├─ docs/                  设计与迭代文档
└─ outputs/               截图 / 录屏 / 报告
```

---

## 设计原则

**Facts over guesses.**  
模型只能引用静态分析得到的路由、字段、选择器和文案；无法建立事实依据的用例不进入执行。

**Deterministic where possible.**  
静态分析、质量闸、数量改写、执行器和覆盖统计保持纯规则；LLM 只做语义增强，不决定执行事实。

**Evidence over claims.**  
执行结果必须带步骤、截图、录屏和错误信息；没有证据的结论标记为 `unknown`，不冒充产品缺陷。

**Immutable versions.**  
每次生成都是独立快照；实时用例代表最新状态，历史版本只读，避免跨版本合并和重复。

**Honest degradation.**  
LLM 不可用、输出非法或模型能力不匹配时，显式降级到规则或确定性问题，不静默伪造成功。

---

## 测试

```bash
# 后端测试
.venv/Scripts/python.exe -m pytest -q
# 138 passed

# 前端构建
cd frontend
npm run build
```

常用脚本：

| 脚本 | 用途 |
|---|---|
| `scripts/analyze_cli.py` | 不落库，直接分析一个源码目录 |
| `scripts/e2e.py` | 临时数据库上的端到端自检 |
| `scripts/evaluate_workflow.py` | 生成 vs 生成+审核的质量对照实验 |
| `scripts/evaluate_result_analysis.py` | 结果分析分类评测 |
| `scripts/gen_mysql_ddl.py` | 从 ORM 重新生成 MySQL DDL |
| `scripts/fix_expected_results.py` | 修复历史逐字符预期结果（幂等） |

---

## 边界与约束

- 静态分析主要覆盖 Java Spring Boot + Vue 3，不是通用语言解析器。
- 当前只读取本机源码目录，不执行 Git 拉取或源码压缩包解压。
- 执行依赖被测站点可访问且已安装 Playwright Chromium；目标不可达时不自动重试。
- 平台没有用户/权限体系，默认只适合单机或可信内网。
- LLM Key 以明文保存在 `.env` / 数据库，接口只返回掩码；请限制环境访问权限。
- 共享测试数据没有通用 reset；数量断言只自动处理“创建动作 + 单个列表总数 +1”。
- 历史版本只读，不能直接从快照执行；执行前需切回实时用例。
- Pipeline 使用后台线程运行，服务重启后只回收长时间无活动的运行；当前未提供取消 API。

---

## Roadmap

- Git 仓库拉取与源码压缩包导入
- 直接从历史生成版本恢复/执行
- Pipeline 取消、暂停与断点续跑
- 执行证据接入对象存储
- 更多技术栈的静态分析器
- CI/CD 与测试报告集成

---

## Contributing

欢迎提交 Issue 和 Pull Request。提交前请确保：

1. `pytest -q` 全部通过；
2. 前端 `npm run build` 通过；
3. LLM 仍然是可选项，新功能在无 Key 时能够确定性降级；
4. 不把真实 API Key、数据库连接和运行数据提交到仓库。

---

## License

当前仓库未附带 LICENSE 文件。如需开源发布，请补充许可证后再对外分发。
