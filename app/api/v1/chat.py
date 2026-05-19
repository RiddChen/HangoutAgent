from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.agents.email_agent import email_agent
from app.models.schemas import ChatRequest


router = APIRouter()


@router.post("/chat/send", tags=["邮件 Agent"])
async def send_chat(request: ChatRequest):
    """EmailAgent SSE endpoint. Handles HITL interrupt decisions."""
    return EventSourceResponse(
        email_agent.generate_sse(
            request.thread_id,
            request.message or "",
            request.interrupt_decision,
        )
    )
