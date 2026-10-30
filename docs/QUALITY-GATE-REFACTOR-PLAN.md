# 测试用例质量分与去重改造方案

- 编制日期：2026-10-29
- 适用范围：`app/agent/quality_gate.py`、生成/审核/用例服务、评估指标
- 目标：让质量分可解释、可复现，让去重不误杀边界用例，并支持跨批次去重

## 0. 实施状态（2026-10-29）

已完成：

- 分项评分（断言 / 数据独立性 / 选择器稳定性 / 覆盖绑定 / 步骤规模）
- 低分保留为待复核，不再默认丢弃
- 结构化场景签名去重，20/21 字符边界用例不再误合并
- 跨批次去重（`scenario_key` + SQLite 自动补列 + MySQL 迁移）
- warning 不参与扣分，缺选择器只扣一次
- 工作流必填字段按用例所在页面过滤，反例/边界用例不误判
- 编辑用例支持 camelCase（`expectedResults` / `structuredSteps` 真正落库）
- 评估指标 `duplicateRate` 与质量闸口径一致，新增 `nearDuplicateRate`

验证：后端 158 个测试通过。

## 1. 现状与问题

当前质量闸由 `run_quality_gate()`（`app/agent/quality_gate.py:196`）执行，流程是：
硬门槛（无预期 / 复合预期 / 同字段多输入）→ `score_case()` 打分 → 相似度去重。

存在四个需要解决的问题：

1. 扣分重复且不可解释。缺 `uiSelector` 先在 lint 中扣 8 分，又在 `score_case`
   中按步骤扣 10 分；warning 与 blocker 同权参与扣分。
2. 分数与可执行性脱钩。工作流审核把 `blocking + fixable + warnings`
   全部交给 `score_case`，会出现 84 分但状态为 `reject` 的用例。
3. 模糊去重会误杀差异很小的用例。实测同一标题下：
   - 输入 20 字符 vs 21 字符、预期相同 → 相似度 0.993，被判重；
   - 预期仅数字不同（20 vs 21 个字符）→ 相似度 0.969，被判重。
   项目级生成的用例 `test_point_id` 为空，不享受"同测试点不同预期"豁免。
4. 去重只在单次生成的内存批次内生效。手工新增/编辑、CSV 导入、跨批次生成
   都不设防；评估指标 `duplicate_rate` 又使用精确指纹，与质量闸口径不一致。

## 2. 设计原则

1. 可执行性由硬门槛决定，质量分只做排序、展示和复核优先级。
2. 每个扣分项必须能追溯到具体问题，同一问题只扣一次。
3. warning 只展示，不参与扣分。
4. 去重先精确、后模糊；不同测试点、不同输入场景默认保留。
5. 低分用例保留为 `needs_review`，不再静默丢弃。
6. 保持 `run_quality_gate(cases)` 签名和 `quality_score` 0~100 语义不变，
   新增字段使用默认值，兼容现有调用方。

## 3. 质量模型

### 3.1 硬门槛（二元判定，失败即不可自动执行）

| 门槛 | 判定 | 处理 |
|---|---|---|
| 无结构化步骤 | `structured_steps` 为空 | 不可执行，保留为 manual |
| 无预期结果 | `expected_results` 为空 | 丢弃或转 manual |
| 复合预期 | 多个「」锚点、嵌套锚点、分号并列结果 | 拆分失败则丢弃 |
| 同字段多输入 | 同一 selector 填了两个不同值 | 丢弃并提示拆分 |
| 未知步骤类型 | 不在 `ui_action/input/capture/state_assert` | 不可执行 |
| 无断言步骤 | 没有任何 `state_assert` | 不可执行 |

硬门槛不参与打分，结果写入 `quality_breakdown.executability`
（`pass` / `fail`）以及具体原因。

### 3.2 质量维度（总分 100）

