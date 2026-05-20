from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.travel.pipeline import travel_pipeline

router = APIRouter()

class TravelChatRequest(BaseModel):
    message: str=""
    thread_id:str="default"
    user_id:str | None=None
    interrupt_decision:Optional[Dict[str, Any]]=None

@router.post("/travel/send",tags=["出行企划 Agent"])
async def send_travel(request:TravelChatRequest):
    return EventSourceResponse(
        travel_pipeline.generate_sse(
            thread_id=request.thread_id,
            message=request.message or "",
            interrupt_decision=request.interrupt_decision
        )
    )


@router.get("/travel/messages", tags=["出行企划 Agent"])
async def get_travel_messages(thread_id: str):
    return await travel_pipeline.get_messages(thread_id)


@router.delete("/travel/messages", tags=["出行企划 Agent"])
async def clear_travel_messages(thread_id: str):
    await travel_pipeline.clear_messages(thread_id)
    return {"status": "ok"}
