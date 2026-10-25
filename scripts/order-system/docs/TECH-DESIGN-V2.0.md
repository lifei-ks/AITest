# 订单管理系统 技术设计文档（V2.0）

## 0 文档信息

| 项 | 内容 |
|---|---|
| 系统名称 | 订单管理系统（order-system） |
| 版本 | V2.0（二期） |
| 基线 | V1.0，见 `docs/TECH-DESIGN-V1.0.md` |
| 代码路径 | `scripts/order-system/backend`、`scripts/order-system/frontend` |
| 后端 | Spring Boot 3.4.5，Java 17+，端口 8080 |
| 前端 | Vue 3.5 + Element Plus 2.9 + Vite 6，端口 5273 |
| 访问地址 | <http://127.0.0.1:5273/orders/list> |
| 数据存储 | 服务端内存，4 条种子订单，可通过 `POST /api/demo/reset` 重置 |
| 交付内容 | 3 个功能：金额区间筛选、批量取消、订单统计 |

> 本文档面向二期增量测试：既描述功能实现，也给出接口契约、业务规则、边界与建议测试点。V1 已有的创建订单、状态流转、关键字/状态筛选、重置等能力保持不变。

---

## 1 二期变更概述

| 编号 | 功能 | 类型 | 主要变化 |
|---|---|---|---|
| F1 | 订单金额区间筛选 | 修改已有接口 + 列表页筛选 | `GET /api/orders` 增加可选 `minAmount` / `maxAmount` |
| F2 | 批量取消订单 | 新增接口 + 列表页批量操作 | 新增 `POST /api/orders/batch-cancel`，支持部分成功 |
| F3 | 订单统计看板 | 新增接口 + 新增页面 | 新增 `GET /api/orders/statistics` 和 `/orders/statistics` 页面 |

兼容性：

- F1 的两个查询参数都是可选的；不传时行为与 V1 完全一致。
- F2、F3 为新增接口，不影响已有接口。
- 没有删除接口，没有修改订单实体字段，没有改变现有状态机。
- 原有请求/响应信封、错误码、种子数据和重置接口保持兼容。

---

## 2 功能一：订单金额区间筛选

### 2.1 业务目标

列表页支持按订单金额区间筛选，并可与关键字、订单状态组合使用；区间为闭区间（包含边界）。

### 2.2 前端

订单列表页筛选栏新增「金额区间」：

- 最低金额输入框，placeholder：`最低金额`
- 最高金额输入框，placeholder：`最高金额`
- 两个输入框均允许为空
- 点击「查询」时一起提交
- 点击「重置」时清空关键字、状态、最低金额、最高金额

### 2.3 接口

`GET /api/orders` 新增两个可选查询参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| minAmount | BigDecimal | 否 | 空 | 最低金额，闭区间下界 |
| maxAmount | BigDecimal | 否 | 空 | 最高金额，闭区间上界 |

请求示例：

```http
GET /api/orders?page=1&pageSize=10&minAmount=100&maxAmount=200
```

响应仍然沿用 V1 的分页结构：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "total": 1,
    "page": 1,
    "pageSize": 10,
    "items": [
      {
        "id": 1001,
        "orderNo": "ORD20260101001",
        "receiverName": "张三",
        "amount": 199.00,
        "status": "CREATED",
        "statusLabel": "待支付"
      }
    ]
  }
}
```

### 2.4 业务规则与边界

1. `minAmount`、`maxAmount` 都是可选的，可以只传一个。
2. 区间为闭区间：金额等于下界或上界时保留。
3. `minAmount < 0` → `code=40001`，`message=最低金额不能小于0`。
4. `maxAmount < 0` → `code=40001`，`message=最高金额不能小于0`。
5. `minAmount > maxAmount` → `code=40001`，`message=最低金额不能大于最高金额`。
6. 非数字（如 `minAmount=abc`）→ `code=40000`，`message=参数 minAmount 格式不正确`。
7. 金额筛选在状态和关键字筛选之后执行，三个条件同时满足才返回。
8. 空字符串按未传处理；前端空输入不会拼接到请求参数。
9. 分页总数 `total` 是金额筛选后的总数，不是全部订单数。

### 2.5 种子数据示例

| 订单号 | 金额 | 状态 |
|---|---:|---|
| ORD20260101001 | 199.00 | 待支付 |
| ORD20260101002 | 88.50 | 已支付 |
| ORD20260101003 | 320.00 | 已发货 |
| ORD20260101004 | 45.00 | 已完成 |

- `minAmount=100&maxAmount=200` → 只剩 199.00，共 1 条
- `minAmount=200` → 只剩 320.00，共 1 条
- `maxAmount=50` → 只剩 45.00，共 1 条
- `minAmount=0` → 4 条全部返回
- `minAmount=300&maxAmount=100` → 返回 40001 错误

---

## 3 功能二：批量取消订单

### 3.1 业务目标

列表页支持勾选多条订单并批量取消。只处理待支付（`CREATED`）订单；允许部分成功，逐条返回处理结果，不因为一条失败而回滚整个批次。

### 3.2 前端

订单列表页新增：

- 表格勾选列
- 工具栏「批量取消」按钮，未勾选时禁用
- 按钮显示已选数量，例如「批量取消(2)」
- 提示文案：`批量取消仅处理待支付订单`
- 执行后提示成功/失败数量，并刷新列表

### 3.3 接口

```http
POST /api/orders/batch-cancel
Content-Type: application/json

