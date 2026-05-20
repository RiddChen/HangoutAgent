from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.agents.email.email_agent import email_agent
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


@router.get("/chat/messages", tags=["邮件 Agent"])
async def get_chat_messages(thread_id: str):
    return await email_agent.get_messages(thread_id)


@router.delete("/chat/messages", tags=["邮件 Agent"])
async def clear_chat_messages(thread_id: str):
    await email_agent.clear_messages(thread_id)
    return {"status": "ok"}
