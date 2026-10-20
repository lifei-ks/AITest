# Vibe Coding 提示词集

> 用法：把 `TASK_BOOK.md` 放到项目根目录，然后按下面顺序发提示词。
> **不要一次把所有提示词发完**——AI 会为了"看起来很完整"而糊出大量空壳代码。
> 每个阶段跑完验收，再发下一个阶段的提示词。

---

## 第 0 步：准备工作（你手动做）

```bash
mkdir aicase-core && cd aicase-core
# 把 TASK_BOOK.md 放到这个目录下
```

然后打开 AI 编码工具（Claude Code / Cursor / Codex / WorkBuddy 均可），把下面「主提示词」整段发出去。

---

## 一、主提示词（每次新会话开头都发这段）

```text
你是一位有 10 年经验的测试平台架构师 + Python 全栈工程师。现在要在当前目录
从零实现一个项目，需求规格在同目录的 TASK_BOOK.md 里。

【第一条指令】
先完整读 TASK_BOOK.md，然后只做三件事，不要写任何代码：
1. 用你自己的话复述：这个项目解决什么问题、五个模块分别做什么、哪些明确不做。
2. 指出 TASK_BOOK.md 里你认为有歧义或可能实现不了的地方（最多 5 条），每条给出你的处理建议。
3. 给出你打算的里程碑执行顺序，以及每个里程碑结束时「怎么验证它真的能跑」。

等我确认后你再开始写代码。

【工作方式（整个过程都要遵守）】
- 按里程碑推进，一次只做一个里程碑。每个里程碑结束必须能启动、能跑通，
  不允许「先写一堆空壳，最后一起联调」。
- 每个里程碑结束时，主动跑一次验证命令，把真实输出贴出来。跑不过就继续修，
  不要用「应该可以了」代替实际运行结果。
- 代码量大的文件（>400 行）要拆分，不要写成一个巨型文件。
- 每写完一个模块，顺手补对应的测试，测试要能在没配 LLM Key 的情况下跑通。
- 遇到需求没写清楚的地方，选最简单的方案实现，并在代码注释里用
  「决策：... 理由：...」记下来，不要停下来反复问我。
- 不要引入 TASK_BOOK.md 里没有的依赖，特别是：不要用 Celery/RabbitMQ/Redis
  做任务队列（用 asyncio 就够了）、不要用 Milvus 或任何向量库、不要加用户登录。

【三条铁律（违反任何一条都算实现错误）】
1. 不假绿：验证不了就标 skipped，绝不因为「没抛异常」判 passed。
2. 不编造：用例里的页面元素和文案，优先来自静态分析结果；推断出来的要降 confidence。
3. 证据可回溯：任何「已覆盖」都要能点回具体的执行记录和截图。

【技术硬约束】
- 后端 FastAPI + SQLAlchemy 2.0（Mapped/mapped_column 风格）+ SQLite（可切 MySQL）
- 前端 Vue 3 <script setup> + Element Plus + Vite + Pinia + ECharts
- 所有接口返回 {code, message, data}；成功 code=0
- 静态分析、质量校验、覆盖率统计必须是纯规则，零 LLM 依赖
- LLM 只用于「事实 → 用例措辞」和「视觉兜底定位」，且必须可降级

现在开始：读 TASK_BOOK.md，给我复述 + 歧义清单 + 里程碑顺序。
```

---

## 二、分阶段提示词（确认主提示词后逐条发）

### P0 骨架

```text
执行里程碑 M0。只做骨架，不要写业务逻辑。

范围：
1. 目录结构严格按 TASK_BOOK.md 第 3.3 节创建（空模块文件可以先建占位）
2. app/config.py：pydantic-settings，带 @lru_cache；字段含 database_url、output_dir、
   llm_api_key、llm_base_url、llm_model、auth 相关（本项目不用，但预留）
3. app/database.py：engine / SessionLocal / Base / session_scope() 上下文管理器；
   SQLite 时监听 connect 事件开 WAL 和 foreign_keys
4. app/common/response.py：ok(data) / fail(code, message) 两个函数
5. app/common/errors.py：BizError 异常 + 全局 handler（含 RequestValidationError 转 400）
6. app/main.py：注册健康检查、CORS、静态目录挂载占位
7. .env.example、requirements.txt、pytest.ini

验证并贴出真实输出：
  uvicorn app.main:app --port 8100 启动成功
  curl http://127.0.0.1:8100/api/health 返回 {"code":0,...}

注意：数据库 URL 必须支持环境变量注入，测试会把它指向临时文件。
```

