from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class TravelChatRequest(BaseModel):
    message: str
    thread_id:str="default"
    user_id:str | None=None

@router.post("/travel/send")
async def send_travel(request:TravelChatRequest):
    return{
        "type":"message",
        "thread_id":request.thread_id,
        "content":"Travel API skeleton is running."
    }