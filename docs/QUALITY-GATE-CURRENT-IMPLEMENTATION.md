# 测试用例质量分与去重：当前实现说明

- 更新日期：2026-10-29
- 对应代码：`app/agent/quality_gate.py` 及相关生成、审核、用例服务
- 当前状态：已实现并通过后端全量测试（158 passed）

## 1. 整体流程

生成一条用例后，质量闸按以下顺序处理：

```text
硬门槛（可执行性）
  ↓ 不通过：丢弃并记录原因
分项评分（0~100）
  ↓ 低于 40：保留并标记待复核（默认）
结构化场景签名去重
  ↓ 完全相同：保留高分者
  ↓ 近似重复：保留并追加 warning
落库（quality_score / quality_breakdown / scenario_key / lint_issues）
```

质量分只用于排序、展示和复核优先级；能不能自动执行由硬门槛决定。

## 2. 硬门槛

以下任一条件命中，用例直接丢弃，不参与打分：

| 原因键 | 命中条件 |
|---|---|
| `no_steps` | `structured_steps` 为空 |
| `no_expected` | `expected_results` 为空 |
| `compound_expected` | 一条预期包含多个「」锚点、嵌套锚点，或分号并列多个结果 |
| `multiple_inputs` | 同一字段被填入两个不同值 |
| `unknown_step_type` | 步骤类型不在 `ui_action/input/capture/state_assert` |
| `no_assertion` | 没有任何 `state_assert` 步骤 |

复合预期的判定还包括：预期中出现两个及以上引号锚点、锚点内部再嵌套
`「」`，或者分号两侧各是一句长度不小于 4 的完整结果。

## 3. 分项评分

总分 100，由五个维度相加。每个问题只在一个维度扣一次。

### 3.1 断言强度（30 分）

| 情况 | 扣分 |
|---|---:|
| 没有任何「」引号锚点 | -15 |
| 锚点数量大于 1 | -10 |
| 包含“显示正常/符合预期/页面正常”等模糊断言 | -10 |

下限为 0 分。

### 3.2 数据独立性（25 分）

| 情况 | 扣分 |
|---|---:|
| 固定总数断言（如“共 5 条”）且没有 `capture` 基线 | -15 |
| 输入值使用固定手机号、`ORD`/`PO` 订单号等共享数据 | -10 |
| 状态用例没有“首行/唯一/订单号/指定订单”等唯一行标识，也没有 capture | -10 |

预期里使用 `${base_count+1}` 这类运行时变量时，不算固定总数。

### 3.3 选择器稳定性（20 分）

逐个检查 `ui_action` 和 `input` 步骤：

| `uiSelector.type` | 每步扣分 |
|---|---:|
| `testid` / `role` / `route` | 0 |
| `placeholder` / `label` | -2 |
| `text` | -4 |
| `css` / `xpath` | -8 |
| 空或未知 | -8 |

本项最低 0 分。缺少 `uiSelector` 只在选择器维度扣一次，lint 中的提示不额外扣分。

### 3.4 覆盖绑定（15 分）

| 情况 | 得分 |
|---|---:|
| 同时有 `test_point_id` 和非空 `coverage_refs` | 15 |
| 只有其中一项 | 8 |
| 都没有 | 0 |

### 3.5 步骤规模（10 分）

| 步骤数 | 得分 |
|---|---:|
| ≤ 10 | 10 |
| 11~15 | 5 |
| > 15 | 0 |

### 3.6 低分处理

- 默认 `drop_low_score=False`：低于 40 分仍然保留，`GateResult.needs_review` 加一，并在
  `lint_issues` 追加“质量分低于 40，建议人工复核”。
- 生成落库时，低于 40 分的用例 `execution_status` 写为 `needs_review`。
- 手工新增/编辑时，如果原状态是 `not_run` 且分数低于 40，也会置为 `needs_review`。
- 传 `drop_low_score=True` 可恢复旧行为：低于 40 分直接丢弃，计入 `dropReasons["low_score"]`。

## 4. 去重

### 4.1 场景签名

每条用例生成一个 SHA-256 签名，签名字段：

```text
scope            # 跨批次去重时可用，当前默认空
testPointId      # 测试点 ID，没有则为空
route            # 第一个 route 步骤的 value
steps            # 每步：(type, action, target, uiSelector.type,
                 #        uiSelector.value, inputValue, expected,
                 #        variable, pattern)
anchors          # expectedResults 和 state_assert 中的「」锚点
```

签名包含输入值，所以“20 字符”和“21 字符”不会得到相同签名。

### 4.2 判重规则

1. 不同 `test_point_id` 的用例默认保留，即使步骤完全相同。
2. 同一 `test_point_id`、但预期不同的用例保留，视为不同场景。
3. 其余情况比较场景签名：签名相同即判重，保留 `quality_score` 高者。
4. 同一批次内按质量分降序处理，先保留高分用例。

### 4.3 近似重复

近似重复不丢弃，只追加 warning。触发条件：

- 两条用例的路由相同；
- 步骤类型序列相同；
- 文本相似度大于 0.98。

