# 订单管理系统（AICase 被测靶站）

一个可运行的最小订单管理系统，用 **Spring Boot 3.4 + Vue 3** 实现，专门用来给 AICase 当被测系统：
代码结构、状态流转、表单校验、页面文案都按「能被静态分析、能被 Playwright 自动执行」的方式写。

> 当前代码为 **V2.0**：在 V1 基础上新增「金额区间筛选」「批量取消」「订单统计」三个功能。
> 二期技术文档见 [`docs/TECH-DESIGN-V2.0.md`](docs/TECH-DESIGN-V2.0.md)。

## 一、快速启动

### 后端（8080）

```bash
cd backend
mvn -DskipTests package
java -jar target/order-system-1.0.0.jar --server.port=8080
```

首次启动会自动写入 4 条种子订单，覆盖「待支付 / 已支付 / 已发货 / 已完成」四种状态。

### 前端（5273）

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 **http://127.0.0.1:5273/orders/list**

> `/api` 由 Vite 代理到 `127.0.0.1:8080`，因此前端与后端同源，自动执行不需要处理跨域。

### 自检

```bash
python scripts/check_ui.py
```

会用 Playwright 走一遍「列表 → 支付 → 空表单校验 → 新建 → 跳转列表」，输出 8 项检查结果，并把截图存到 `docs/shots/`。

## 二、功能与页面

| 路由 | 页面 | 内容 |
|---|---|---|
| `/orders/list` | 订单列表 | 关键字/状态/金额区间筛选、分页、勾选批量取消、按状态显示可执行操作 |
| `/orders/create` | 新建订单 | 5 字段表单：收货人、手机号、收货地址、订单金额、备注 |
| `/orders/statistics` | 订单统计 | 订单总数、总额、平均金额、状态分布、有效金额 |

订单状态与操作：

```
CREATED 待支付 ──支付──> PAID 已支付 ──发货──> SHIPPED 已发货 ──完成──> COMPLETED 已完成
   │                        │
   └──取消──> CANCELLED     └──退款──> REFUNDED
```

共 5 条合法流转，终态为 已完成 / 已取消 / 已退款。

## 三、接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/orders` | 创建订单（初始状态待支付） |
| GET | `/api/orders` | 分页列表，参数 `page` / `pageSize` / `status` / `keyword` / `minAmount` / `maxAmount` |
| GET | `/api/orders/status-options` | 状态下拉选项 |
| GET | `/api/orders/{id}` | 详情 |
| PUT | `/api/orders/{id}` | 修改收货信息（已完成/已取消不可改） |
| DELETE | `/api/orders/{id}` | 删除 |
| POST | `/api/orders/{id}/pay` | 支付（仅待支付） |
| POST | `/api/orders/{id}/ship` | 发货（仅已支付） |
| POST | `/api/orders/{id}/complete` | 完成（仅已发货） |
| POST | `/api/orders/{id}/cancel` | 取消（仅待支付） |
| POST | `/api/orders/{id}/refund` | 退款（仅已支付） |
| POST | `/api/orders/batch-cancel` | 批量取消待支付订单，允许部分成功 |
| GET | `/api/orders/statistics` | 订单统计：总数、总额、平均金额、状态分布 |
| POST | `/api/demo/reset` | 恢复 4 条种子订单（测试环境用） |

统一响应：`{ "code": 0, "message": "ok", "data": ... }`；业务失败 `code = 40001`，参数校验失败 `code = 40000`。

## 四、为什么这样写（给自动化测试留的接口）

这一节是给 AICase 用的，说明每条设计对应哪类测试能力。

**1. 状态机有明确的代码证据**
五个动作都在 `OrderServiceImpl` 里，统一形态：

```java
public Order ship(Long id) {
    Order order = getById(id);
    if (!"PAID".equals(order.getStatus().name())) {
        throw new BizException("当前状态不可发货，只有已支付订单可以发货");
    }
    order.setStatus(OrderStatus.SHIPPED);
    return orderRepository.save(order);
}
```

