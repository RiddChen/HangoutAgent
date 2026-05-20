from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.travel.prompts import WEATHER_PROMPT
from app.agents.travel.state import TravelState


_agent = None


def create_weather_agent(tools: list):
    """用 MCP 的 maps_weather 工具创建 Weather Agent。"""
    global _agent
    _agent = create_agent(
        "deepseek-chat",
        tools=tools,
        name="weather_agent",
        state_schema=TravelState,
        system_prompt=WEATHER_PROMPT,
    )


async def weather_node(state: TravelState) -> dict:
    """StateGraph 节点：调用 Weather Agent，结果写入 state.weather_result。"""
    dest = state.get("destination", "")
    date = state.get("date_text", "本周末")

    try:
        result = await _agent.ainvoke({
            "messages": [HumanMessage(content=f"查询 {dest} {date} 的天气")],
        })
    except Exception as exc:
        return {"weather_result": f"天气调研暂时失败：{exc}"}

    last_ai = _extract_last_ai(result)
    return {"weather_result": last_ai}


def _extract_last_ai(result: dict) -> str:
    """从 agent 结果中提取最后一条 AI 消息。"""
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