{
  "ids": [1001, 1002, 9999]
}
```

请求约束：

- `ids` 不能为空 → `code=40000`，`message=请选择至少一个订单`
- `ids` 最多 50 个 → `code=40000`，`message=单次最多取消50个订单`
- `ids` 中所有元素都为空 → `code=40001`，`message=订单ID不能为空`
- 重复 ID 会去重，结果中的 `requested` 是原始数量，`unique` 是去重后数量

成功响应（允许部分失败，HTTP 200）：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "requested": 3,
    "unique": 3,
    "cancelled": 1,
    "failed": 2,
    "items": [
      {
        "id": 1001,
        "orderNo": "ORD20260101001",
        "success": true,
        "status": "CANCELLED",
        "statusLabel": "已取消",
        "message": "订单已取消"
      },
      {
        "id": 1002,
        "orderNo": "ORD20260101002",
        "success": false,
        "status": "PAID",
        "statusLabel": "已支付",
        "message": "当前状态不可取消，只有待支付订单可以取消"
      },
      {
        "id": 9999,
        "orderNo": "",
        "success": false,
        "status": "NOT_FOUND",
        "statusLabel": "不存在",
        "message": "订单不存在"
      }
    ]
  }
}
```

### 3.4 业务规则

1. 只有 `CREATED`（待支付）订单可以取消，复用 V1 的状态机规则：`CREATED -> CANCELLED`。
2. 已支付、已发货、已完成、已取消、已退款订单均不可再次取消。
3. 不存在的订单不抛 404，而是在 `items` 中返回 `success=false`、`status=NOT_FOUND`，便于批次继续处理。
4. 批次内允许部分成功，`cancelled + failed = unique`。
5. 成功取消的订单立即写回内存，后续查询/统计可见。
6. 不删除订单；取消只改变状态。
7. 空列表、超 50 条属于请求参数错误，返回 400；单条失败属于业务结果，仍返回 200。

### 3.5 建议测试场景

- 选择 1 条待支付订单 → 成功，状态变为已取消
- 选择 1 条已支付订单 → 失败，提示只有待支付订单可以取消
- 勾选待支付 + 已支付 → 部分成功，提示成功 1 条、失败 1 条
- 勾选不存在的 ID → 该项失败，其他项不受影响
- 不勾选直接点批量取消 → 前端阻止并提示
- 传空 `ids` → 40000
- 传 51 个 ID → 40000
- 传重复 ID → `unique` 小于 `requested`，不重复取消
- 批量取消后刷新列表 → 状态和操作列同步更新
- 批量取消后调用统计接口 → 已取消数量增加

---

## 4 功能三：订单统计看板

### 4.1 业务目标

提供订单数量、金额汇总和状态分布，作为列表之外的经营概览页面。

### 4.2 前端

新增页面：

- 路由：`/orders/statistics`
- 顶部导航新增「订单统计」
- 页面包含：
  - 订单总数
  - 订单总额
  - 平均金额
  - 状态分布表（6 种状态的数量、占比进度条）
  - 有效订单金额（不含已取消、已退款）
  - 「刷新统计」按钮

### 4.3 接口

```http
GET /api/orders/statistics
```

