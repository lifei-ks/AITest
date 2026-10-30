# 分层断言 URL 短路问题修复

- 日期：2026-10-30
- 范围：`app/agent/execution_agent.py` 的 `_do_assert`
- 验证：后端 188 passed

## 1. 问题

原实现：

```python
if re.search(r"URL|跳转|路由|页面地址|/[\w/]+", expected):
    path = ...
    if path in page.url:
        return True, "passed", "URL 命中"
```

当 expected 同时包含 URL 和文案：

```json
{ "expected": "跳转到 /orders 并提示「订单创建成功」" }
```

URL 命中后会直接返回 passed，不再检查「订单创建成功」是否真的出现，存在假绿风险。

## 2. 修复方案

`_do_assert` 改为“收集所有断言维度，全部满足才 passed”：

```text
1. 解析 expected 里的 URL 路径
2. 解析 expected 里的「」文案锚点
3. URL 存在 → 必须命中
4. 文案锚点存在 → 必须全部命中
5. 所有存在的维度都通过 → passed
6. 任一维度失败 → failed
7. 没有任何可验证维度 → skipped
```

判定表：

| URL | 文案锚点 | 结果 |
|---|---|---|
| 有，命中 | 有，命中 | passed |
| 有，命中 | 有，缺失 | failed（原实现会 passed） |
| 有，不命中 | 有，命中 | failed（原实现可能继续检查文案） |
| 有，不命中 | 无 | failed（原实现 skipped） |
| 无 | 有，命中 | passed |
| 无 | 有，缺失 | failed |
| 无 | 无 | skipped |

## 3. URL 路径匹配也做了加固

原实现用：

```python
path in page.url
```

`/orders` 会错误匹配 `/orders/new`。

新实现 `_url_path_matches()`：

- 只比较 `urlparse(page.url).path`，忽略 query；
- 路径按段精确匹配，`/orders` 不再匹配 `/orders/new`；
- 支持占位路径：

```text
/orders/{id}     匹配 /orders/123
/orders/{id}/pay 匹配 /orders/123/pay
```

- URL 路径检测要求 `/` 后面是字母、下划线或 `{`，避免把 `20/21 字符` 里的 `/21` 当成 URL。

## 4. 例子

用例：

```json
{ "expected": "跳转到 /orders 并提示「订单创建成功」" }
```

页面：

```text
URL:  http://localhost/orders
Body: 暂无数据
```

结果：

```json
{
  "status": "failed",
  "detail": "文本锚点缺失: ['订单创建成功']"
}
```

URL 命中但文案缺失，不再误判为 passed。

## 5. 代码改动

- `app/agent/execution_agent.py`
  - 新增 `URL_HINT_RE`、`URL_PATH_RE`
  - 重写 `_do_assert`
  - 新增 `_url_path_matches`
- `tests/test_execution_assertions.py`
  - URL + 文案同时通过
  - URL 命中但文案缺失 → failed
  - URL 不命中但文案通过 → failed
  - URL 单独不命中 → failed
  - 无锚点 → skipped
  - `20/21` 不被当成 URL
  - `/orders` 不匹配 `/orders/new`
  - `/orders/{id}` 匹配 `/orders/123`

## 6. 后续建议

当前 expected 仍是自然语言，URL 和文案是从文本里解析出来的。更稳的方案是把断言结构化成：

```json
{
  "type": "state_assert",
  "url": "/orders",
  "anchor": "订单创建成功"
}
```

执行器直接按字段验证，不再依赖正则从自然语言里提取。这需要同步修改生成器、质量闸、审核 Agent 和 prompt，建议作为下一步。
