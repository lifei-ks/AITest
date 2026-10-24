"""测试点生成：静态结构/固定数量不应成为业务测试点。"""
from __future__ import annotations

from app.agent import test_point_agent


def test_static_structure_points_are_filtered():
    static_points = [
        {
            "title": "新建订单页展示5个表单字段",
            "category": "functional",
            "priority": "P1",
            "description": "新建页含收货人、手机号、收货地址、订单金额、备注5个输入项",
            "expected": "「请输入备注（选填）」",
            "fields": [],
        },
        {
            "title": "列表页默认展示4条种子订单",
            "category": "functional",
            "priority": "P1",
            "description": "列表默认显示4条种子订单",
            "expected": "「共 4 条订单」",
            "fields": [],
        },
    ]
    weak_status = {
        "title": "待支付订单排在列表第一行",
        "category": "state",
        "priority": "P1",
        "description": "列表第一行是待支付订单",
        "expected": "「待支付」",
        "fields": [],
    }
    normal = {
        "title": "合法数据新建订单成功",
        "category": "functional",
        "priority": "P0",
        "description": "填写合法收货信息并提交订单",
        "expected": "「订单创建成功」",
        "fields": ["receiverName", "phone"],
    }
    assert all(test_point_agent.is_static_structure_point(p) for p in static_points)
    assert test_point_agent.is_static_structure_point(weak_status)
    assert not test_point_agent.is_static_structure_point(normal)
    kept = test_point_agent.filter_static_structure_points([*static_points, weak_status, normal])
    assert kept == [normal]


def test_extract_points_drops_static_structure_from_llm():
    class FakeClient:
        available = True

        def chat_json(self, messages, max_tokens=8192, json_mode=False):
            return {
                "testPoints": [
                    {
                        "title": "新建订单页展示5个表单字段",
                        "category": "functional",
                        "priority": "P1",
                        "description": "页面有5个输入项",
                        "expected": "「请输入备注（选填）」",
                    },
                    {
                        "title": "合法数据新建订单成功",
                        "category": "functional",
                        "priority": "P0",
                        "description": "填写合法收货信息并提交",
                        "expected": "「订单创建成功」",
                    },
                ]
            }

    points = test_point_agent.extract_points(
        FakeClient(),
        requirement_version="V1",
        requirement_name="订单",
        content="新建订单",
        facts={},
    )
    assert [p["title"] for p in points] == ["合法数据新建订单成功"]
