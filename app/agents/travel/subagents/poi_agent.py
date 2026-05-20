from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.travel.prompts import POI_PROMPT
from app.agents.travel.state import TravelState


_agent = None


def create_poi_agent(tools: list):
    """用 MCP 的搜索工具创建 POI Agent。"""
    global _agent
    _agent = create_agent(
        "deepseek-chat",
        tools=tools,
        name="poi_agent",
        state_schema=TravelState,
        system_prompt=POI_PROMPT,
    )


async def poi_node(state: TravelState) -> dict:
    """StateGraph 节点：调用 POI Agent，结果写入 state.poi_result。"""
    dest = state.get("destination", "")

    try:
        result = await _agent.ainvoke({
            "messages": [HumanMessage(content=f"搜索 {dest} 及周边值得去的景点、餐厅。优先使用搜索结果摘要，不要逐个查询详情。")],
        })
    except Exception as exc:
        return {"poi_result": f"景点调研暂时失败：{exc}"}

    last_ai = _extract_last_ai(result)
    return {"poi_result": last_ai}


def _extract_last_ai(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
