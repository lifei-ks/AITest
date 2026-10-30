# 视觉审核图片预算与两阶段复核

- 日期：2026-10-30
- 目标：在成本、延迟和证据覆盖之间取平衡
- 验证：后端 198 passed

## 1. 问题

原来固定最多 6 张截图：

- 长用例的中间步骤可能看不到；
- 可疑原因如果是“证据不足”，6 张图无法确认；
- 全部步骤都给又会造成图片 token 成本和延迟过高。

## 2. 图片预算

| 场景 | 默认上限 |
|---|---:|
| 普通执行 | 6 |
| 有失败步骤 | 10 |
| 二阶段补图 | 最多 12 |

配置项：

```env
VISION_MAX_IMAGES=6
VISION_MAX_IMAGES_FAILED=10
VISION_TWO_PASS_REVIEW=true
```

## 3. 选图优先级

`_pick_images()`：

```text
失败步骤 after + before
→ 最后一步 after
→ 所有 state_assert after
→ 第一步 after
→ 剩余步骤 after 补足
```

正常步骤只选 after，不选 before；失败步骤才同时选 before/after。

## 4. 两阶段复核

第一轮：

```text
按普通预算选 6 张（失败场景 10 张）
→ 调 VLM
```

触发第二轮的条件：

```text
第一轮 verdict = suspect
且没有具体 issues
且没有任一张图片判 suspect
且还有未发送的截图
```

这说明第一轮更像“证据不足”，而不是“明确异常”。

第二轮：

```text
最多选 12 张（包含第一轮图片 + 剩余图片）
→ 再调 VLM
→ 第二轮结果作为最终结果
```

如果第一轮已经发现具体问题或可疑图片，不再补图，避免多花一次调用。

## 5. 审核覆盖度

审核结果新增：

```json
{
  "reviewPasses": 2,
  "totalSteps": 8,
  "reviewedSteps": [1, 2, 4, 8],
  "imageCoverage": "4/8"
}
```

- `reviewPasses`：实际审核轮次；
- `totalSteps`：用例总步骤数；
- `reviewedSteps`：截图覆盖到的步骤；
- `imageCoverage`：覆盖比例。

这些信息写入 `ExecutionReview.review_meta`，接口通过 `reviewMeta` 返回。

## 6. Prompt 规则

```text
只根据提供的截图判断；没有提供截图的步骤，不要仅因“没有看到”就判 suspect；
但关键断言步骤或失败步骤没有截图时，应判 suspect。
```

## 7. 代码改动

| 文件 | 改动 |
|---|---|
| `app/agent/review_agent.py` | 动态图片预算、两阶段复核、覆盖度元数据、prompt 规则 |
| `app/config.py` | 新增视觉审核预算配置 |
| `.env.example` | 新增环境变量示例 |
| `app/entity/models.py` | `ExecutionReview.review_meta` |
| `app/database.py` | SQLite 自动补 `review_meta` 列 |
| `migrations/mysql/005_review_meta.sql` | MySQL 增量迁移 |
| `app/service/execution_service.py` | 保存/返回审核元数据 |
| `tests/test_review_image_budget.py` | 选图优先级、二阶段补图、失败场景预算测试 |

## 8. 验证

- 普通 8 步用例第一轮发 6 张；
- 第一轮证据不足 → 第二轮发 8 张（最多 12 张）；
- 第一轮已有具体 issue → 不触发第二轮；
- 有失败步骤时预算从 6 放宽到 10；
- 审核结果包含 `reviewPasses` / `imageCoverage`；
- 全量后端 198 passed。

## 9. 当前限制

- 两阶段复核只处理“suspect 且没有明确问题”的证据不足场景；
- 第二轮重新发送第一轮图片，会多消耗一次图片 token；
- 图片数量上限仍由模型上下文和成本决定，建议按模型实测调整；
- 老执行记录没有 `review_meta`，重新审核后才有。
