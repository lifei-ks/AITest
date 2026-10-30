# AITest 代码审查报告

- 审查日期：2026-10-26
- 审查范围：后端（分析器、Agent、生成/执行/工作流服务、路由、模型、迁移）、前端（Vue 页面/组件/API）、测试与工程配置
- 审查方式：只读读码 + 关键路径交叉验证；未修改任何业务代码
- 基线状态：后端 `144 passed`，前端 `npm run build` 通过

## 结论摘要

这次审查没有发现“服务完全不可用”的问题，但发现了两类会直接影响演示/日常使用的缺陷：

1. **手工编辑用例时，预期结果和结构化步骤实际没有保存**：前端发 camelCase，后端 DTO 只认 snake_case，Pydantic 默认忽略多余字段，接口仍返回成功。
2. **Pipeline 的确定性审核把“全项目所有页面的必填字段”套到每一条用例上**：列表筛选、统计、状态类用例会被误判“必填字段未填写”，无 LLM 时直接 reject，导致 Pipeline 大量用例不可执行。

另外还有一批 P1/P2 问题，主要集中在执行证据覆盖、删除级联、报告 XSS、路径安全、异常恢复和前端请求竞态。按严重度汇总：**P0 2 项、P1 10 项、P2 10 项**。

> 说明：本报告只给结论和修复建议，不代替实际修复。涉及行号以当前提交 `5019ebc` 为准。

---

## 优先级清单

| ID | 级别 | 问题 | 位置 |
|---|---|---|---|
| P0-1 | P0 | 手工编辑用例的预期/步骤未保存 | `app/routers/testcases.py`、`app/service/testcase_service.py`、`frontend/src/views/TestcasesView.vue` |
| P0-2 | P0 | Pipeline 审核把全项目必填字段套到每条用例 | `app/service/workflow_service.py` |
| P1-1 | P1 | 版本回滚只恢复标题等基础字段 | `app/service/testcase_service.py` |
| P1-2 | P1 | 重复执行覆盖历史截图，录屏可能串档 | `app/agent/execution_agent.py` |
| P1-3 | P1 | 删除项目未级联需求/测试点/执行元数据/工作流 | `app/service/project_service.py` |
| P1-4 | P1 | HTML 报告未转义，存在存储型 XSS | `app/service/report_service.py` |
| P1-5 | P1 | 证据目录直接使用项目名，存在路径穿越/非法字符 | `app/service/execution_service.py` |
| P1-6 | P1 | 服务重启后执行记录可能永久 running | `app/service/execution_service.py`、`app/main.py` |
| P1-7 | P1 | 定位器立即探测，慢渲染会假红；`body` 兜底可能假绿 | `app/agent/execution_agent.py` |
| P1-8 | P1 | LLM 最坏重试链路过长，429 无退避 | `app/llm/client.py` |
| P1-9 | P1 | AI 审核异常会把已 passed 的执行改判 failed | `app/service/execution_service.py` |
| P1-10 | P1 | 前端切项目/需求时旧请求可能覆盖新数据 | `TestcasesView.vue`、`ExecutionsTab.vue`、`GenerationTab.vue` |
| P2-1 | P2 | 需求文档未保存时切换版本会静默丢失 | `RequirementsTab.vue` |
| P2-2 | P2 | 质量闸对分号预期误杀；无锚点预期被整句包装后假红 | `quality_gate.py`、`test_generator_agent.py` |
| P2-3 | P2 | 固定手机号/订单号在共享数据下重复执行会假红 | `test_generator_agent.py` |
| P2-4 | P2 | MySQL TEXT 上限与无限制 PRD/快照写入不匹配 | `models.py`、`routers/requirements.py` |
| P2-5 | P2 | CSV 导入缺列/非法数字会 500，且逐行全表扫描 | `testcase_service.py` |
| P2-6 | P2 | 用例路由不校验 pid；执行报告忽略 eid | `routers/testcases.py`、`routers/mindmap.py` |
| P2-7 | P2 | 需求版本唯一性只靠先查后插 | `requirement_service.py`、迁移 |
| P2-8 | P2 | Spring 分析器漏方法级 `@RequestMapping` | `app/analyzer/spring.py` |
| P2-9 | P2 | Vite 未加载 `.env`，`dev.ps1` 硬编码路径；README 测试数不一致 | `vite.config.js`、`scripts/dev.ps1`、`README.md` |
| P2-10 | P2 | 前端包体大、无 lint/前端测试；轮询/SSE/错误态有改进空间 | `frontend/` |