### P1 项目 + 静态分析

```text
执行里程碑 M1。这一步是全项目的地基，规则层必须完全正确。

1. 建 8 张表（TASK_BOOK.md 第 4 节），字段类型和默认值严格照做。
   注意 test_cases 要有 project_seq 和 lint_issues；execution_records 必须有
   test_case_snapshot。
2. 项目 CRUD + PRD 上传（.md/.txt 直接读；.pdf 用 pypdf；.docx 用 python-docx）。
3. app/analyzer/scanner.py：递归收集文件、识别技术栈（有 pom.xml/build.gradle → java-spring；
   有 package.json + .vue → vue）。
4. app/analyzer/spring.py：按 TASK_BOOK.md 6.1.1 实现端点/实体/枚举/规则/UI 文案提取。
   特别注意：类级 @RequestMapping 只能作为路径前缀，不能本身成为端点。
   端点必须带 file 和 line。
5. app/analyzer/vue.py：按 6.1.1 实现路由/表单/选择器/文案提取。
   路由解析必须先找 path: 再向左找 { 做括号配平，不能用 [^{}]* 这类正则。
   <el-form 的正则要加 (?![-\w])。
6. app/agent/state_machine_agent.py：按 6.1.2 实现。
   必须支持：setStatus 写入点作为 verified 证据、守卫条件推出 from/to、
   if(!X) throw 的排除语义、BFS 求最短推进路径。
7. scripts/sample_src/：造一个最小但真实的 Spring Boot + Vue 样本
   （1 个 Controller、1 个 ServiceImpl 含 5 处 setStatus 和 4 处前置 throw、
    1 个 OrderStatus 枚举、1 个 Vue 新建页和 1 个列表页），
   并加 pom.xml / package.json。

验证并贴出真实输出：
  pytest tests/test_analyzer.py tests/test_state_machine.py -v
  期望：端点 >= 5 且路径不重复拼接；状态机 >= 1 个；转换 >= 4 条且全部 verified=true

如果样本源码里你自己造的代码解析不出来，先怀疑解析器，不要改样本去迁就解析器。
```

### P2 用例生成

```text
执行里程碑 M2 的生成部分。

1. app/agent/test_generator_agent.py：
   - rule 模式必须能独立产出 >= 12 条，覆盖 positive/negative/boundary/state 四类
   - 每条用例必须有：structuredSteps（非空）、expectedResults（非空）、testData、module
   - state 类型必须按 BFS 路径生成前置推进步骤（比如「完成」要先 支付→发货）
   - structuredSteps 的 type 只允许 ui_action / input / state_assert
2. app/agent/quality_gate.py：三道闸（Linter → 打分 → 去重）
   - 无 expectedResults 直接丢弃（硬门槛）
   - score < 40 丢弃
   - lintIssues 每条 -8（上限 -40）
   - 返回值要带 {generated, kept, dropped, dropReasons}
3. app/llm/client.py：OpenAI 兼容协议，支持任何 baseUrl；超时/429/JSON 非法
   一律抛 LlmUnavailable，调用方捕获后降级到规则模式。
4. app/service/generation_service.py：编排（分析结果 → 规则或 LLM → 质量闸 → 落库）
5. SSE 接口 GET /api/projects/{pid}/generate/stream，
   事件按 TASK_BOOK.md 6.1.5：started / progress / case / complete / cancelled / error
6. 覆盖自检：生成后反查缺口，规则模式自动补一轮

验证并贴出真实输出：
  pytest tests/test_generator.py tests/test_quality_gate.py -v
  再用一个临时库跑一次真实生成（不配 LLM Key），打印生成条数与四类型分布

必须做到：把 LLM_API_KEY 置空后，整条生成链路仍然跑通并产出 12 条以上用例。
```

### P3 用例管理

```text
执行里程碑 M2 的管理部分。

1. 用例列表：分页 + 筛选（module/type/priority/executionStatus/keyword）+ 排序
   响应结构 {total, page, pageSize, items, testCases}（items 和 testCases 同时返回）
2. 编辑：校验 structuredSteps 非空 → 落版本 → 重跑质量闸 → 更新
3. 版本列表 + 回滚（回滚也产新版本，不回退版本号）
4. 批量删除、复制到其他项目
5. CSV 导出 / 导入（表头固定，多值用 " | " 分隔）
6. XMind 导入（zip 读 content.json，children 兼容数组与 {attached:[]} 两种）
7. 执行状态回写接口

前端：用例列表页（路由 /projects/:id/testcases）
  - 顶部四张覆盖卡（接口/状态转换/规则/通过率），数据来自 /coverage/matrix
  - 表格多选、批量删除、批量执行
  - 编辑抽屉（宽 720）含结构化步骤编辑器：可增删、上下移动、每步可选 type
  - 版本对话框、导入导出按钮

验证并贴出真实输出：
  pytest tests/test_api_testcases.py -v
  浏览器打开用例列表页截图，确认无白屏、覆盖卡有数字
```