| 维度 | 分值 | 规则 |
|---|---:|---|
| 断言强度 | 30 | 有且仅有一个「」锚点得满分；无锚点 -15；锚点超过 1 个 -10；模糊断言 -10；绝对数字断言且无 capture 基线 -5 |
| 数据独立性 | 25 | 固定总数无基线 -15；固定订单号/手机号等共享数据 -10；状态用例无唯一行标识 -10 |
| 选择器稳定性 | 20 | testid/role 不扣；placeholder/label 每个 -2；text 每个 -4；body/整页兜底每个 -8；本项最多扣 20 |
| 覆盖绑定 | 15 | 同时有 test_point_id 和 coverage_refs 得 15；只有一项得 8；都没有得 0 |
| 步骤规模 | 10 | ≤10 步得 10；11~15 步得 5；>15 步得 0 |

实现方式：新增 `score_case_detailed(case)` 返回

```python
{
    "score": 0..100,
    "dimensions": {
        "assertion": {"score": 30, "max": 30, "issues": [...]},
        "dataIndependence": {...},
        "selectorStability": {...},
        "coverageBinding": {...},
        "stepScale": {...},
    },
    "executability": "pass" | "fail",
    "blocking": [...],
    "warnings": [...],
}
```

保留 `score_case(case, lint_issues)` 作为兼容包装，内部调用新实现。

### 3.3 分数用途

- `quality_score` 继续写入 `TestCase.quality_score`，供列表排序和展示。
- 新增 `quality_breakdown`（JSON）保存分项和问题明细，便于 UI 展开查看。
- 分数低于阈值不再直接丢弃，改为 `execution_status` 或审核状态
  `needs_review`，并保留在用例列表。
- 是否自动执行由硬门槛 + 审核状态决定，不由分数单独决定。

## 4. 去重设计

### 4.1 场景签名

新增 `scenario_signature(case) -> str`，用结构化字段生成稳定签名：

```text
scope        = project_id + requirement_id（跨批次去重时使用）
route        = 第一个 route 步骤的 value
steps        = tuple(
                   (type, action, target, uiSelector.type, uiSelector.value, inputValue)
                   for step in structured_steps
               )
anchors      = tuple(quoted_anchors(expected_results + state_assert.expected))
signature    = sha256(json.dumps([scope, route, steps, anchors], ensure_ascii=False))
```

签名包含 `uiSelector.value` 和 `inputValue`，因此输入 20 字符和 21 字符
会得到不同签名，不会因整串相似度高而被误判。

### 4.2 判重规则

1. 签名完全相同 → 判重，保留 `quality_score` 高者。
2. 同一 `test_point_id`：
   - 输入值或预期锚点不同 → 保留，视为不同场景；
   - 两者都相同 → 判重。
3. 不同 `test_point_id` → 保留，保证覆盖矩阵每个测试点都有用例。
4. 无 `test_point_id`（项目级生成）→ 按签名和 route + 字段键判重，
   不再使用整串模糊相似度。
5. 模糊相似度只作为同桶内（同 route、同动作类型序列）的二次确认，
   阈值提高到 0.98，并且只记录 warning，不直接丢弃。

### 4.3 跨批次去重

推荐给 `TestCase` 增加 `scenario_key` 字段：

```sql
ALTER TABLE test_cases
  ADD COLUMN scenario_key VARCHAR(64) NULL,
  ADD INDEX ix_test_cases_scenario_key (scenario_key);
```

生成前查询同项目（需求版本模式下同 requirement）已有 `scenario_key`，
把已存在的签名传给质量闸；命中则跳过。手工新增、编辑、CSV 导入同样计算
`scenario_key`，至少在命中时返回提示。

若暂不做 schema 变更，可先按项目加载现有用例在 Python 中计算签名。
用例量在数千以内可以接受，后续再落库。

## 5. 代码改动清单

| 文件 | 改动 |
|---|---|
| `app/agent/quality_gate.py` | 新增 `score_case_detailed`、`scenario_signature`；重写 `_is_duplicate`；`run_quality_gate` 增加低分保留、签名去重、跨批次签名入参；`UiLanguageLinter` 返回带 code/severity 的结构化问题 |
| `app/service/generation_service.py` | 生成前加载已有签名；`_save_case` 写入 `quality_score`、`quality_breakdown`、`scenario_key`；保留旧调用签名 |
| `app/service/testcase_service.py` | `_rerun_gate` 写入 breakdown；手工新增/编辑/导入计算 `scenario_key` 并提示重复 |
| `app/service/workflow_service.py` | `_deterministic_review` 改用 `score_case_detailed`；warning 不进入扣分；修复 `_missing_required_fields` 按用例所在页面过滤 |
| `app/entity/models.py` | `TestCase` 增加 `quality_breakdown: Text`、`scenario_key: String(64)`（含索引） |
| `migrations/mysql/002_quality_gate.sql` | 新增上述两列和索引 |
| `app/evaluation/metrics.py` | `duplicate_rate` 改用 `scenario_signature`；增加 `nearDuplicateRate` 和分项得分统计 |
| `tests/test_quality_gate.py` | 更新既有断言，补充 20/21 字符、跨字段、跨批次、warning 不计分等用例 |