---

## P0 详情

### P0-1 手工编辑用例的预期和步骤没有保存

**位置**

- `app/routers/testcases.py:29-40`：`TestCasePayload` 字段全部是 `expected_results`、`structured_steps`
- `app/service/testcase_service.py:138-148`：`_apply_payload` 只识别 snake_case
- `frontend/src/views/TestcasesView.vue:882-891`：提交的是 `expectedResults`、`structuredSteps`

**原因**

`TestCasePayload` 是普通 `BaseModel`，没有 camelCase alias；Pydantic 默认 `extra=ignore`，所以前端传来的 `expectedResults` / `structuredSteps` 被静默丢弃。`_apply_payload` 又只遍历 snake_case，最终只更新了标题、模块、类型、优先级等基础字段。

**影响**

- 用户编辑预期/步骤后点击保存，界面提示“已保存”，实际内容没有变化。
- 仍然会写入一条 `TestCaseVersion`，造成“版本变了但内容没变”的脏历史。
- 面试演示时这是最容易被面试官当场发现的问题。

**建议**

给 `TestCasePayload` 增加统一的 camelCase alias（项目里已有 `CamelModel`），或让 `_apply_payload` 同时兼容 camelCase；补一条 API 测试，断言编辑后 `expectedResults` / `structuredSteps` 真的变化。

### P0-2 Pipeline 审核把全项目必填字段套到每条用例

**位置**

- `app/service/workflow_service.py:880-894`：`_missing_required_fields` 遍历 `facts["pages"]` 的所有表单字段
- `:1013-1021`：只要 `fixable` 非空且无法修复，就 `reject`
- `:1253-1258`：每条用例都会调用这套确定性审核

**原因**

函数没有先判断“这条用例属于哪个页面”，而是把整个项目所有页面的必填字段合并成一个集合。列表筛选用例只填金额，统计用例根本不填表单，都会被判“收货人/手机号/地址/金额未填写”。

**影响**

- 规则模式下大量列表/统计/状态用例被判 `revise` 或 `reject`。
- 无 LLM 时直接 reject，Pipeline 的“审核通过且可自动化”候选集大幅缩小，出现“生成成功但执行阶段没有用例”的观感。
- 这也是之前用户反馈“Pipeline case 都失败/被拒绝”的结构性原因之一。

**建议**

按用例第一步的 route 找到对应页面，只校验该页面表单的必填字段；没有表单的页面跳过必填校验。补 Pipeline 规则模式测试，断言列表/统计用例不会被“未填收货人”拒绝。

---

## P1 详情

### P1-1 版本回滚只恢复基础字段

`testcase_service.py:221-233` 使用 `case_to_dict` 生成的 camelCase 快照回滚，但 `_apply_payload` 只认 snake_case。结果回滚只恢复 title/module/type/priority/source，`expectedResults`、`structuredSteps`、`testData` 不恢复，且新增一条“回滚成功”的版本记录。

建议：回滚前把快照统一转换成 `_apply_payload` 认识的键；测试中断言回滚后预期与步骤内容。

### P1-2 重复执行覆盖历史截图，录屏可能串档

`execution_agent.py:175` 的产物目录是 `outputs/{项目}/{用例id}`，没有 execution_id；`:202-205` 的截图文件名固定为 `step-NN-*.png`；`:329-334` 用 `glob("*.webm")` 找视频并把第一个重命名为 `video.webm`。

影响：同一用例第二次执行会覆盖第一次的截图，而历史 `ExecutionStep` 仍指向同一路径；旧执行详情会展示新证据。目录里已有 `video.webm` 时，`videos[0]` 可能取到上一次的录屏。

建议：目录改为 `outputs/{project_id}/{execution_id}/{case_id}`；视频只取本次执行新建的文件，或在 finally 中按本次临时目录收集。

### P1-3 删除项目级联不完整

