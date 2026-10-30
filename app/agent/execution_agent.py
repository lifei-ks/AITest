"""自动执行器（Playwright）：uiSelector 映射 + 五级兜底 + 三层断言 + 截图录屏。

铁律（TASK_BOOK 1.3）：
- 不假绿：验证不了标 skipped，绝不因"没报错"判 passed
- 失败分类用 6.3.6 的 6 个枚举值
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.common.execution_enums import ERROR_CATEGORIES

logger = logging.getLogger(__name__)

# 引号锚点：中文角括号「」『』 / 弯引号 / 英文引号
QUOTE_ANCHOR_RE = re.compile(r"[\u300c\u300e\u201c\u2018\"']([^\u300d\u300f\u201d\u2019\"']+)[\u300d\u300f\u201d\u2019\"']")
REFRESH_RE = re.compile(r"刷新页面|刷新浏览器|reload", re.IGNORECASE)
# 断言里的 URL 路径：必须是以字母/下划线/花括号开头的路由，
# 避免把“20/21 字符”里的 /21 误判成 URL。
URL_HINT_RE = re.compile(r"URL|跳转|路由|页面地址", re.IGNORECASE)
URL_PATH_RE = re.compile(r"(?<![\w])/(?=[A-Za-z_{])[\w/{}.-]+")
# 运行时变量：${name} / ${name+1} / ${name-1}
VARIABLE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:([+-])(\d+))?\}")
VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _resolve_variables(value, variables: dict[str, str]) -> str:
    """替换 ${var} 和简单整数加减；变量缺失或非数字时抛业务步骤错误，不静默跳过。"""
    if not value or "${" not in str(value):
        return value
    text = str(value)

    def _repl(match: re.Match) -> str:
        name, op, delta = match.group(1), match.group(2), match.group(3)
        if name not in variables:
            raise _StepError("CASE_INVALID", f"运行时变量未捕获: {name}")
        raw = variables[name]
        if not op:
            return str(raw)
        try:
            base = int(str(raw).strip())
            number = int(delta)
        except (TypeError, ValueError):
            raise _StepError("CASE_INVALID", f"变量 {name}={raw!r} 不是整数，无法进行 {op}{delta}") from None
        return str(base + number if op == "+" else base - number)

    return VARIABLE_RE.sub(_repl, text)


def _input_evidence(expected: str, actual: str, sensitive: bool = False) -> dict:
    """把输入框真实值做成可核对证据，避免 AI 从截图判断长文本是否完整。"""
    expected = str(expected or "")
    actual = str(actual or "")
    if sensitive:
        return {
            "inputValueLength": len(expected),
            "actualValueLength": len(actual),
            "valueMatch": actual == expected,
            "sensitive": True,
        }
    preview = actual.replace("\n", " ").replace("\r", " ")
    if len(preview) > 40:
        preview = preview[:20] + "..." + preview[-20:]
    return {
        "inputValueLength": len(expected),
        "actualValueLength": len(actual),
        "inputValueHash": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "actualValueHash": hashlib.sha256(actual.encode("utf-8")).hexdigest(),
        "valueMatch": actual == expected,
        "actualValuePreview": preview,
    }


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
    # 2.1 Element Plus 下拉选项（订单状态等 select 展开后渲染到 body 的 popper 中）
    if target:
        chain.append(
            f'[role="option"]:has-text("{target}"), '
            f'.el-select-dropdown__item:has-text("{target}"), '
            f'.el-select-dropdown__item span:has-text("{target}")'
        )
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
    """分层断言（6.3.4）：URL 和文案锚点都检查，全部满足才 passed。

    同一句 expected 同时包含 URL 和文案时，不能因为 URL 先命中就跳过文案；
    只有所有可验证维度都通过才返回 passed，否则 failed；没有任何锚点才 skipped。
    """
    expected = str(expected or "")
    path_match = URL_PATH_RE.search(expected)
    path = path_match.group(0) if path_match else ""
    url_required = bool(path)
    anchors = QUOTE_ANCHOR_RE.findall(expected)

    url_ok = True
    url_detail = ""
    if url_required:
        url_ok = _url_path_matches(path, page.url)
        url_detail = (
            f"URL 命中: {path}"
            if url_ok
            else f"URL 未命中: {path}（当前 {urlparse(page.url).path}）"
        )

    anchors_ok = True
    anchor_detail = ""
    if anchors:
        text = page.inner_text("body")
        missing = [anchor for anchor in anchors if anchor not in text]
        anchors_ok = not missing
        anchor_detail = (
            f"文本锚点命中: {anchors}"
            if anchors_ok
            else f"文本锚点缺失: {missing}"
        )

    if url_required and not url_ok:
        return False, "failed", url_detail
    if anchors and not anchors_ok:
        return False, "failed", anchor_detail
    if url_required and anchors:
        return True, "passed", f"{url_detail}；{anchor_detail}"
    if url_required:
        return True, "passed", url_detail
    if anchors:
        return True, "passed", anchor_detail
    if URL_HINT_RE.search(expected):
        return False, "skipped", "包含 URL 提示但缺少可校验路径或文案锚点"
    return False, "skipped", "无可验证的断言锚点"


def _url_path_matches(expected_path: str, current_url: str) -> bool:
    """按路径匹配，不做简单子串匹配；支持 /orders/{id} 这类占位路径。"""
    current_path = (urlparse(current_url).path or "/").rstrip("/") or "/"
    expected = (expected_path or "/").rstrip("/") or "/"
    parts = re.split(r"\{[^/{}]+\}", expected)
    pattern = r"[^/]+".join(re.escape(part) for part in parts)
    return re.fullmatch(pattern, current_path) is not None


def _refresh_requested(step: dict) -> bool:
    selector = step.get("uiSelector") or {}
    return bool(REFRESH_RE.search(
        f"{step.get('action') or ''} {step.get('target') or ''} {selector.get('value') or ''}"
    ))


def _reload_and_verify(page) -> dict:
    """执行真实浏览器刷新，并用 Navigation Timing 生成可审计证据。"""
    page.reload(wait_until="load")
    navigation = page.evaluate(
        """() => {
          const entries = performance.getEntriesByType('navigation');
          const nav = entries && entries.length ? entries[entries.length - 1] : null;
          return { type: nav ? nav.type : '', url: location.href };
        }"""
    )
    navigation_type = str((navigation or {}).get("type") or "")
    if navigation_type != "reload":
        raise _StepError(
            "NAVIGATION_FAILED",
            f"浏览器刷新未通过 Navigation Timing 验证，实际类型: {navigation_type}",
        )
    return {
        "navigationType": navigation_type,
        "url": (navigation or {}).get("url"),
    }


def execute_case(case: dict, target_url: str, output_dir: str, mode: str = "programmatic") -> dict:
    """执行单条用例，返回执行记录 dict。"""
    from playwright.sync_api import sync_playwright

    steps = case.get("structuredSteps") or []
    out = Path(output_dir) / case.get("id", "exec")
    out.mkdir(parents=True, exist_ok=True)

    total = len(steps)
    passed = 0
    step_results = []
    variables: dict[str, str] = {}
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
                reload_evidence = None
                assertion_detail = None
                capture_detail = None
                step_evidence = None
                try:
                    if stype == "ui_action" and (step.get("uiSelector") or {}).get("type") == "route":
                        route = step["uiSelector"]["value"]
                        url = target_url.rstrip("/") + ("/" + route.lstrip("/") if route else "")
                        response = page.goto(url)
                        if response is not None and response.status >= 400:
                            raise _StepError(
                                "NAVIGATION_FAILED",
                                f"页面返回 HTTP {response.status}: {url}",
                            )
                        page.wait_for_timeout(300)
                    elif stype == "ui_action" and _refresh_requested(step):
                        reload_evidence = _reload_and_verify(page)
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
                        expected_value = _resolve_variables(step["inputValue"], variables)
                        loc.fill(expected_value)
                        try:
                            sensitive_text = (
                                f"{step.get('target') or ''} {step.get('action') or ''} "
                                f"{(step.get('uiSelector') or {}).get('value') or ''}"
                            ).lower()
                            sensitive = any(
                                word in sensitive_text
                                for word in ("密码", "password", "pwd")
                            )
                            step_evidence = _input_evidence(
                                expected_value, loc.input_value(), sensitive=sensitive
                            )
                        except Exception as exc:  # noqa: BLE001 - 证据采集失败不阻断执行
                            step_evidence = {
                                "inputValueLength": len(str(expected_value)),
                                "valueMatch": None,
                                "evidenceError": str(exc)[:200],
                            }
                    elif stype == "capture":
                        variable = str(step.get("variable") or "").strip()
                        pattern = str(step.get("pattern") or "").strip()
                        if not variable or not VARIABLE_NAME_RE.match(variable):
                            raise _StepError("CASE_INVALID", "capture 步骤缺少合法 variable")
                        if not pattern:
                            raise _StepError("CASE_INVALID", "capture 步骤缺少 pattern")
                        try:
                            regex = re.compile(pattern)
                        except re.error as exc:
                            raise _StepError("CASE_INVALID", f"capture pattern 非法: {exc}") from exc
                        match = regex.search(page.inner_text("body"))
                        if not match:
                            raise _StepError(
                                "ASSERT_FAILED",
                                f"未能从页面捕获变量 {variable}，pattern={pattern}",
                            )
                        captured = match.group(1) if regex.groups else match.group(0)
                        variables[variable] = str(captured)
                        capture_detail = f"capture {variable}={captured}"
                    elif stype == "state_assert":
                        if REFRESH_RE.search(
                            f"{step.get('action') or ''} {step.get('expected') or ''}"
                        ) and reload_evidence is None:
                            reload_evidence = _reload_and_verify(page)
                        # 等页面跳转/渲染稳定（如提交后 500ms 跳转列表页）
                        page.wait_for_timeout(800)
                        resolved_expected = _resolve_variables(step.get("expected") or "", variables)
                        ok_, status, detail = _do_assert(page, resolved_expected)
                        result = status
                        if status == "passed":
                            assertion_detail = detail
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
                except PlaywrightTimeoutError as e:
                    result = "failed"
                    error = f"步骤超时: {e}"
                    record["errorCategory"] = record["errorCategory"] or "TIMEOUT"
                except Exception as e:  # noqa: BLE001
                    result = "failed"
                    error = f"{type(e).__name__}: {e}"
                    is_navigation = stype == "ui_action" and (step.get("uiSelector") or {}).get("type") == "route"
                    record["errorCategory"] = record["errorCategory"] or (
                        "NAVIGATION_FAILED" if is_navigation else "ENV_ERROR"
                    )

                try:
                    page.screenshot(path=after)
                except Exception:
                    after = None

                action_for_record = str(step.get("action") or "")
                target_for_record = str(step.get("target") or "")
                selector_for_record = (step.get("uiSelector") or {}).get("value") or None
                if reload_evidence:
                    action_for_record = (
                        f"{action_for_record}（浏览器已刷新："
                        f"performance.navigation.type={reload_evidence['navigationType']}）"
                    )
                    target_for_record = "browser.reload"
                    selector_for_record = f"browser.reload; navigationType={reload_evidence['navigationType']}"
                elif stype == "state_assert" and assertion_detail:
                    action_for_record = f"{action_for_record}（断言证据：{assertion_detail}）"
                elif stype == "capture" and capture_detail:
                    action_for_record = f"{action_for_record}（变量证据：{capture_detail}）"

                if result == "passed":
                    passed += 1
                step_results.append({
                    "stepIndex": idx, "stepType": stype, "action": action_for_record,
                    "target": target_for_record, "selectorUsed": selector_for_record,
                    "result": result, "screenshotBefore": before, "screenshotAfter": after,
                    "error": error, "evidence": step_evidence or {},
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
            elif any(s["result"] == "failed" for s in step_results):
                record["status"] = "failed"
            elif any(s["result"] == "skipped" for s in step_results):
                record["status"] = "skipped"
            else:
                record["status"] = "passed"
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
