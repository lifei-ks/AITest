"""被测系统自检：用 Playwright 走一遍完整业务闭环。

用途：
  1. 确认订单系统本身可正常操作（避免"AICase 跑失败其实是被测系统坏了"）；
  2. 产出的截图可直接放进 README；
  3. 顺带验证 Element Plus 两字按钮「支 付」的空格渲染，供选择器兜底参考。

运行前需先启动后端（8080）与前端（5273）。
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5273"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)


def button(text: str):
    """两字中文按钮会被 Element Plus 渲染成「支 付」，必须用 normalize-space 匹配。"""
    return f"xpath=//button[normalize-space(.)='{text}']"


def main() -> None:
    checks: list[tuple[str, bool]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ---------- 1. 列表页 ----------
        page.goto(f"{BASE}/orders/list", wait_until="networkidle")
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        checks.append(("列表页展示 4 条种子订单", "共 4 条订单" in body))
        checks.append(("首行是待支付订单", "待支付" in body))
        page.screenshot(path=str(SHOTS / "order-list.png"), full_page=True)

        # ---------- 2. 支付流转：待支付 -> 已支付 ----------
        page.locator(button("支付")).first.click()
        page.wait_for_timeout(900)
        body = page.inner_text("body")
        checks.append(("点击支付后出现成功提示", "支付成功" in body))
        checks.append(("首行状态变为已支付", "已支付" in body))
        page.screenshot(path=str(SHOTS / "order-paid.png"), full_page=True)

        # ---------- 3. 新建页：必填校验 ----------
        page.goto(f"{BASE}/orders/create", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.locator(button("提交订单")).click()
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        checks.append(("空表单提交触发必填校验", "收货人不能为空" in body))
        page.screenshot(path=str(SHOTS / "order-create-validation.png"), full_page=True)

        # ---------- 4. 新建页：正常提交并跳转列表 ----------
        page.fill("[placeholder='请输入收货人']", "钱七")
        page.fill("[placeholder='请输入手机号']", "13900000007")
        page.fill("[placeholder='请输入收货地址']", "杭州市西湖区文三路 100 号")
        page.fill("[placeholder='请输入订单金额']", "66.60")
        page.fill("[placeholder='请输入备注（选填）']", "周末前送达")
        page.screenshot(path=str(SHOTS / "order-create-filled.png"), full_page=True)

        page.locator(button("提交订单")).click()
        page.wait_for_timeout(1200)
        checks.append(("提交成功后跳到列表页", "/orders/list" in page.url))
        body = page.inner_text("body")
        checks.append(("列表页出现新建订单", "钱七" in body))
        first_row = page.locator("tbody tr").first.inner_text()
        checks.append(("新订单排在表格第一行", "钱七" in first_row))
        page.screenshot(path=str(SHOTS / "order-created.png"), full_page=True)

        browser.close()

    print("\n=== 被测系统自检 ===")
    ok = 0
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
        ok += 1 if passed else 0
    print(f"\n{ok}/{len(checks)} 通过，截图目录：{SHOTS}")


if __name__ == "__main__":
    main()
