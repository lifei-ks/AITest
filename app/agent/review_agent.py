"""执行结果截图审核（视觉 LLM）。

规则：
- 未配置视觉模型 → 不审核（返回 None），执行结果保持原状态
- 配置了但调用失败/无截图 → verdict=unavailable，交人工复核
- 只依据截图与步骤记录判断，证据不足一律 suspect，避免假通过
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from app.config import get_settings
from app.llm.client import LlmClient, LlmUnavailable

logger = logging.getLogger(__name__)

VERDICT_OK = "ok"
VERDICT_SUSPECT = "suspect"
VERDICT_UNAVAILABLE = "unavailable"

MAX_IMAGES = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _effective_vision_config() -> tuple[str, str, str]:
    """多模态配置优先；任一项为空则逐项回退到文本 LLM。返回 (key, base, model)。"""
    s = get_settings()
    return (
        s.llm_vision_api_key or s.llm_api_key,
        s.llm_vision_base_url or s.llm_base_url,
        s.llm_vision_model or s.llm_model,
    )


def vision_enabled() -> bool:
    """只要有多模态模型（或回退后的文本模型）+ Key 就启用截图审核。"""
    key, _base, model = _effective_vision_config()
    return bool((model or "").strip() and key)


def _vision_client() -> LlmClient:
    key, base, model = _effective_vision_config()
    return LlmClient(key, base, model)


def _pick_images(steps: list[dict], max_images: int = MAX_IMAGES) -> list[dict]:
    """挑选关键截图：失败步骤（前/后）→ 最后一步 → 断言步骤 → 第一步，最多 6 张。"""
    picked: list[dict] = []
    seen: set[str] = set()

    def add(path: str | None, label: str, step_index: int | None = None) -> None:
        if not path or len(picked) >= max_images or path in seen:
            return
        p = Path(path)
        try:
            if not p.exists() or p.stat().st_size > MAX_IMAGE_BYTES:
                return
        except OSError:
            return
        seen.add(path)
        picked.append({"label": label, "path": str(p), "stepIndex": step_index})

    for idx, step in enumerate(steps, 1):
        if step.get("result") != "passed":
            add(step.get("screenshotAfter"), f"步骤{idx}「{step.get('action', '')}」操作后（结果 {step.get('result')}）", idx)
            add(step.get("screenshotBefore"), f"步骤{idx} 操作前", idx)

    if steps:
        add(steps[-1].get("screenshotAfter"), "最后一步操作后", len(steps))

    for idx, step in enumerate(steps, 1):
        if step.get("stepType") == "state_assert":
            add(step.get("screenshotAfter"), f"步骤{idx} 断言后", idx)

    if steps:
        add(steps[0].get("screenshotAfter"), "第一步操作后", 1)

    for idx, step in enumerate(steps, 1):
        add(step.get("screenshotAfter"), f"步骤{idx} 操作后", idx)

    return picked


def _steps_text(steps: list[dict]) -> str:
    lines = []
    for idx, s in enumerate(steps, 1):
        line = f"{idx}. [{s.get('stepType')}] {s.get('action', '')}（结果：{s.get('result')}）"
        if s.get("target"):
            line += f" 目标：{s.get('target')}"
        if s.get("selectorUsed"):
            line += f" 执行证据：{s.get('selectorUsed')}"
        if s.get("error"):
            line += f" 错误：{s.get('error')}"
        lines.append(line)
    return "\n".join(lines)


def _build_prompt(case: dict, steps: list[dict], target_url: str, images: list[dict]) -> str:
    expected = "、".join(case.get("expectedResults") or []) or "（用例未写预期结果）"
    image_list = "\n".join(f"图{i}：{img['label']}" for i, img in enumerate(images, 1))
    return f"""你是 UI 自动化测试结果审核员。请根据用例、步骤记录和关键截图，判断本次执行是否真的符合业务预期。

被测地址：{target_url}
用例标题：{case.get("title", "")}
预期结果：{expected}

执行步骤与结果：
{_steps_text(steps)}

截图清单：
{image_list}