### P4 自动执行

```text
执行里程碑 M3。这是项目最核心也最容易做假的一步。

1. scripts/demo_site/serve.py：FastAPI 静态服务 + SPA 路由回退（端口 8200），
   三个页面按 TASK_BOOK.md 附录 A 实现。
   关键：提交订单成功后写 sessionStorage 并 500ms 后跳 /orders/list；
        列表页初始化读 sessionStorage 把新订单插到第一行；
        四个操作（支付/发货/完成/取消）严格校验前置状态。
2. app/agent/execution_agent.py：
   - uiSelector 映射严格按 6.3.3 的表，placeholder 必须是 [placeholder="..."]
   - 兜底链五级，最后一级必须是 normalize-space 的 XPath
     （Element Plus 会把「登录」渲染成「登 录」）
   - 三层断言按 6.3.4：URL → 引号锚点文本 → 诚实 skipped
   - 每步前后截图；context 级 record_video_dir 录 WebM
   - 失败分类严格用 6.3.6 的 6 个枚举值
3. 执行服务：单条（同步返回）、批量（异步，上限 100，串行）、批次查询、取消
4. 执行前必须写 test_case_snapshot
5. 证据文件接口做路径穿越校验（realpath 必须在 outputs 下）

验证并贴出真实输出：
  python scripts/e2e.py
  期望：12 条用例通过率 >= 90%，截图数 >= 用例数×步骤数，每条用例有 video.webm

任何一条失败，先把那一步的 after 截图打开看清楚页面停在哪，
再判断是选择器问题、靶站问题还是用例问题。不要靠猜。
```

### P5 脑图 + 执行结果

```text
执行里程碑 M4。

1. 脑图服务：四层树（模块→类型→用例→前置/步骤/预期），id 前缀严格按 6.4.1
   （module-* / type-* / case-* / pre-* / steps-* / exp-* / s-*），
   statistics 要包含 modules/types/cases/leaves
2. XMind 导出：手写 zip（content.json + metadata.json + manifest.json）
3. Markdown 导出（markmap 兼容）
4. 执行历史：分页 + 筛选 + 顶部统计 + 14 天趋势
5. 执行详情：概览 + <video> 录屏 + 步骤时间线（before/after 并排，可放大）
6. 批次聚合接口 + 批次页
7. HTML 报告（Jinja2 单文件自包含，截图用相对路径）：
   头部通过率大字 + 统计条 + 用例折叠面板 + 覆盖矩阵 + 失败分类分布
8. 覆盖矩阵按 6.5.5 实现，特别注意：执行覆盖必须过滤 status='passed'

验证并贴出真实输出：
  pytest -q（全量）
  python scripts/e2e.py
  打开生成的 report.html 截图确认排版正常
  故意让一条用例失败，确认覆盖矩阵的执行覆盖率会下降（这是自洽性验证）
```

### P6 收口

```text
执行里程碑 M5。

1. README.md：一句话定位、架构图（ASCII 或 mermaid）、三步启动、
   五个模块各一段说明、已知边界（诚实写 LLM 模式的局限）、截图
2. migrations/mysql/001_init.sql：写个小脚本从 ORM 元数据生成 DDL，
   每张表补 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
3. requirements.txt 按实际安装版本固定
4. 补一个 scripts/demo_site/README 说明靶站
5. 全量跑一遍：pytest + e2e，把输出贴出来

最后给我一份「这个项目能写进简历的 5 个技术点」，
每个点要指明对应的文件与函数，不要空谈。
```

---

## 三、纠偏提示词（AI 跑偏时用）

### 3.1 它开始糊空壳 / 大量 TODO

```text
停。你现在的实现里有 N 处 TODO / pass / 返回假数据。这个项目不接受空壳。

请逐条处理：
1. 列出所有 TODO、未实现的 except、返回 mock 数据的位置
2. 对每一处，要么现在真正实现，要么在代码里明确 raise NotImplementedError 并说明
3. 然后跑一次真实验证命令，把输出贴给我

我判断进度只看「跑起来的输出」，不看代码数量。
```

### 3.2 它偷偷让 LLM 参与规则层

