# app/agents/hangout/supervisor.py
"""HangoutSupervisor：编排天气/路线/POI/邮件/12306/住宿子 Agent。"""

import asyncio
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.types import Command
from langgraph_supervisor import create_supervisor

from app.agents.hangout.agents import (
    create_weather_agent, create_route_agent, create_poi_agent,
    create_email_agent, create_train_agent, create_flight_agent, create_hotel_agent,
)
from app.agents.hangout.mcp_client import get_hangout_tools
from app.agents.hangout.prompts import SUPERVISOR_PROMPT
from app.agents.hangout.tools import (
    HangoutState,
    ask_weather_concern,
    mark_trip_type,
    mark_weather_result,
    save_final_plan,
    set_store,
    update_trip_info,
)
from app.common.logger import logger
from app.common.sse import sse_event, sse_json_event, serialize
from app.models.session import session_manager

load_dotenv()


def _make_prompt_fn():
    """返回一个接收 state 的 callable，每轮模型调用时自动注入当前状态。

    注意：callable prompt 必须返回 [SystemMessage, ...messages] 列表，
    不能只返回字符串，否则框架不会拼接对话历史。
    """
    from langchain_core.messages import SystemMessage

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today = f"{now.year}年{now.month}月{now.day}日（{wd[now.weekday()]}）"
    base = f"{SUPERVISOR_PROMPT}\n\n## 当前日期\n今天是 {today}。所有相对日期以此为准。"

    _field_labels = {
        "destination": "目的地", "date": "日期", "origin": "出发地",
        "transport_preference": "交通偏好", "dining_preference": "用餐偏好",
        "nearby_preference": "周边偏好", "hotel_needed": "住宿需求",
    }

    def prompt_fn(state: dict) -> list:
        parts = [base]

        # ── 自动注入已收集的出行信息 ──
        info_lines = []
        for field, label in _field_labels.items():
            val = state.get(field, "")
            if val:
                info_lines.append(f"- {label}：{val}")

        trip_type = state.get("trip_type", "")
        if trip_type:
            info_lines.append(f"- 出行类型：{'同城' if trip_type == 'same_city' else '跨城'}")

        if state.get("weather_checked"):
            ok = state.get("weather_ok", False)
            summary = state.get("weather_summary", "")
            info_lines.append(f"- 天气：{'✅ 适合出行' if ok else '⚠️ 不太理想'}（{summary}）")

        if state.get("plan_saved"):
            info_lines.append("- 最终方案：✅ 已保存")

        if info_lines:
            parts.append("\n## 当前已收集的出行信息（自动注入，无需调工具读取）\n" + "\n".join(info_lines))

        # ── 阶段提示：用代码约束 LLM 下一步 ──
        dest = state.get("destination", "")
        date = state.get("date", "")
        weather_checked = state.get("weather_checked", False)
        weather_ok = state.get("weather_ok", False)

        hints = []
        if dest and date and not weather_checked:
            hints.append("目的地和日期已齐全，下一步**必须** transfer_to_weather_expert 查天气，不能跳过。")
        if weather_checked and not weather_ok:
            hints.append("天气不理想，**必须**调用 ask_weather_concern 询问用户是否在意，不能跳过。")
        if weather_checked and weather_ok and not state.get("origin", ""):
            hints.append("天气已通过，但还缺出发地，请询问用户从哪里出发。")

        if hints:
            parts.append("\n## ⚠️ 阶段提示（必须遵守）\n" + "\n".join(f"- {h}" for h in hints))

        system_text = "\n".join(parts)

        # 必须返回 [SystemMessage, ...对话历史]，框架不会自动拼接 messages
        messages = state.get("messages", [])
        return [SystemMessage(content=system_text)] + list(messages)

    return prompt_fn


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
    if t.startswith("transferring") or t.startswith("successfully transferred"):
        return True
    if "transfer_back_to_supervisor" in t:
        return True
    if t.startswith('{"') or t.startswith('[{"'):
        return True
    return False


def _visible(meta: dict) -> bool:
    """只展示 supervisor 节点的消息，子 agent 的消息不直接流给前端。

    子 agent 结果由 supervisor 聚合后统一输出，避免内容重复和泄露。
    """
    node = meta.get("langgraph_node", "")
    return node == "supervisor"


_NODE_HINTS = {
    "weather_expert": "天气专家正在调研...",
    "route_expert": "路线专家正在规划...",
    "poi_expert": "正在搜索周边...",
    "email_expert": "正在准备邮件...",
    "train_expert": "正在查询火车票...",
    "flight_expert": "正在查询航班...",
    "hotel_expert": "正在查询住宿...",
}


