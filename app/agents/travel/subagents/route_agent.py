from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.travel.prompts import ROUTE_PROMPT
from app.agents.travel.state import TravelState


_agent = None


def create_route_agent(tools: list):
    """用 MCP 的路线规划工具创建 Route Agent。"""
    global _agent
    _agent = create_agent(
        "deepseek-chat",
        tools=tools,
        name="route_agent",
        state_schema=TravelState,
        system_prompt=ROUTE_PROMPT,
    )


async def route_node(state: TravelState) -> dict:
    """StateGraph 节点：调用 Route Agent，结果写入 state.route_result。"""
    origin = state.get("origin", "")
    dest = state.get("destination", "")

    try:
        result = await _agent.ainvoke({
            "messages": [HumanMessage(content=f"规划从 {origin} 到 {dest} 的交通路线")],
        })
    except Exception as exc:
        return {"route_result": f"路线调研暂时失败：{exc}"}

    last_ai = _extract_last_ai(result)
    return {"route_result": last_ai}


def _extract_last_ai(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
