# app/api/v1/travel.py
"""出行企划 REST API。"""

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.travel.supervisor import travel_supervisor

router = APIRouter()


class TravelRequest(BaseModel):
    message: str = ""
    thread_id: str = "default"
    interrupt_decision: Optional[Dict[str, Any]] = None


@router.post("/travel/send")
async def send_travel(request: TravelRequest):
    """SSE 流式对话接口。"""
    return EventSourceResponse(
        travel_supervisor.generate_sse(
            thread_id=request.thread_id,
            message=request.message,
            interrupt_decision=request.interrupt_decision,
        )
    )


@router.get("/travel/messages")
async def get_messages(thread_id: str):
    """获取会话历史（前端切换会话时调用）。"""
    return await travel_supervisor.get_messages(thread_id)


@router.delete("/travel/messages")
async def clear_messages(thread_id: str):
    """清除会话历史（前端删除会话时调用）。"""
    await travel_supervisor.clear_messages(thread_id)
    return {"status": "ok"}