`project_service.py:92-116` 删除了 TestCase/TestCaseVersion/ExecutionRecord/ExecutionStep/CodeAnalysis/StateMachine/Mindmap/CaseGeneration，但没有删除：

- `requirements`、`test_points`
- `requirement_cases`（用例已删，映射成孤儿）
- `execution_requirements`
- `execution_reviews` / `execution_review_issues` / `execution_review_images`
- `workflow_runs` / `workflow_stages` / `workflow_cases` / `workflow_case_reviews` / `workflow_result_analyses`

影响：项目删除后残留数据不可达且无法清理；工作流详情/报告仍可能按残留 case_id 展示。

建议：按 project_id 补齐所有表；没有 project_id 的表先按 requirement_id / execution_id / run_id 收集再删；整个删除放同一事务并补测试。

### P1-4 HTML 报告未转义，存在存储型 XSS

`report_service.py:35` 直接 `Template(...)`，Jinja2 默认 `autoescape=False`；`:264` 渲染；`routers/workflows.py:44-48`、`routers/mindmap.py:59-76` 以 `text/html` 返回。项目名、用例标题、错误文案、CSV 导入内容都可能进入模板。

影响：恶意用例标题/项目名可在报告页面执行脚本，属于存储型 XSS。

建议：使用 `Environment(autoescape=True)` 或 `select_autoescape()`；图片路径等可信字段单独标记 `|safe`。

### P1-5 证据目录直接使用项目名

`execution_service.py:459-464` 用 `proj.name` 拼 `outputs` 子目录；`dto/projects.py:20` 只限制长度，没有字符白名单。

影响：

- 项目名 `../..` 可把截图/录屏写到 outputs 之外。
- Windows 非法字符（`: * ? " < > |`）会让 `mkdir` 失败，整批用例变成 ENV_ERROR。
- 失败路径下 `_rel_path` 会回退成绝对路径，`/file` 接口又因越界返回 400。

建议：目录使用 project_id；项目名只用于展示；生成目录前做字符清洗和 `resolve()` 边界校验。

### P1-6 服务重启后执行记录可能永久 running

`execution_service.py:448-456` 先提交 `running` 记录，再执行 Playwright；进程被杀/重启时 `_mark_execution_interrupted` 不会运行。`main.py:36-42` 只回收 workflow，不回收 execution。

`workflow_service.py:506-525` 的回收要求最后活动时间超过 2 小时，重启后 2 小时内中断的 run 仍会保持 running。

建议：启动时按进程启动时间回收 `running` 的执行记录与工作流；为长任务增加心跳字段，而不是只靠 started_at；回收时同步回写用例状态。

### P1-7 定位器立即探测导致假红/假绿

`execution_agent.py:114-119` 对每个候选选择器直接 `count()` + `is_visible()`，没有 Playwright 的自动等待。异步渲染/下拉动画慢 200ms 就会 SELECTOR_NOT_FOUND。

`execution_agent.py:100` 的 `body :has-text` 会命中外层容器，点击容器可能“步骤通过”但实际没有触发按钮；`input` 步骤命中 div 后会被归为 ENV_ERROR，错误分类也不准确。

建议：用 `locator.wait_for(state="visible", timeout=...)`；兜底链收窄到可交互元素；input 只接受 input/textarea/contenteditable，命中其他元素时明确报 CASE_INVALID。

### P1-8 LLM 最坏重试链路过长，429 无退避

`llm/client.py:186-214` 在一次 `chat_json` 中可能发起 3 次 `_chat_raw`；`_request` 在 400/422 时最多再尝试 3 种参数组合；每次 `httpx.post(timeout=240)`。最坏情况会阻塞 SSE/Pipeline 线程十几分钟。429 没有指数退避，直接失败/降级。

建议：引入总时间预算（例如单次生成 120s），超时/429 做有限退避；把重试次数和超时做成配置；后台任务与 HTTP 请求解耦。

### P1-9 AI 审核异常会把已 passed 的执行改判 failed

`execution_service.py:471-477` 在 `review_execution` 抛异常时调用 `_mark_execution_interrupted`，后者把记录置为 failed/ENV_ERROR。如果 Playwright 已经跑完且确定性结果 passed，仅因可选的 AI 审核失败就被覆盖。