响应示例（种子数据）：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "totalOrders": 4,
    "totalAmount": 652.50,
    "validAmount": 652.50,
    "averageAmount": 163.13,
    "statusCounts": {
      "CREATED": 1,
      "PAID": 1,
      "SHIPPED": 1,
      "COMPLETED": 1,
      "CANCELLED": 0,
      "REFUNDED": 0
    },
    "statusLabels": {
      "CREATED": "待支付",
      "PAID": "已支付",
      "SHIPPED": "已发货",
      "COMPLETED": "已完成",
      "CANCELLED": "已取消",
      "REFUNDED": "已退款"
    }
  }
}
```

### 4.4 计算口径

| 字段 | 口径 |
|---|---|
| totalOrders | 当前内存中的全部订单数 |
| totalAmount | 全部订单金额之和，保留 2 位小数 |
| validAmount | 排除 `CANCELLED`、`REFUNDED` 后的金额之和 |
| averageAmount | `totalAmount / totalOrders`，保留 2 位小数，四舍五入 |
| statusCounts | 6 个状态固定返回，未出现的状态为 0 |
| statusLabels | 6 个状态对应的中文文案 |

### 4.5 建议测试场景

- 初始种子数据：4 条、总额 652.50、平均 163.13、四种状态各 1 条
- 取消 1 条待支付订单后刷新：`CREATED` 减 1、`CANCELLED` 加 1、有效金额减少 199.00
- 新建订单后刷新：总数、总额、平均金额、`CREATED` 数量增加
- 调用重置后刷新：恢复种子统计
- 页面无控制台错误，状态分布占比合计约 100%
- 金额展示统一 2 位小数

---

## 5 API 变更汇总

| 编号 | 接口 | V2 变化 | 兼容性 |
|---|---|---|---|
| A2 | `GET /api/orders` | 新增可选 `minAmount`、`maxAmount` | 不传参数时与 V1 一致 |
| A13 | `POST /api/orders/batch-cancel` | 新增 | 纯新增 |
| A14 | `GET /api/orders/statistics` | 新增 | 纯新增 |
| A1/A3~A12 | 其余接口 | 不变 | 完全兼容 |

新增错误响应：

| code | HTTP | message | 触发条件 |
|---|---|---|---|
| 40000 | 400 | 请选择至少一个订单 | 批量取消 ids 为空 |
| 40000 | 400 | 单次最多取消50个订单 | ids 超过 50 |
| 40000 | 400 | 参数 minAmount 格式不正确 | 金额参数非数字 |
| 40000 | 400 | 请求体格式不正确 | 批量取消 JSON 非法 |
| 40001 | 400 | 最低金额不能小于0 | minAmount 为负 |
| 40001 | 400 | 最高金额不能小于0 | maxAmount 为负 |
| 40001 | 400 | 最低金额不能大于最高金额 | 区间上下界颠倒 |
| 40001 | 400 | 订单ID不能为空 | ids 全部为 null |

---

## 6 前端改动

| 文件 | 变化 |
|---|---|
| `frontend/src/views/OrderList.vue` | 金额区间筛选；勾选列；批量取消按钮与结果提示 |
| `frontend/src/views/OrderStatistics.vue` | 新增统计看板页面 |
| `frontend/src/router/index.js` | 新增 `/orders/statistics` |
| `frontend/src/App.vue` | 顶部导航新增「订单统计」 |
| `frontend/src/api/order.js` | 新增 `batchCancelOrders`、`fetchStatistics` |

后端改动：

| 文件 | 变化 |
|---|---|
| `OrderController.java` | 列表新增金额参数；新增 batch-cancel、statistics 接口 |
| `OrderServiceImpl.java` | 金额区间校验/过滤；批量取消；统计计算 |
| `BatchCancelRequest.java` | 新增批量取消请求 DTO |
| `GlobalExceptionHandler.java` | 参数类型错误、请求体格式错误返回 400 |

---

## 7 数据与状态机

- `Order` 实体字段不变，没有新增数据库/内存字段。
- 订单仍存内存，重启恢复 4 条种子数据。
- 状态机不变：批量取消复用 `CREATED -> CANCELLED`，不新增状态。
- 但批量取消是一个新动作，AITest 会把它识别为新的动作证据（`CREATED -> CANCELLED:batchCancel`），这是正常的业务动作增量，不是新增状态。
- 统计接口只读，不修改订单状态。
- 金额筛选只读，不修改订单。

---

## 8 对 AITest 增量测试的预期

以 V1.0 代码分析快照为基线，切换到 V2.0 后，接口差异应至少包含：

| 变化 | 接口 |
|---|---|
| 新增 | `POST /api/orders/batch-cancel` |
| 新增 | `GET /api/orders/statistics` |
| 修改 | `GET /api/orders`，新增 `minAmount` / `maxAmount` 参数 |
| 新增动作 | `CREATED -> CANCELLED:batchCancel`，复用已有状态边 |
| 新增规则 | “请选择至少一个订单”“订单ID不能为空”等批量取消校验 |
| 未变化 | 创建、详情、修改、删除、支付、发货、完成、取消、退款、状态选项、重置 |

预期增量策略：

- 为 `batch-cancel` 生成正常、混合状态、空列表、超 50、重复 ID、订单不存在等用例。
- 为 `statistics` 生成种子统计、取消后统计、新建后统计、重置后统计等用例。
- 为 `GET /api/orders` 生成金额上下界、只传下界、只传上界、负数、最小值大于最大值、非数字、与状态组合等用例。
- 未变化的创建/状态流转接口默认复用 V1 历史用例。
- 回归推荐应覆盖：
  - 列表页查询/重置/分页用例（列表接口发生修改）
  - 列表页支付/取消/发货/退款/完成按钮用例（批量取消修改了同一页面交互）
  - 新建订单与关键字/状态筛选用例（共享列表数据和筛选逻辑）
- 统计接口是纯 API 接口，当前 AITest 若无法映射到前端页面，会生成 manual/待补充用例；统计页面本身可通过新增页面路由做 UI 测试。

---

## 9 建议二期测试点清单

### 9.1 金额区间筛选

1. 只填最低金额 100，返回金额 ≥100 的订单
2. 只填最高金额 50，返回金额 ≤50 的订单
3. 最低 100、最高 200，只返回 199.00 订单
4. 最低和最高相等 199，返回 199.00 订单
5. 最低 0，返回全部 4 条
6. 最低金额为负数，提示最低金额不能小于0
7. 最高金额为负数，提示最高金额不能小于0
8. 最低 300、最高 100，提示最低金额不能大于最高金额
9. 金额输入 abc，提示参数格式不正确
10. 金额区间 + 状态=已支付，只返回同时满足的订单
11. 金额区间 + 关键字，只返回同时满足的订单
12. 金额筛选后点重置，清空金额条件并恢复 4 条

### 9.2 批量取消

13. 勾选 1 条待支付订单批量取消，成功并变为已取消
14. 勾选 1 条已支付订单批量取消，失败并提示状态不可取消
15. 勾选待支付 + 已支付，部分成功并提示成功/失败数量
16. 勾选不存在的 ID，该项失败、其他项继续
17. 不勾选点批量取消，前端提示先选择订单
18. 提交空 ids，返回请选择至少一个订单
19. 提交 51 个 id，返回单次最多取消50个订单
20. 提交重复 id，只处理一次且 unique 正确
21. 批量取消后列表状态和操作列更新
22. 批量取消后统计接口已取消数量增加

### 9.3 订单统计

23. 种子数据统计总数 4、总额 652.50、平均 163.13
24. 状态分布六项齐全，未出现状态数量为 0
25. 取消一条待支付订单后，CREATED 减 1、CANCELLED 加 1
26. 新建一条订单后，总数/总额/平均金额/CREATED 增加
27. 有效金额不含已取消、已退款
28. 重置后统计恢复种子值
29. 统计页面刷新按钮重新拉取数据
30. 统计页面金额均为两位小数

---

## 10 启动与验证

### 10.1 后端

```bash
cd scripts/order-system/backend
mvn -DskipTests package
java -jar target/order-system-1.0.0.jar --server.port=8080
```

### 10.2 前端

```bash
cd scripts/order-system/frontend
npm install
npm run dev
```

访问：

- 订单列表：<http://127.0.0.1:5273/orders/list>
- 订单统计：<http://127.0.0.1:5273/orders/statistics>

### 10.3 快速验证

```bash
# 金额区间
curl 'http://127.0.0.1:8080/api/orders?minAmount=100&maxAmount=200'

# 非法区间
curl 'http://127.0.0.1:8080/api/orders?minAmount=300&maxAmount=100'

# 批量取消（1001 待支付，1002 已支付，9999 不存在）
curl -X POST http://127.0.0.1:8080/api/orders/batch-cancel \
  -H 'Content-Type: application/json' \
  -d '{"ids":[1001,1002,9999]}'

# 统计
curl http://127.0.0.1:8080/api/orders/statistics

# 恢复种子数据
curl -X POST http://127.0.0.1:8080/api/demo/reset
```

---

## 11 已知限制

- 数据存内存，后端重启后恢复种子数据，不保留测试期间新增/取消的订单。
- 金额筛选只支持订单金额，不支持日期、商品、收货人等区间筛选。
- 批量取消没有“全部成功或全部回滚”的事务语义，按条处理并返回部分成功。
- 统计不区分时间维度，没有按日/周/月统计。
- `averageAmount` 按全部订单计算，包含已取消/已退款；有效金额另字段展示。
- 当前没有账号与权限体系，批量取消和统计接口没有角色校验。
- 统计接口是聚合接口，暂不支持分页或按状态单独查询。
