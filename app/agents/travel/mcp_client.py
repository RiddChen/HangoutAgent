import os
from datetime import timedelta

from dotenv import load_dotenv
load_dotenv()

from langchain_mcp_adapters.client import MultiServerMCPClient


async def get_amap_tools() -> list:
    """获取高德 MCP 的全部工具（本地 npx 启动）。"""
    api_key = os.getenv("AMAP_MAPS_API_KEY")
    if not api_key:
        raise ValueError("AMAP_MAPS_API_KEY 未设置！")

    client = MultiServerMCPClient({
        "amap-maps": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {
                "AMAP_MAPS_API_KEY": api_key,
            },
        }
    })
    return await client.get_tools()


async def get_weather_mcp_tools() -> list:
    """获取独立天气 MCP 工具。

    配置了 WEATHER_MCP_URL 时优先使用外部天气 MCP；未配置则返回空列表，
    由 split_tools 回退到高德 maps_weather。
    """
    url = os.getenv("WEATHER_MCP_URL")
    if not url:
        return []

    api_key = (
        os.getenv("WEATHER_MCP_API_KEY")
        or os.getenv("BIGMODEL_API_KEY")
        or os.getenv("ZHIPUAI_API_KEY")
    )
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client = MultiServerMCPClient({
        "weather": {
            "transport": os.getenv("WEATHER_MCP_TRANSPORT", "http"),
            "url": url,
            "headers": headers,
            "timeout": timedelta(seconds=int(os.getenv("WEATHER_MCP_TIMEOUT", "30"))),
            "sse_read_timeout": timedelta(
                seconds=int(os.getenv("WEATHER_MCP_SSE_READ_TIMEOUT", "300"))
            ),
        }
    })
    return await client.get_tools()


async def get_travel_tools() -> dict:
    """获取出行流程需要的工具，并按 Agent 职责分组。"""
    amap_tools = await get_amap_tools()
    weather_tools = await get_weather_mcp_tools()
    return split_tools(amap_tools, weather_tools=weather_tools)


def split_tools(all_tools: list, weather_tools: list | None = None) -> dict:
    """把 MCP 工具按 Agent 职责分组。

    返回:
        {
            "coordinator": [...],   # maps_geo, maps_ip_location
            "weather": [...],       # maps_weather
            "poi": [...],           # maps_text_search, maps_around_search, maps_search_detail
            "route": [...],         # maps_direction_*, maps_distance
        }
    """
    groups = {
        "coordinator": {"maps_geo", "maps_ip_location", "maps_regeocode"},
        "weather": {"maps_weather"},
        "poi": {"maps_text_search", "maps_around_search", "maps_search_detail"},
        "route": {
            "maps_direction_driving",
            "maps_direction_transit_integrated",
            "maps_direction_walking",
            "maps_bicycling",
            "maps_distance",
        },
    }

    result = {k: [] for k in groups}
    tool_index = {t.name: t for t in all_tools}

    for group_name, tool_names in groups.items():
        for name in tool_names:
            if name in tool_index:
                result[group_name].append(tool_index[name])

    if weather_tools:
        result["weather"] = weather_tools

    return result