## 6. 兼容与迁移

- `run_quality_gate(cases)` 保持原签名；`GateResult` 只新增字段。
- `quality_score` 语义保持 0~100；旧数据 `quality_breakdown` 为空时，
  UI 回退展示 `lint_issues`。
- 新增配置项：
  - `QUALITY_GATE_DROP_LOW_SCORE`：默认 `0`（保留低分），可回退为旧行为；
  - `QUALITY_GATE_DEDUP_MODE`：`signature`（默认）/ `legacy`；
  - `QUALITY_GATE_CROSS_BATCH_DEDUP`：默认 `1`。
- SQLite 开发库不会自动加列，需在 `app/database.py` 的启动钩子里做
  轻量 `ALTER TABLE` 兼容，或在开发环境重建库。

## 7. 测试计划

单元测试：

1. 20 字符与 21 字符边界用例（无 test_point_id）→ 两条都保留。
2. 同一测试点、输入不同 → 保留；输入和预期都相同 → 一条。
3. 不同测试点、步骤相同 → 两条都保留。
4. 完全相同的两条用例 → 保留高分者。
5. 缺少 selector 只扣一次；warning 不影响分数。
6. 低分用例保留并标记 `needs_review`。
7. 跨批次重复 → 第二次生成跳过。
8. `duplicate_rate` 与质量闸判重结果一致。

服务与接口测试：

1. 生成两条边界用例，接口返回两条。
2. 手工编辑 `expectedResults` / `structuredSteps` 后真正落库
   （同时修复现有 camelCase 问题）。
3. CSV 导入重复用例时给出提示。

回归：`pytest -q` 全量通过；`npm run build` 通过。

## 8. 分阶段实施

**阶段一：打分与去重（不改表）**

- `score_case_detailed` + 低分保留
- 结构化签名去重
- warning 不扣分、缺 selector 只扣一次
- 更新单测

预计 0.5 天。

**阶段二：跨批次去重（加字段）**

- `scenario_key` 字段与索引
- 生成前加载已有签名
- 手工/导入也计算签名

预计 0.5 天。

**阶段三：工作流与指标对齐**

- `_deterministic_review` 改用新打分
- 修复必填字段误判
- `duplicate_rate` 与新签名对齐
- UI 展示 breakdown

预计 0.5 天，测试 0.5 天。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 低分不再丢弃，用例数量上升 | 默认按测试点保留最低一条，其余转 `needs_review`；列表默认过滤低分 |
| 签名键设计遗漏字段，导致误判重复 | 签名包含 route、动作、selector、输入值、预期锚点；模糊只做提示 |
| 跨批次去重误删不同需求版本的相同用例 | scope 中包含 `requirement_id`，不同版本不互相去重 |
| 旧库没有新列 | 提供 SQL 迁移和 SQLite 启动兼容；UI 对空值回退 |
| 分数权重需要调整 | 把权重集中为常量，配合人工标注集回归校准 |

## 10. 推荐默认配置

```text
QUALITY_GATE_DROP_LOW_SCORE=0
QUALITY_GATE_DEDUP_MODE=signature
QUALITY_GATE_CROSS_BATCH_DEDUP=1
QUALITY_GATE_LOW_SCORE_THRESHOLD=40
```

结论：保留现有"硬门槛 + 确定性规则"的骨架，把百分制扣分改成
可解释的分项评分，把整串模糊去重改成结构化签名去重，并把低分用例
转为待复核。这样改动范围可控，也不会破坏现有 API 和前端字段。
