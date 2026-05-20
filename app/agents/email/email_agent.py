import inspect
import json
import os
from typing import Callable

import aiosqlite
from app.common.logger import logger
from app.integrations.gmail_auth import get_gmail_auth_message, has_gmail_token
from app.integrations.gmail_tools import get_gmail_tools
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelRequest,
    ModelResponse,
    dynamic_prompt,
    wrap_model_call,
)
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command


AUTHENTICATED_KEY = "authenticated"


class AuthenticatedState(AgentState):
    authenticated: bool



@tool
def authenticate(runtime: ToolRuntime) -> Command:
    """Check whether Gmail OAuth has been completed."""

    authenticated = has_gmail_token()
    message = get_gmail_auth_message()

    return Command(
        update={
            AUTHENTICATED_KEY: authenticated,
            "messages": [
                ToolMessage(message, tool_call_id=runtime.tool_call_id)
            ],
        }
    )

# 动态工具中间件
@wrap_model_call
async def dynamic_tool_call(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    """Only expose Gmail tools after authentication."""

    authenticated = request.state.get(AUTHENTICATED_KEY)

    if authenticated:
        tools = [tool for tool in request.tools if tool.name != "authenticate"]
    else:
        tools = [authenticate]

    request = request.override(tools=tools)
    return await handler(request)

# 动态提示词
unauthenticated_prompt = """
You are a helpful email assistant.
Before doing anything with Gmail, you must call authenticate to check whether Gmail OAuth has been completed.
If authentication fails, tell the user to complete Google OAuth first.
"""

authenticated_prompt = """
You are a helpful email assistant.
You can search Gmail, read Gmail messages, create drafts, and send Gmail messages.
Before sending an email, the system will require human approval.
"""


@dynamic_prompt
def dynamic_prompt_func(request: ModelRequest) -> str:
    authenticated = request.state.get(AUTHENTICATED_KEY)
    return authenticated_prompt if authenticated else unauthenticated_prompt



# 序列化函数
def _serialize(obj):
    if hasattr(obj, "value"):
        return _serialize(obj.value)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _serialize(value) for key, value in obj.items()}
    return obj

# emailagent 类
class EmailAgent:
    def __init__(self):
        self.conn: aiosqlite.Connection | None = None
        self.checkpointer: BaseCheckpointSaver | None = None
        self.agent = None

    async def init(self):
        await self.init_checkpointer()
        logger.info("checkpointer 初始化完成")
        await self.init_agent()
        logger.info("email agent 初始化完成")

    async def init_checkpointer(self):
        db_path = os.path.join(os.path.dirname(__file__), "../../db/mail_friend.db")
        self.conn = await aiosqlite.connect(db_path)
        self.checkpointer = AsyncSqliteSaver(conn=self.conn)
        await self.checkpointer.setup()

    async def close(self):
        if self.conn:
            await self.conn.close()
            logger.info("sqlite connection 关闭")

    async def init_agent(self):
        gmail_tools = get_gmail_tools()
        tool_names = [tool.name for tool in gmail_tools]
        logger.info(f"Gmail tools loaded: {tool_names}")

        self.agent = create_agent(
            "deepseek-chat",
            tools=[authenticate, *gmail_tools],
            state_schema=AuthenticatedState,
            checkpointer=self.checkpointer,
            middleware=[
                dynamic_tool_call,
                dynamic_prompt_func,
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        "authenticate": False,
                        "search_gmail": False,
                        "get_gmail_message": False,
                        "get_gmail_thread": False,
                        "create_gmail_draft": False,
                        "send_gmail_message": True,#保证发信前出现interrupt
                    }
                ),
            ],
        )

# sse方法
    async def generate_sse(
            self,
            thread_id: str,
            message: str,
            interrupt_decision: dict | None,
    ):
        config = {
            "configurable": {
                "thread_id": thread_id,
            }
        }

        agent_input = {
            "messages": [HumanMessage(content=message)],
            AUTHENTICATED_KEY: has_gmail_token(),
        }
        if interrupt_decision:
            agent_input = Command(
                resume={
                    "decisions": [interrupt_decision],
                }
            )

        logger.info(f"调用 agent，input={agent_input}")

        try:
            async for chunk in self.agent.astream(
                    agent_input,
                    config=config,
                    stream_mode=["messages", "updates"],
                    version="v2",
            ):
                event_type = chunk["type"]
                data = chunk["data"]

                if event_type == "messages":
                    token, metadata = data
                    content = None
                    if isinstance(token, AIMessage) and token.content:
                        content = token.content

                    if content:
                        yield {
                            "event": "message",
                            "data": json.dumps(
                                {"type": "message", "content": content},
                                ensure_ascii=False,
                            ),
                        }

                elif event_type == "updates":
                    if "__interrupt__" in data:
                        interrupt_data = data["__interrupt__"]
                        details = _serialize(interrupt_data)
                        yield {
                            "event": "interrupt",
                            "data": json.dumps(
                                {
                                    "type": "interrupt",
                                    "interrupt": {
                                        "reason": "需要人工确认",
                                        "details": details,
                                    },
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        }

            yield {
                "event": "done",
                "data": json.dumps(
                    {"type": "done", "content": "处理完成"},
                    ensure_ascii=False,
                ),
            }

        except Exception as exc:
            logger.error(f"SSE 流中断: {exc}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps(
                    {"type": "error", "error": str(exc)},
                    ensure_ascii=False,
                ),
            }


# 清空和读取历史

    async def clear_messages(self, thread_id: str):
        logger.info(f"清空 email agent 历史消息，thread_id={thread_id}")
        if not self.checkpointer:
            return

        delete_thread = getattr(self.checkpointer, "delete_thread", None)
        if delete_thread is None:
            logger.warning("当前 checkpointer 不支持 delete_thread")
            return

        result = delete_thread(thread_id)
        if inspect.isawaitable(result):
            await result

    async def get_messages(self, thread_id: str) -> dict:
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.agent.aget_state(config)
        if state is None or not state.values:
            return {"messages": []}

        messages = state.values.get("messages", [])

        result = []
        for msg in messages:
            if not msg.content:
                continue
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": msg.content})

        response = {"messages": result}

        interrupts = None
        if hasattr(state, "interrupts") and state.interrupts:
            interrupts = state.interrupts
        elif hasattr(state, "tasks") and state.tasks:
            for task in state.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupts = task.interrupts
                    break

        if interrupts:
            response["has_interrupt"] = True
            response["interrupt"] = {
                "reason": "需要人工确认",
                "details": _serialize(interrupts),
            }

        return response

email_agent = EmailAgent()

__all__ = ["email_agent"]