审核要求：
1. 只依据截图中可见的内容和步骤记录判断，不要臆测未展示的信息。
2. 若截图出现报错提示、空白页、登录页、与预期不符的页面/文案/数据，判 suspect。
3. 浏览器刷新是页面动作，截图通常看不到浏览器刷新过程。若步骤记录中明确出现
   「浏览器已刷新」或 `performance.navigation.type=reload`，应把这视为刷新动作
   的客观证据；此时截图只用于验证刷新后的页面状态，不要因为截图无法展示
   “正在刷新”就判 suspect。
4. 若用例预期要求刷新，但步骤记录既没有刷新动作，也没有 Navigation Timing 证据，
   才应因缺少刷新验证判 suspect。
5. 若截图不足以证明其他预期结果（例如关键结果没截到、图片模糊），也判 suspect，不要放过。
6. 只有截图明确展示了预期结果（文案、列表数据、状态变化等）才判 ok。
7. 不要因为步骤结果写着 passed 就直接判 ok，必须自己看截图。

确定性断言证据的补充规则：
- 若步骤记录包含「断言证据：文本锚点命中」或「断言证据：URL 命中」，说明 Playwright 已做过
  精确检查。除非截图出现矛盾（报错、空白页、错误页面、相反状态），不要仅因截图分辨率、
  文案在截图中不够醒目或“截图没有完整展示该文案”就判 suspect。

请对每一张图片都给出单独分析（images 数组，每张图一条），方便人工查阅：
- image：图片编号（从 1 开始）
- verdict：ok（该图符合预期）/ suspect（该图有问题或无法证明预期）
- analysis：这张图看到了什么、和预期是否一致，1~2 句话
- 如果该图有明确问题，同时在 issues 中给出：
  - image：图片编号
  - bbox：问题区域的归一化坐标 [x1,y1,x2,y2]，0~1，相对图片左上角；必须框住问题元素/提示，无法确定时省略
  - problem：一句话说明问题

