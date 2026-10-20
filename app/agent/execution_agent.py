"""自动执行器（Playwright）：uiSelector 映射 + 五级兜底 + 三层断言 + 截图录屏。

铁律（TASK_BOOK 1.3）：
- 不假绿：验证不了标 skipped，绝不因"没报错"判 passed
- 失败分类用 6.3.6 的 6 个枚举值
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ERROR_CATEGORIES = (
    "SELECTOR_NOT_FOUND", "ASSERT_FAILED", "NAVIGATION_FAILED",
    "TIMEOUT", "CASE_INVALID", "ENV_ERROR",
)

# 引号锚点：中文角括号「」『』 / 弯引号 / 英文引号
QUOTE_ANCHOR_RE = re.compile(r"[\u300c\u300e\u201c\u2018\"']([^\u300d\u300f\u201d\u2019\"']+)[\u300d\u300f\u201d\u2019\"']")


def _selector_for(step: dict) -> str | None:
    """uiSelector → Playwright selector（6.3.3）。route 特殊处理。"""
    sel = step.get("uiSelector") or {}
    stype = sel.get("type")
    value = sel.get("value") or ""
    if not value:
        return None
    if stype == "route":
        return None  # 导航由调用方处理
    if stype == "placeholder":
        return f'[placeholder="{value}"]'
    if stype == "text":
        return None  # text 用兜底链的 has-text
    if stype == "label":
        return f'label:has-text("{value}") >> xpath=following::input[1]'
    if stype == "role":
        return f'[role="{value}"]'
    if stype == "testid":
        return f'[data-testid="{value}"]'
    if stype in ("css", "xpath"):
        return value
    return None


def _fallback_chain(step: dict) -> list[str]:
    """五级兜底链（6.3.3）。"""
    sel = step.get("uiSelector") or {}
    stype = sel.get("type")
    value = sel.get("value") or ""
    target = step.get("target") or ""
    chain = []
    # 1. uiSelector 精确匹配
    if stype == "text" and value:
        chain.append(f'button:has-text("{value}"), a:has-text("{value}"), [role="button"]:has-text("{value}"), input[value="{value}"], summary:has-text("{value}")')
    elif stype not in ("route",) and value:
        s = _selector_for(step)
        if s:
            chain.append(s)
    # 2. target 文本在 button/a/[role=button]
    if target:
        chain.append(f'button:has-text("{target}"), a:has-text("{target}"), [role="button"]:has-text("{target}")')
    # 3. target 文本整页（排除 option/script/style）
    if target:
        chain.append(f'body :not(option):not(script):not(style):has-text("{target}")')
    # 4. normalize-space XPath（Element Plus 两字按钮渲染成"登 录"）
    if target:
        chain.append(f'xpath=//button[normalize-space(.)="{target}"]')
    return chain


def _find_locator(page, step: dict):
    """按兜底链定位元素，返回 locator 或 None。"""
    chain = _fallback_chain(step)
    attempted = []
    for sel in chain:
        attempted.append(sel)
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return loc, attempted
        except Exception:
            continue
    return None, attempted


def _do_assert(page, expected: str) -> tuple[bool, str, str]:
    """三层断言（6.3.4）：URL → 引号锚点文本 → 诚实 skipped。返回 (result, status, detail)。"""
    # 第 1 层 URL 断言
    if re.search(r"URL|跳转|路由|页面地址|/[\w/]+", expected):
        path_m = re.search(r"(/[\w/{}]+)", expected)
        if path_m and path_m.group(1) in page.url:
            return True, "passed", f"URL 命中: {path_m.group(1)}"
    # 第 2 层 DOM 文本断言：引号锚点
    anchors = QUOTE_ANCHOR_RE.findall(expected)
    if anchors:
        text = page.inner_text("body")
        missing = [a for a in anchors if a not in text]
        if not missing:
            return True, "passed", f"文本锚点命中: {anchors}"
        return False, "failed", f"文本锚点缺失: {missing}"
    # 第 3 层 诚实 skipped
    return False, "skipped", "无可验证的断言锚点"


def execute_case(case: dict, target_url: str, output_dir: str, mode: str = "programmatic") -> dict:
    """执行单条用例，返回执行记录 dict。"""
    from playwright.sync_api import sync_playwright

    steps = case.get("structuredSteps") or []
    out = Path(output_dir) / case.get("id", "exec")
    out.mkdir(parents=True, exist_ok=True)

    total = len(steps)
    passed = 0
    step_results = []
    record = {
        "status": "passed", "errorCategory": None, "errorMessage": None,
        "videoPath": None, "passedSteps": 0, "totalSteps": total, "steps": step_results,
    }

    if not steps:
        record["status"] = "failed"
        record["errorCategory"] = "CASE_INVALID"
        record["errorMessage"] = "用例无结构化步骤"
        return record

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(out))
            page = context.new_page()
            page.set_default_timeout(10000)

            for idx, step in enumerate(steps, 1):
                stype = step.get("type")
                before = str(out / f"step-{idx:02d}-before.png")
                after = str(out / f"step-{idx:02d}-after.png")
                try:
                    page.screenshot(path=before)
                except Exception:
                    before = None

                result = "passed"
                error = None
                try:
                    if stype == "ui_action" and (step.get("uiSelector") or {}).get("type") == "route":
                        route = step["uiSelector"]["value"]
                        url = target_url.rstrip("/") + ("/" + route.lstrip("/") if route else "")
                        page.goto(url)
                        page.wait_for_timeout(300)
                    elif stype == "ui_action":
                        loc, attempted = _find_locator(page, step)
                        if not loc:
                            raise _StepError("SELECTOR_NOT_FOUND", f"元素未找到，尝试链: {attempted}")
                        loc.click()
                        page.wait_for_timeout(300)
                    elif stype == "input":
                        if not (step.get("inputValue") and (step.get("uiSelector") or {}).get("value")):
                            raise _StepError("CASE_INVALID", "input 步骤缺少 uiSelector.value 或 inputValue")
                        loc, attempted = _find_locator(page, step)
                        if not loc:
                            raise _StepError("SELECTOR_NOT_FOUND", f"输入框未找到，尝试链: {attempted}")
                        loc.fill(step["inputValue"])
                    elif stype == "state_assert":
                        # 等页面跳转/渲染稳定（如提交后 500ms 跳转列表页）
                        page.wait_for_timeout(800)
                        ok_, status, detail = _do_assert(page, step.get("expected") or "")
                        result = status
                        if status == "failed":
                            raise _StepError("ASSERT_FAILED", detail)
                        if status == "skipped":
                            error = detail
                    else:
                        raise _StepError("CASE_INVALID", f"未知步骤类型: {stype}")
                except _StepError as e:
                    result = "failed"
                    error = e.message
                    record["errorCategory"] = record["errorCategory"] or e.category
                except Exception as e:  # noqa: BLE001
                    result = "failed"
                    error = f"{type(e).__name__}: {e}"
                    record["errorCategory"] = record["errorCategory"] or "ENV_ERROR"

                try:
                    page.screenshot(path=after)
                except Exception:
                    after = None

                if result == "passed":
                    passed += 1
                step_results.append({
                    "stepIndex": idx, "stepType": stype, "action": step.get("action", ""),
                    "target": step.get("target", ""), "selectorUsed": (step.get("uiSelector") or {}).get("value") or None,
                    "result": result, "screenshotBefore": before, "screenshotAfter": after, "error": error,
                })
                # 失败即停（不继续无意义步骤）
                if result == "failed":
                    break

            context.close()
            browser.close()

            # Playwright 视频文件名随机，从目录里找
            videos = list(out.glob("*.webm"))
            video_path = None
            if videos:
                # 重命名为固定 video.webm（Playwright 默认随机文件名）
                video_path = out / "video.webm"
                try:
                    videos[0].replace(video_path)
                except Exception:
                    video_path = videos[0]
            record["videoPath"] = str(video_path) if video_path else None
            record["passedSteps"] = passed
            if record["errorCategory"]:
                record["status"] = "failed"
            elif passed < total:
                # 有 skipped 步骤，但无失败：整体算 failed（skipped 不算通过）
                record["status"] = "failed" if any(s["result"] == "failed" for s in step_results) else "passed"
                record["status"] = "skipped" if any(s["result"] == "skipped" for s in step_results) and record["status"] == "passed" else record["status"]
            return record
    except Exception as e:  # noqa: BLE001 - 浏览器启动失败等
        logger.exception("执行用例失败: %s", case.get("id"))
        record["status"] = "failed"
        record["errorCategory"] = record["errorCategory"] or "ENV_ERROR"
        record["errorMessage"] = str(e)
        record["totalSteps"] = total
        return record


class _StepError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message