建议：AI 审核异常统一降级为 `unavailable`，保留确定性执行结果；只有执行器本身异常才写 failed。

### P1-10 前端切项目/需求时旧请求可能覆盖新数据

- `TestcasesView.vue:584-595`：`load()` 没有请求序号/取消；`watch(projectId)` 清空了部分状态但没有清 `rows`，旧响应回来仍会写 `rows/total`。
- `ExecutionsTab.vue:550-575`：list 返回后再读当前 props 拉矩阵/总览，没有序列保护，切项目会错配。
- `GenerationTab.vue:324-430`：等待后用新的 requirementId 加载旧 generation；生成中切换需求只 close SSE，后端仍继续生成。

影响：界面显示上一个项目/版本的用例或执行记录，可能误执行、误删除；生成结果串版本。

建议：每个请求带 `seq` 或 `AbortController`，回写前校验 projectId/requirementId；切换时先清空旧数据；生成中切需求要明确提示并禁用重复触发。

---

## P2 详情

### P2-1 需求文档未保存时切换版本会静默丢失

`RequirementsTab.vue:300-355` 有 `detailRequestSeq` 防止旧响应覆盖，但没有检查 `dirty`。用户编辑文档后点击另一个版本，`loadDetail` 直接用新内容覆盖 `prd`。

建议：`dirty` 时弹确认或保存草稿；切换前禁用编辑。

### P2-2 质量闸误杀分号预期；无锚点预期假红

- `quality_gate.py:54-55`：`is_compound_expected` 只要分号两侧都 ≥4 字就判复合，`提交订单；提示「成功」` 这种单场景也会被丢。
- `test_generator_agent.py:464-468`：没有「」锚点时把整句包成 `「提交后提示创建成功」`，执行器做精确子串匹配，必然找不到。

建议：复合判定基于锚点数量和结果子句语义；无锚点预期应降级 skipped 或要求 LLM 补锚点，而不是硬造一个整句锚点。

### P2-3 固定测试数据在共享数据环境会假红

`test_generator_agent.py:25-39` 的样例值包含固定手机号/订单号；`_success_text` 取页面里任意含“成功”的文案，可能与本次动作无关。

建议：用时间戳/随机后缀生成唯一值；成功文案限定为操作后的 toast/区域；共享数据测试优先用唯一行标识，而不是固定总数。

### P2-4 MySQL TEXT 上限与无限制写入不匹配

`models.py:257`（requirements.content）、`:512`（case_generations.snapshot）等使用 MySQL `TEXT`（utf8mb4 约 1.6 万汉字）；`routers/requirements.py:71-72` 全量读取上传文件，不截断。SQLite 不会暴露问题，MySQL 严格模式会 `Data too long`。

建议：PRD 限制大小并提示；快照超限时压缩/裁剪；MySQL 改 `MEDIUMTEXT` 需要迁移评估。

### P2-5 CSV 导入缺列/非法数字会 500，且逐行全表扫描

`testcase_service.py:295-319` 直接 `row["前置条件"].split`、`int(row["质量分"])`；每行调用 `_next_tc_id`，而 `_next_tc_id` 会 `select(TestCase.id)` 全表求最大值，N 行导入 O(N²)。

建议：表头校验 + 逐行错误收集；批量分配 ID；限制行数/文件大小。

### P2-6 用例路由不校验 pid；执行报告忽略 eid

`routers/testcases.py:130-161` 的 pid 参数没有参与查询/校验，可跨项目读改删；`routers/mindmap.py:68-76` 用 eid 只取 project_id，返回整个项目的执行报告。当前系统无鉴权，但这会破坏 API 契约，未来加多租户时是越权漏洞。

建议：所有子资源查询加 `project_id == pid`；报告按 eid 过滤。

### P2-7 需求版本唯一性只靠先查后插

`requirement_service.py:193-208` 先查再插，迁移中没有 `(project_id, version)` 唯一索引；并发创建可产生重复版本号，`ensure_initial_requirement` 并发 GET 也可能生成两条 V1.0。

建议：加唯一约束并捕获 IntegrityError；初始化用幂等 upsert。

### P2-8 Spring 分析器漏方法级 `@RequestMapping`

