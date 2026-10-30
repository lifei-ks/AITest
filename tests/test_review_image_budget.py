"""视觉审核图片预算与二阶段补图测试。"""
from __future__ import annotations

from app.agent import review_agent


def _steps(tmp_path, count: int = 8) -> list[dict]:
    steps = []
    for index in range(1, count + 1):
        before = tmp_path / f"step-{index:02d}-before.png"
        after = tmp_path / f"step-{index:02d}-after.png"
        before.write_bytes(b"png")
        after.write_bytes(b"png")
        steps.append({
            "stepIndex": index,
            "stepType": "input",
            "action": f"操作{index}",
            "result": "passed",
            "screenshotBefore": str(before),
            "screenshotAfter": str(after),
        })
    return steps


def test_pick_images_respects_budget_and_priority(tmp_path):
    steps = _steps(tmp_path, 8)
    steps[1]["result"] = "failed"
    steps[3]["stepType"] = "state_assert"
    picked = review_agent._pick_images(steps, max_images=3)
    assert len(picked) == 3
    assert picked[0]["stepIndex"] == 2
    assert picked[0]["path"].endswith("step-02-after.png")
    assert picked[1]["path"].endswith("step-02-before.png")


class FakeClient:
    model = "fake-vlm"

    def __init__(self, issue_on_first: bool = False):
        self.calls: list[int] = []
        self.issue_on_first = issue_on_first

    def available(self) -> bool:
        return True

    def chat_json(self, messages, max_tokens=0, json_mode=False):
        image_count = sum(
            1 for item in messages[0]["content"] if item.get("type") == "image_url"
        )
        self.calls.append(image_count)
        if len(self.calls) == 1:
            if self.issue_on_first:
                return {
                    "verdict": "suspect",
                    "reason": "发现报错",
                    "images": [{"image": 1, "verdict": "suspect", "analysis": "出现报错"}],
                    "issues": [{"image": 1, "problem": "页面报错"}],
                }
            return {"verdict": "suspect", "reason": "截图证据不足", "images": [], "issues": []}
        return {
            "verdict": "ok",
            "reason": "补充截图后确认正常",
            "images": [
                {"image": i + 1, "verdict": "ok", "analysis": "符合预期"}
                for i in range(image_count)
            ],
            "issues": [],
        }


class FakeSettings:
    vision_max_images = 6
    vision_max_images_failed = 10
    vision_two_pass_review = True


def test_evidence_insufficient_triggers_second_pass(monkeypatch, tmp_path):
    steps = _steps(tmp_path, 8)
    client = FakeClient()
    monkeypatch.setattr(review_agent, "_vision_client", lambda: client)
    monkeypatch.setattr(review_agent, "get_settings", lambda: FakeSettings())

    result = review_agent.review_execution({"title": "测试", "expectedResults": ["「成功」"]}, steps, "http://localhost")
    assert client.calls == [6, 8]
    assert result["verdict"] == "ok"
    assert result["reviewPasses"] == 2
    assert result["imageCount"] == 8
    assert result["imageCoverage"] == "8/8"


def test_actionable_suspect_does_not_trigger_second_pass(monkeypatch, tmp_path):
    steps = _steps(tmp_path, 8)
    client = FakeClient(issue_on_first=True)
    monkeypatch.setattr(review_agent, "_vision_client", lambda: client)
    monkeypatch.setattr(review_agent, "get_settings", lambda: FakeSettings())

    result = review_agent.review_execution({"title": "测试", "expectedResults": ["「成功」"]}, steps, "http://localhost")
    assert client.calls == [6]
    assert result["verdict"] == "suspect"
    assert result["issues"]


def test_image_limit_expands_for_failed_steps(monkeypatch):
    class Settings:
        vision_max_images = 6
        vision_max_images_failed = 10
        vision_two_pass_review = True

    monkeypatch.setattr(review_agent, "get_settings", lambda: Settings())
    assert review_agent._image_limit([{"result": "passed"}]) == 6
    assert review_agent._image_limit([{"result": "failed"}]) == 10
    assert review_agent._image_limit([{"result": "passed"}], extended=True) == 12