`setStatus(...)` 是状态写入点，前面那个 `if (!"X".equals(...)) throw` 是来源态守卫。
静态分析据此可以推出 5 条 `from → to`，每条都能指到 `OrderServiceImpl.java` 的具体行，所以全部 `verified`。

**2. 实体带 `@Entity`，字段带校验注解**
`Order` 有 10 个字段和 9 条约束（必填、长度、手机号正则、金额下限），分析器能提取出字段名与规则，生成用例时用来构造正例/反例/边界值。

**3. 前端表单有 label + placeholder + rules**
新建页的 5 个字段都有 `label`、`placeholder`、`:rules` 三件套，分析器能提取出 `uiSelector`（placeholder 型），执行器直接用 `[placeholder='请输入收货人']` 定位。

**4. 提示文案全部是字面量**
`ElMessage.success('支付成功')` 这种写法（而不是传变量）让分析器能提取到 6 条可锚定文案，`state_assert` 才有东西可断言。

**5. 提交成功后跳列表，新订单在第一行**
创建成功后立即 `router.push('/orders/list')`，列表按创建时间倒序，新订单必然在最前。这样「新建 → 在列表对新订单支付」的闭环用例才成立。

**6. 状态 tag 与按钮按状态显示**
列表里已完成的订单不显示操作按钮（显示 `—`），避免生成"对已完成订单点支付"这种执行时必然失败的步骤。

## 五、接入 AICase

1. 把项目路径（`D:/javacode/AICaseTest-main (1)/order-system`）填进 AICase 的项目「源码路径」；
2. 目标 URL 填 `http://127.0.0.1:5273`；
3. 先跑「代码分析」，预期得到：

```
端点 12 个 · 实体 Order(10 字段) · 枚举 OrderStatus(6 值) · 业务规则 6 条
前端路由 3 条 · 表单 2 个（新建订单含 5 字段） · 选择器 17 个 · 提示文案 6 条
状态机 1 个，5 条转换，全部 verified，置信度 1.0
```

4. 再跑「生成用例」→「批量执行」。

> 执行前建议调一次 `POST /api/demo/reset`，让订单回到确定的初始状态。

## 六、两个刻意的真实特征

**① 两字按钮会被渲染成「支 付」**
Element Plus 默认对两个中文字符的按钮插入空格，所以 `<el-button>支付</el-button>` 实际渲染文本是 `支 付`。
这是真实项目的常见情况，**刻意保留**，用来验证执行器的 `normalize-space()` 兜底能力。
执行器若只用 `text=支付` 会定位失败，改用 `//button[normalize-space(.)='支付']` 即可。

**② 数据存内存，重启即重置**
不引入数据库，是为了让被测系统零依赖启动、每次测试都有干净数据。
代价是重启后新建的订单会丢失——这对测试场景反而是优点。

## 七、目录结构

```
order-system/
├── backend/
│   ├── pom.xml
│   └── src/main/java/com/order/
│       ├── OrderSystemApplication.java
│       ├── common/          # 统一响应、业务异常、全局异常处理
│       ├── config/          # CORS
│       ├── controller/      # OrderController / DemoController
│       ├── dto/             # CreateOrderRequest / UpdateOrderRequest
│       ├── entity/          # Order / OrderStatus
│       ├── repository/      # 内存存储 + 种子数据
│       └── service/         # OrderServiceImpl（状态机全部在这里）
├── frontend/
│   ├── vite.config.js       # 端口 5273 + /api 代理
│   └── src/
│       ├── router/index.js
│       ├── api/             # request.js / order.js
│       └── views/           # OrderList.vue / OrderCreate.vue
├── scripts/check_ui.py      # 8 项业务闭环自检
└── docs/shots/              # 自检产出的页面截图
```

## 八、环境要求

- JDK 17+（本机 21 已验证）
- Maven 3.8+
- Node 18+（本机 22 已验证）
- Python 3.10+（仅自检脚本需要，且需 `playwright` 与 `playwright install chromium`）