只输出 JSON：
{{"verdict":"ok|suspect","reason":"不超过 120 字的中文结论","images":[{{"image":1,"verdict":"ok","analysis":"截图显示订单列表出现新订单，符合预期"}}],"issues":[{{"image":1,"step":2,"bbox":[0.12,0.35,0.78,0.52],"problem":"具体问题"}}]}}"""


def _png_size(path: str) -> tuple[int, int] | None:
    """从 PNG 头读尺寸（不依赖 Pillow），用于把像素坐标归一化。"""
    import struct

    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None


def _normalize_bbox(raw, path: str) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    size = _png_size(path)
    # 模型偶尔返回像素坐标：按图片尺寸折算
    if any(v > 1 for v in vals):
        if size:
            w, h = size
            vals = [vals[0] / w, vals[1] / h, vals[2] / w, vals[3] / h]
        else:
            return None
    x1, y1, x2, y2 = vals
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x1, y1 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
    x2, y2 = max(0.0, min(1.0, x2)), max(0.0, min(1.0, y2))
    if x2 - x1 < 0.02 or y2 - y1 < 0.02:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def _parse_issues(raw, images: list[dict]) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("image") or item.get("imageIndex") or 0)
        except (TypeError, ValueError):
            idx = 0
        img = images[idx - 1] if 1 <= idx <= len(images) else None
        problem = str(item.get("problem") or "").strip()
        if not problem:
            continue
        out.append(
            {
                "imageIndex": idx,
                "stepIndex": img["stepIndex"] if img else item.get("step"),
                "label": img["label"] if img else "",
                "screenshot": img["path"] if img else None,
                "bbox": _normalize_bbox(item.get("bbox"), img["path"]) if img else None,
                "problem": problem[:500],
            }
        )
    return out


def _parse_image_reviews(raw, images: list[dict]) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    seen: set[int] = set()
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("image") or item.get("imageIndex") or 0)
        except (TypeError, ValueError):
            continue
        if idx in seen or not (1 <= idx <= len(images)):
            continue
        img = images[idx - 1]
        analysis = str(item.get("analysis") or "").strip()
        if not analysis:
            continue
        seen.add(idx)
        raw_verdict = str(item.get("verdict") or "").strip().lower()
        out.append(
            {
                "imageIndex": idx,
                "stepIndex": img["stepIndex"],
                "label": img["label"],
                "screenshot": img["path"],
                # 只接受模型明确给出的 ok；缺省、拼写错误或未知值一律按 suspect，
                # 避免“没有分析结果”被误当成正常截图。
                "verdict": "ok" if raw_verdict == "ok" else "suspect",
                "analysis": analysis[:1000],
                "bbox": _normalize_bbox(item.get("bbox"), img["path"]),
            }
        )
    return out


def _normalize_verdict(
    raw_verdict,
    image_reviews: list[dict],
    issues: list[dict],
    image_count: int,
) -> tuple[str, str | None]:
    """汇总模型结论，任何异常证据都会把总 verdict 降级为 suspect。"""
    verdict = str(raw_verdict or "").strip().lower()
    reasons: list[str] = []
    if verdict not in (VERDICT_OK, VERDICT_SUSPECT):
        verdict = VERDICT_SUSPECT
        reasons.append("模型未返回有效总结论")
    if issues:
        verdict = VERDICT_SUSPECT
        reasons.append("审核发现具体问题")
    if any(item.get("verdict") == VERDICT_SUSPECT for item in image_reviews):
        verdict = VERDICT_SUSPECT
        reasons.append("至少一张关键截图疑似异常")
    reviewed = {item.get("imageIndex") for item in image_reviews}
    if len(reviewed) < image_count:
        verdict = VERDICT_SUSPECT
        reasons.append(f"仅分析了 {len(reviewed)}/{image_count} 张关键截图")
    return verdict, "；".join(reasons) or None


def review_execution(case: dict, steps: list[dict], target_url: str) -> dict | None:
    """返回审核结果；未配置视觉模型时返回 None（不审核）。"""
    if not vision_enabled():
        return None

    model = _effective_vision_config()[2]
    images = _pick_images(steps)
    if not images:
        return {
            "verdict": VERDICT_UNAVAILABLE,
            "reason": "没有可用的截图，无法进行 AI 审核",
            "model": model,
            "imageCount": 0,
            "imageReviews": [],
            "issues": [],
        }

    prompt = _build_prompt(case, steps, target_url, images)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        label = img["label"]
        path = img["path"]
        try:
            raw = Path(path).read_bytes()
        except OSError:
            continue
        content.append({"type": "text", "text": f"截图：{label}"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"},
            }
        )

    try:
        data = _vision_client().chat_json(
            [{"role": "user", "content": content}],
            max_tokens=2500,
            json_mode=True,
        )
    except LlmUnavailable as exc:
        logger.warning("视觉模型审核失败: %s", exc)
        return {
            "verdict": VERDICT_UNAVAILABLE,
            "reason": f"AI 审核调用失败：{exc}"[:2000],
            "model": model,
            "imageCount": len(images),
            "imageReviews": [],
            "issues": [],
        }

    if not isinstance(data, dict):
        return {
            "verdict": VERDICT_UNAVAILABLE,
            "reason": "视觉模型返回结构非法",
            "model": model,
            "imageCount": len(images),
            "imageReviews": [],
            "issues": [],
        }

    image_reviews = _parse_image_reviews(data.get("images"), images)
    issues = _parse_issues(data.get("issues"), images)
    verdict, downgrade_reason = _normalize_verdict(
        data.get("verdict"), image_reviews, issues, len(images)
    )
    reason = str(data.get("reason") or "").strip()
    if downgrade_reason:
        reason = f"{reason}；{downgrade_reason}" if reason else downgrade_reason
    if issues:
        parts = [f"图{it['imageIndex']}：{it['problem']}" for it in issues]
        reason = (reason + "；" + "；".join(parts))[:2000]
    else:
        reason = reason[:2000]

    return {
        "verdict": verdict,
        "reason": reason or "视觉模型未给出原因",
        "model": model,
        "imageCount": len(images),
        "imageReviews": image_reviews,
        "issues": issues,
    }