`GateResult.near_duplicate` 记录命中数量，`lint_issues` 中会出现
“近似重复用例：与「xxx」高度相似，建议复核”。

### 4.4 跨批次去重

- `TestCase.scenario_key` 保存签名。
- 生成前读取同项目的历史签名：
  - 需求版本模式：只读取同一需求版本下的用例；
  - 项目级模式：只读取没有关联需求版本的用例；
  - 本次会被替换掉的旧 `rule/ai` 用例会从查询中排除。
- 新用例签名命中历史签名时，直接跳过，计入 `dropReasons["duplicate"]`。
- 手工新增、编辑、复制也会计算 `scenario_key`。
- 历史数据在迁移后 `scenario_key` 为空，需要重新保存或重新生成后才会参与跨批次去重。

### 4.5 审核 Agent 的标题相似度（仅标注）

质量闸负责“删除精确重复”，审核 Agent 另外做一层“标题相似度提示”：

- 标题去空白/标点后计算相似度；
- 再比对 route、inputValue、expectedResults；
- 输入值不同 → 不算重复（避免 20/21 字符边界被误杀）；
- 输入、预期、路由相同 → `NEAR_DUPLICATE` warning；
- 标题相似但预期不同 → `TITLE_SIMILAR` info；
- 审核只标注，不自动删除用例。

## 5. 工作流审核

工作流审核使用同一套分项评分，但额外处理 `fixable` 问题：

```text
score = 分项评分
if fixable:
    score = max(0, score - min(5 * len(fixable), 20))
if blocking:
    score = min(score, 30)
```

`warnings` 只展示，不参与扣分。

### 必填字段校验

`_missing_required_fields` 现在只检查：

- 用例类型是 `positive` 或 `functional`；
- 用例包含“提交/保存/创建/新增/确认/添加/注册/登录”等提交动作；
- 用例有首步路由，并且能匹配到源码事实中的页面。

匹配后只检查该页面的必填字段。反例、边界、列表、筛选、统计类用例不会被全项目
必填字段误判。

## 6. 落库与接口

`TestCase` 新增两个字段：

| 字段 | 类型 | 用途 |
|---|---|---|
| `quality_breakdown` | TEXT | 保存分项得分、问题明细和 warning |
| `scenario_key` | VARCHAR(64)，带索引 | 保存结构化场景签名 |

接口返回新增：

```json
{
  "qualityBreakdown": {
    "score": 85,
    "dimensions": {
      "assertion": {"score": 30, "max": 30, "issues": []},
      "dataIndependence": {"score": 25, "max": 25, "issues": []},
      "selectorStability": {"score": 12, "max": 20, "issues": ["空 定位扣 8 分"]},
      "coverageBinding": {"score": 8, "max": 15, "issues": []},
      "stepScale": {"score": 10, "max": 10, "issues": []}
    },
    "warnings": []
  },
  "scenarioKey": "64 位十六进制字符串"
}
```

编辑用例的请求体现在同时接受 camelCase 和 snake_case：

```json
{
  "expectedResults": ["「订单创建成功」"],
  "structuredSteps": []
}
```

## 7. 评估指标

- `duplicateRate`：使用与质量闸相同的 `scenario_signature` 计算精确重复率。
- `nearDuplicateRate`：同路由、同步骤类型序列且相似度 > 0.98 的用例对占比。
- `qualityScore`：均值、最小、最大值。

`evaluate_case_set` 返回中已加入 `nearDuplicateRate`，`compare_metrics` 将其视为
“越低越好”的指标。

## 8. 示例

### 8.1 20/21 字符边界

```text
TC-A：输入 20 个字符，断言「订单创建成功」
TC-B：输入 21 个字符，断言「收货人不能超过20个字符」
```

两条用例的签名包含不同的 `inputValue` 和断言锚点，因此都会保留。
它们可能被标记为“近似重复”warning，但不会被丢弃。

### 8.2 列表页用例

```text
打开 /orders
断言「暂无数据」
```

不包含表单提交动作，必填字段校验直接跳过，不会再被“收货人/手机号未填写”误判。

## 9. 测试覆盖

当前后端测试：`158 passed`。

质量闸相关测试覆盖：

- 无步骤、无预期、无断言、未知步骤类型直接丢弃；
- 分项评分和低分保留/可选丢弃；
- 缺选择器只扣一次；
- warning 不影响分数；
- 20/21 字符边界不被去重；
- 同一测试点不同预期保留；
- 不同测试点保留；
- 精确重复去重；
- 近似重复只警告；
- 跨批次签名去重；
- 同字段多输入丢弃，不同字段多输入允许；
- 工作流必填字段按页面过滤；
- camelCase 编辑真正落库。

## 10. 当前限制

- 历史用例的 `scenario_key` 为空，跨批次去重需要重新保存或重新生成后才生效。
- 选择器稳定性维度目前按固定阶梯扣分，权重可以在 `score_case_detailed` 中统一调整。
- MySQL 需要手动执行 `migrations/mysql/002_quality_gate.sql`；SQLite 启动时会自动补列。
- 近似重复只做同路由、同步骤类型序列的比较，跨页面、跨流程的语义重复不会识别。