```text
检查你刚才写的静态分析和质量闸代码：里面有没有任何 LLM 调用？

这个项目的铁律是：静态分析、状态机提取、质量校验、覆盖率统计必须纯规则，
零 token 消耗，不配 Key 也能跑出完全一样的结果。

如果发现有 LLM 参与，改回纯规则实现，并说明改了什么。
然后把 LLM_API_KEY 置空再跑一次生成，确认产出条数不变。
```

### 3.3 执行通过率虚高 / 用例假绿

```text
我怀疑执行器在假绿。请自证：

1. 打印一条 state_assert 步骤的判定过程：expected 原文、抽出的引号锚点、
   当前页 innerText 里是否命中、最终 result。
2. 故意把某条用例的 expected 改成页面上不存在的文案，重跑，
   确认这条用例变成 failed 而不是 passed。
3. 故意把 expected 改成没有引号的抽象描述（如「订单状态正确」），重跑，
   确认这条变成 skipped 而不是 passed。

把三次的真实输出贴出来。任何一次不符合预期，就修断言逻辑。
```

### 3.4 覆盖率和通过率互相矛盾

```text
检查 /coverage/matrix 的执行覆盖统计：
它是不是把所有执行记录都算成已覆盖，没区分 passed / failed？

要求：执行覆盖 = 有执行记录 且 该记录 status='passed'。
否则会出现「通过率 33% 但执行覆盖 100%」这种自相矛盾。

改完后，制造一条失败用例，验证执行覆盖率下降。
```

### 3.5 它开始扩大范围

```text
你正在做的功能不在 TASK_BOOK.md 的范围内（用户登录 / 任务队列 / 向量库 / RBAC）。
本项目明确不做这些。

请停下来，把已经写的一半代码回退掉，回到最后一个可运行的里程碑状态，
然后继续做当前里程碑里还没完成的部分。

如果确实认为不做会导致主流程断裂，先说明是哪条链路断了，再等我决定。
```

### 3.6 前端页面白屏 / 报 undefined

```text
页面白屏，通常是接口契约不一致。按顺序排查：

1. 浏览器 Network 里看这个请求的实际响应 JSON，贴给我
2. 对比 TASK_BOOK.md 第 5 章该接口的契约，指出差异
3. 检查前端读的字段路径（比如 data.items vs data.testCases）
4. 后端缺字段就补后端，不要在前端写一堆 ?. 兜底把问题藏起来

贴出：请求 URL、响应 JSON、报错堆栈、你改了哪里。
```

---

## 四、验收提示词（最后用）

```text
项目做完了，现在做验收。请按 TASK_BOOK.md 第 9 章逐条核对，
每条给出「通过 / 不通过 + 证据（命令输出或文件路径）」，不要概括。

必须包含这些实测：
1. 全新虚拟环境 pip install -r requirements.txt 是否成功
2. playwright install chromium 后在无 LLM Key 情况下，e2e 是否通过
3. pytest 全量条数与结果
4. 生成用例总数、四类型分布、质量分分布
5. 执行通过率、截图数、WebM 数
6. 覆盖矩阵的接口/转换/规则三个口径的规划覆盖与执行覆盖
7. 打开 report.html，列出它包含哪些区块
8. 把 TASK_BOOK.md 附录 B 的交付清单逐项打勾或标缺

最后告诉我：如果只允许我改 3 个地方让这个项目更可信，你会改哪 3 个？
（要说具体文件和具体改动，不要说「加强测试」这种空话）
```

---

## 五、几条实战经验（不用发给 AI，你自己看）

1. **里程碑之间一定要真跑。** 这个项目最容易翻车的不是写代码，而是"分析器自认为解析对了"和"执行器自认为执行对了"。每步都要看真实输出。
2. **先让规则模式全绿，再接 LLM。** 顺序反了的话，一旦 LLM 出问题，你无法判断是 LLM 的锅还是引擎的锅。
3. **靶站要先自己点一遍。** 用浏览器手动走一遍「新建 → 列表 → 支付 → 发货 → 完成」，确认状态机和你写的一致，再去跑自动执行。靶站不闭环，用例再对也全红。
4. **AI 最容易偷懒的三处**：兜底选择器链（只写一级）、状态机证据（随手标 verified）、覆盖率的 passed 过滤（忘了加）。这三处上手就要盯。
5. **让它自己截图。** 每完成一个页面就让 AI 跑一次截图存到 `outputs/shots/`，白屏和样式塌陷肉眼一秒就能发现，比看代码快十倍。
