# app/agents/travel/supervisor.py
"""TravelSupervisor：编排天气/路线/POI/邮件/12306/住宿子 Agent。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.types import Command
from langgraph_supervisor import create_supervisor

from app.agents.travel.agents import (
    create_weather_agent, create_route_agent, create_poi_agent,
    create_email_agent, create_train_agent, create_hotel_agent,
)
from app.agents.travel.mcp_client import get_travel_tools
from app.agents.travel.prompts import SUPERVISOR_PROMPT
from app.agents.travel.tools import save_travel_plan, set_store
from app.common.logger import logger
from app.common.sse import sse_event, sse_json_event, serialize
from app.models.session import session_manager

load_dotenv()


def _prompt_with_today() -> str:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today = f"{now.year}年{now.month}月{now.day}日（{wd[now.weekday()]}）"
    return f"{SUPERVISOR_PROMPT}\n\n## 当前日期\n今天是 {today}。所有相对日期以此为准。"


# ---- 流式工具 ----

def _unpack(chunk):
    if isinstance(chunk, tuple) and len(chunk) == 2:
        return chunk
    return (chunk.get("type"), chunk.get("data")) if isinstance(chunk, dict) else (None, None)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item if isinstance(item, str)
            else (item.get("text") or item.get("content") or "")
            for item in content if isinstance(item, (str, dict))
        )
    return ""


def _interrupts(data):
    if isinstance(data, dict):
        if "__interrupt__" in data:
            v = data["__interrupt__"]
            yield from (v if isinstance(v, list) else [v])
        for val in data.values():
            yield from _interrupts(val)
    elif isinstance(data, list):
        for item in data:
            yield from _interrupts(item)


def _is_noise(s: str) -> bool:
    t = (s or "").strip().lower()
    return t.startswith("transferring") or t.startswith("successfully transferred")


def _visible(meta: dict) -> bool:
    node = meta.get("langgraph_node", "")
    return node == "supervisor" or "supervisor" in (meta.get("langgraph_path") or ())


_NODE_HINTS = {
    "weather_expert": "天气专家正在调研...",
    "route_expert": "路线专家正在规划...",
    "poi_expert": "正在搜索周边...",
    "email_expert": "正在准备邮件...",
    "train_expert": "正在查询火车票...",
    "hotel_expert": "正在查询住宿...",
}


# ---- TravelSupervisor ----

class TravelSupervisor:
    def __init__(self):
        self.graph = None

    async def init(self):
        logger.info("TravelSupervisor 初始化中...")
        await session_manager.init()

        tools = await get_travel_tools()

        # 注入 store 给 tools.py 使用
        set_store(session_manager.store)

        # 子 Agent（可用的才注册）
        agents = [
            create_weather_agent(tools["weather"]),
            create_route_agent(tools["route"]),
            create_poi_agent(tools["poi"]),
            create_email_agent(),
        ]
        if tools["train"]:
            agents.append(create_train_agent(tools["train"]))
            logger.info("12306 专家已启用")
        if tools["hotel"]:
            agents.append(create_hotel_agent(tools["hotel"]))
            logger.info("住宿专家已启用")

        # Supervisor 工具
        sup_tools = [save_travel_plan]
        for name in ("maps_geo", "maps_distance", "maps_schema_personal_map"):
            t = next((t for t in tools["supervisor"] if t.name == name), None)
            if t:
                sup_tools.append(t)

        model = init_chat_model("deepseek-chat", streaming=True)
        self.graph = create_supervisor(
            agents=agents,
            model=model,
            prompt=_prompt_with_today(),
            tools=sup_tools,
            parallel_tool_calls=True,
            output_mode="full_history",
        ).compile(
            checkpointer=session_manager.checkpointer,
            store=session_manager.store,
        )
        logger.info(f"TravelSupervisor 初始化完成 ✓ ({len(agents)} 个子 Agent)")

    async def close(self):
        await session_manager.close()

    async def generate_sse(self, thread_id: str, message: str, interrupt_decision: dict | None = None):
        config = {"configurable": {"thread_id": thread_id, "user_id": "default"}}
        inp = (
            Command(resume={"decisions": [dict(interrupt_decision)]})
            if interrupt_decision
            else {"messages": [{"role": "user", "content": message}]}
        )

        try:
            seen = set()
            async for chunk in self.graph.astream(inp, config=config, stream_mode=["messages", "updates"]):
                kind, data = _unpack(chunk)

                if kind == "messages":
                    token, meta = data
                    if token.__class__.__name__ in ("AIMessage", "AIMessageChunk") and _visible(meta):
                        t = _text(getattr(token, "content", "")).strip()
                        if t and not _is_noise(t):
                            yield sse_event("message_delta", t)

                elif kind == "updates":
                    if isinstance(data, dict):
                        for node in data:
                            s = _NODE_HINTS.get(node)
                            if s and s not in seen:
                                seen.add(s)
                                yield sse_event("status", s)
                    for item in _interrupts(data):
                        val = serialize(item.value if hasattr(item, "value") else item)
                        if isinstance(val, list) and len(val) == 1:
                            val = val[0]
                        yield sse_json_event("interrupt", {"type": "interrupt", "interrupt": val})

            yield sse_event("done", "")
        except Exception as exc:
            logger.error(f"SSE 流错误: {exc}", exc_info=True)
            yield sse_event("error", str(exc))

    async def get_messages(self, thread_id: str) -> dict:
        return await session_manager.get_messages(self.graph, thread_id)

    async def clear_messages(self, thread_id: str):
        await session_manager.clear_messages(thread_id)


travel_supervisor = TravelSupervisor()
