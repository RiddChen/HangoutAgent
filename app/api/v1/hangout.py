# app/api/v1/hangout.py
"""出行企划 REST API。"""

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.hangout.orchestrator import hangout_orchestrator

router = APIRouter()


class HangoutRequest(BaseModel):
    message: str = ""
    thread_id: str = "default"
    interrupt_decision: Optional[Dict[str, Any]] = None


@router.post("/hangout/send")
async def send_hangout(request: HangoutRequest):
    """SSE 流式对话接口。"""
    return EventSourceResponse(
        hangout_orchestrator.generate_sse(
            thread_id=request.thread_id,
            message=request.message,
            interrupt_decision=request.interrupt_decision,
        )
    )


@router.get("/hangout/messages")
async def get_messages(thread_id: str):
    """获取会话历史（前端切换会话时调用）。"""
    return await hangout_orchestrator.get_messages(thread_id)


@router.delete("/hangout/messages")
async def clear_messages(thread_id: str):
    """清除会话历史（前端删除会话时调用）。"""
    await hangout_orchestrator.clear_messages(thread_id)
    return {"status": "ok"}
