# 长文本输入导致 AI 审核误判的修复

- 日期：2026-10-30
- 场景：输入框上限 100 字符，页面宽度只能显示前十几个字符
- 问题：视觉模型从截图判断输入不完整，误判 suspect
- 验证：后端 194 passed

## 1. 根因

输入框只渲染可视区域，长文本会滚动/截断。

AI 审核此前只能看到：

```text
2. [input] 输入收货人（结果：passed）
```

它不知道实际填了多少字符，只能看截图；截图只显示一部分，就判“输入不完整”。

## 2. 解决原则

```text
值用 DOM 证据，截图只验证行为和提示。
```

执行时读取输入框真实值：

```python
loc.fill(expected_value)
actual = loc.input_value()
```

记录：

```json
{
  "inputValueLength": 100,
  "actualValueLength": 100,
  "inputValueHash": "...",
  "actualValueHash": "...",
  "valueMatch": true,
  "actualValuePreview": "aaaaaaaaaaaaaaaaaaaa...aaaaaaaaaaaaaaaaaaaa"
}
```

## 3. 数据流

```text
Playwright fill()
  → loc.input_value()
  → _input_evidence()
  → step_results[].evidence
  → ExecutionStep.evidence（JSON）
  → _steps_for_review()
  → review_agent._steps_text()
  → _build_prompt()
  → 视觉模型
```

## 4. 代码改动

| 文件 | 改动 |
|---|---|
| `app/agent/execution_agent.py` | 新增 `_input_evidence()`；input 步骤 fill 后读取 `input_value()`；证据写入 step_results |
| `app/entity/models.py` | `ExecutionStep` 新增 `evidence` TEXT |
| `app/database.py` | SQLite 启动时给 `execution_steps` 补 `evidence` 列 |
| `migrations/mysql/004_execution_step_evidence.sql` | MySQL 增量迁移 |
| `app/service/execution_service.py` | 保存/读取步骤 evidence |
| `app/routers/executions.py` | 步骤接口返回 evidence |
| `app/agent/review_agent.py` | `_steps_text()` 拼入长度/一致/预览；prompt 增加长文本规则 |
| `tests/test_execution_evidence.py` | 长文本、截断、短值、敏感字段、prompt 证据测试 |

## 5. Prompt 新规则

```text
输入框只显示可视区域，长文本被截断是正常现象。
若步骤记录提供「输入长度 / DOM实际长度 / 值一致」，
以 DOM 证据为准，不要因为截图中输入框只显示部分字符而判 suspect；
只有值一致=false，或校验提示、页面状态与预期不符时，才判 suspect。
```

## 6. 示例

用例输入 100 个 `a`，页面只显示前 20 个。

执行证据：

```json
{
  "inputValueLength": 100,
  "actualValueLength": 100,
  "valueMatch": true,
  "actualValuePreview": "aaaaaaaaaaaaaaaaaaaa...aaaaaaaaaaaaaaaaaaaa"
}
```

AI prompt 中出现：

```text
2. [input] 输入收货人（结果：passed）
   输入长度：100
   DOM实际长度：100
   值一致：True
   值预览：aaaaaaaaaaaaaaaaaaaa...aaaaaaaaaaaaaaaaaaaa
```

审核结论：

```text
截图显示不全属于输入框可视区域限制；
DOM 证据显示实际长度 100、值一致 true；
页面提示「保存成功」；
→ ok。
```

如果实际长度是 99：

```json
{
  "inputValueLength": 100,
  "actualValueLength": 99,
  "valueMatch": false
}
```

审核结论：

```text
suspect：输入值被截断，实际长度 99，预期 100。
```

## 7. 敏感字段

密码、pwd 等字段不记录明文和哈希：

```json
{
  "inputValueLength": 16,
  "actualValueLength": 16,
  "valueMatch": true,
  "sensitive": true
}
```

prompt 中只显示：

```text
输入长度：16；DOM实际长度：16；值一致：True；敏感字段：未记录明文
```

## 8. 验证

- 100 字符输入 → `valueMatch=true`，AI 不再因截图截断判 suspect；
- 99/100 字符不一致 → `valueMatch=false`，可以准确判异常；
- 短文本不显示省略号；
- 敏感字段不记录明文/哈希；
- prompt 中包含 DOM 证据和长文本规则；
- 全量后端 194 passed。

## 9. 当前限制

- `input_value()` 适用于标准 input/textarea；contenteditable 需要改用 `inner_text()`。
- 如果页面在 blur/submit 后二次格式化或截断，当前证据只反映输入时状态，可以再补一次提交后的 DOM 读取。
- 老执行记录没有 evidence，重新执行后才会产生。
- MySQL 需要手动执行 `004_execution_step_evidence.sql`；SQLite 启动时自动补列。