async def _emit_deltas(text: str):
    """把上游整段消息拆成前端可见的 delta。"""
    step = 1 if len(text) < 80 else 3
    for index in range(0, len(text), step):
        yield sse_event("message_delta", text[index:index + step])
        await asyncio.sleep(0.005)


# ---- HangoutSupervisor ----

class HangoutSupervisor:
    def __init__(self):
        self.graph = None

    async def init(self):
        logger.info("HangoutSupervisor 初始化中...")
        await session_manager.init()

        tools = await get_hangout_tools()

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
        if tools["flight"]:
            agents.append(create_flight_agent(tools["flight"]))
            logger.info("飞机票专家已启用")
        if tools["hotel"]:
            agents.append(create_hotel_agent(tools["hotel"]))
            logger.info("住宿专家已启用")

        # Supervisor 工具（返回 Command 的工具会自动更新 State）
        sup_tools = [
            update_trip_info,
            mark_weather_result,
            mark_trip_type,
            ask_weather_concern,
            save_final_plan,
        ]
        for name in ("maps_geo", "maps_distance", "maps_schema_personal_map"):
            t = next((t for t in tools["supervisor"] if t.name == name), None)
            if t:
                sup_tools.append(t)

        model = init_chat_model("deepseek-chat", streaming=True)
        self.graph = create_supervisor(
            agents=agents,
            model=model,
            prompt=_make_prompt_fn(),
            tools=sup_tools,
            state_schema=HangoutState,
            parallel_tool_calls=True,
            output_mode="full_history",
        ).compile(
            checkpointer=session_manager.checkpointer,
            store=session_manager.store,
        )
        logger.info(f"HangoutSupervisor 初始化完成 ✓ ({len(agents)} 个子 Agent)")

    async def close(self):
        await session_manager.close()

    async def generate_sse(self, thread_id: str, message: str, interrupt_decision: dict | None = None):
        config = {"configurable": {"thread_id": thread_id, "user_id": "default"}}
        if not interrupt_decision:
            if _is_greeting(message):
                async for event in _emit_deltas("你好，我可以帮你规划出行。你想哪天去哪里玩？"):
                    yield event
                yield sse_event("done", "")
                return
        inp = (
            Command(resume={"decisions": [dict(interrupt_decision)]})
            if interrupt_decision
            else {"messages": [{"role": "user", "content": message}]}
        )

        try:
            seen_hints = set()
            async for chunk in self.graph.astream(inp, config=config, stream_mode=["messages", "updates"]):
                kind, data = _unpack(chunk)

                if kind == "messages":
                    token, meta = data

                    # ── 状态提示：节点一开始工作就发，不等完成 ──
                    node = meta.get("langgraph_node", "")
                    hint = _NODE_HINTS.get(node)
                    if hint and hint not in seen_hints:
                        seen_hints.add(hint)
                        yield sse_event("status", hint)

                    # ── 只展示 supervisor 的文字，子 agent 不直接流 ──
                    if token.__class__.__name__ in ("AIMessage", "AIMessageChunk") and _visible(meta):
                        t = _text(getattr(token, "content", "")).strip()
                        if t and not _is_noise(t):
                            async for event in _emit_deltas(t):
                                yield event

                elif kind == "updates":
                    for item in _interrupts(data):
                        val = serialize(item.value if hasattr(item, "value") else item)
                        if isinstance(val, list) and len(val) == 1:
                            val = val[0]
                        yield sse_json_event("interrupt", {"type": "interrupt", "interrupt": val})

            yield sse_event("done", "")
        except Exception as exc:
            logger.error(f"SSE 流错误: {exc}", exc_info=True)
            yield sse_event("error", _format_exception(exc))

    async def get_messages(self, thread_id: str) -> dict:
        return await session_manager.get_messages(self.graph, thread_id)

    async def clear_messages(self, thread_id: str):
        await session_manager.clear_messages(thread_id)


def _is_greeting(message: str) -> bool:
    text = (message or "").strip().lower()
    return text in {"你好", "你好呀", "您好", "hi", "hello", "哈喽", "嗨"}


def _format_exception(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        lines = [f"{type(exc).__name__}: {exc}"]
        for index, inner in enumerate(exc.exceptions, start=1):
            lines.append(f"[{index}] {type(inner).__name__}: {inner}")
        return "\n".join(lines)
    tb = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return tb or str(exc)


hangout_supervisor = HangoutSupervisor()
