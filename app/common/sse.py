# app/common/sse.py
"""SSE（Server-Sent Events）序列化工具。"""

import json


def sse_event(event_type: str, content: str) -> dict:
    """构造普通文本 SSE 事件。"""
    return {
        "event": event_type,
        "data": json.dumps(
            {"type": event_type, "content": content},
            ensure_ascii=False,
        ),
    }


def sse_json_event(event_type: str, payload: dict) -> dict:
    """构造 JSON SSE 事件（用于 interrupt 等复杂数据）。"""
    return {
        "event": event_type,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }


def serialize(obj):
    """将 LangGraph 对象（Interrupt、Command 等）转为可 JSON 序列化的格式。"""
    if hasattr(obj, "value"):
        return serialize(obj.value)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {key: serialize(value) for key, value in obj.items()}
    return obj