`app/analyzer/spring.py:27` 只匹配 `@Get/Post/Put/Delete/PatchMapping`；方法级 `@RequestMapping(value=..., method=GET)` 不会被识别，类级无括号的 `@RequestMapping` 也只按前缀处理。会漏报接口，进而少生成测试点和覆盖矩阵。

建议：增加方法级 `@RequestMapping` 解析，读取 `method` 属性；补一个混合映射的样本测试。

### P2-9 工程配置与文档不一致

- `frontend/vite.config.js:6` 用 `process.env.VITE_BACKEND_TARGET`，Vite 不会把 `.env` 注入 `process.env`，应使用 `loadEnv`。
- `scripts/dev.ps1:4` 硬编码 `D:\node\node.exe` 和绝对项目路径。
- `README.md:13` 徽章仍是 `138 passed`，正文是 `144 passed`。
- `.env.example` 没有 `VITE_BACKEND_TARGET`。

建议：统一配置入口，README 只保留一个测试数字。

### P2-10 前端包体、轮询与错误态

- `main.js` 全量引入 Element Plus 和全部图标，`index` JS 约 1.3MB，Mindmap chunk 约 1MB；无 lint / 前端测试脚本。
- `RequirementsTab.vue`、`ExecutionsTab.vue`、`WorkflowTab.vue` 轮询无上限/退避/页面隐藏暂停。
- `CoverageTab.vue:249` 请求失败把 matrix 置 null，页面显示“暂无数据”，掩盖网络错误；`ExecutionsTab.vue:654` 同样把详情失败显示成“无步骤”。
- `TestcasesView.vue:805-819` “全部执行”最多取 200 条，超过 100 条直接拒绝，但按钮文案叫“全部执行”；移动端编辑抽屉固定 760px。

建议：按需引入 Element Plus/echarts；轮询加退避和可见性判断；错误态与空态分开；执行全部改为后端按筛选分批。

---

## 建议修复顺序

1. **P0-1**：先修编辑保存，避免继续产生“假保存”的版本数据。
2. **P0-2**：按页面校验必填字段，否则 Pipeline 的核心卖点不可用。
3. **P1-1/P1-2/P1-3**：版本回滚、证据隔离、删除级联，都是数据一致性问题，越晚修迁移成本越高。
4. **P1-4/P1-5**：报告转义、证据目录清洗，属于安全底线。
5. **P1-6/P1-9**：异常恢复和 AI 审核降级，避免“执行成功被写成失败/永久 running”。
6. **P1-7/P1-8/P1-10**：执行稳定性和前端竞态，影响日常体验。
7. P2 按演示/使用频率排。

## 未发现的问题

以下点经过读码未发现可指出的缺陷，可以作为正面结论：

- 前端没有发现 `v-html` / `innerHTML` 等直接 XSS sink；报告模板是唯一的 HTML 拼接点。
- `.env` 未被 git 跟踪，LLM Key 在后端以掩码返回（短 Key 只暴露前 4 位，见 P2）。
- `/file` 接口对 outputs 目录做了边界校验。
- 执行状态机的核心原则是对的：确定性 failed 不会被 AI 改成 passed。
- 生成空结果保护、需求删除时的自动用例清理、覆盖矩阵只统计当前实时用例和 passed 执行，这些逻辑是成立的。

## 复现建议

- P0-1：在用例编辑抽屉修改预期/步骤 → 保存 → 刷新页面，预期/步骤恢复原值。
- P0-2：用规则模式跑一次 Pipeline，观察列表/统计用例的审核问题列表出现“必填字段未填写：收货人/手机号/地址/金额”。
- P1-2：同一用例执行两次，对比两次执行详情里的截图路径和内容。
- P1-4：项目名或 CSV 用例标题写入 `<img src=x onerror=alert(1)>`，打开 HTML 报告。
- P1-5：新建项目名 `../tmp-test`，执行用例，观察 outputs 目录位置。

---

审查结论：项目的主架构和“确定性内核 + LLM 增强”的方向是成立的，当前问题主要集中在**契约一致性、数据一致性、执行证据隔离和异常恢复**。先把 P0 两项修掉，再做一轮 P1 的数据与安全收口，这个项目就具备比较完整的演示和面试讲解条件。
