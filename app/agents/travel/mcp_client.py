# app/agents/travel/mcp_client.py
"""MCP 工具获取与分组。"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.common.logger import logger

load_dotenv()


# ---- MCP 连接 ----

async def get_amap_tools() -> list:
    """获取高德地图 MCP 工具。"""
    api_key = (
        os.getenv("GAODE_MCP_API_KEY")
        or os.getenv("WEATHER_MCP_API_KEY")
        or os.getenv("BIGMODEL_API_KEY")
    )
    if not api_key:
        raise ValueError("GAODE_MCP_API_KEY 未设置！")

    url = os.getenv("GAODE_MCP_URL") or (
        "https://open.bigmodel.cn/api/mcp-broker/proxy/"
        f"gaode-map/mcp?Authorization={api_key}"
    )
    client = MultiServerMCPClient({
        "gaode-map": {
            "transport": os.getenv("GAODE_MCP_TRANSPORT", "http"),
            "url": url,
            "headers": {"Authorization": f"Bearer {api_key}"},
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(seconds=300),
        }
    })
    return await client.get_tools()


async def get_weather_mcp_tools() -> list:
    """获取墨迹天气 MCP 工具。未配置则返回空列表，回退到高德天气。"""
    url = os.getenv("WEATHER_MCP_URL")
    if not url:
        return []

    api_key = os.getenv("WEATHER_MCP_API_KEY") or os.getenv("BIGMODEL_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    client = MultiServerMCPClient({
        "weather": {
            "transport": os.getenv("WEATHER_MCP_TRANSPORT", "http"),
            "url": url,
            "headers": headers,
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(seconds=300),
        }
    })
    return await client.get_tools()


async def get_train_mcp_tools() -> list:
    """获取 12306 火车票 MCP 工具。未配置则返回空列表。"""
    url = os.getenv("TRAIN_MCP_URL")
    if not url:
        logger.info("TRAIN_MCP_URL 未配置，12306 专家不可用")
        return []

    api_key = os.getenv("TRAIN_MCP_API_KEY") or os.getenv("BIGMODEL_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    client = MultiServerMCPClient({
        "train": {
            "transport": os.getenv("TRAIN_MCP_TRANSPORT", "http"),
            "url": url,
            "headers": headers,
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(seconds=300),
        }
    })
    return await client.get_tools()


async def get_hotel_mcp_tools() -> list:
    """获取住宿 MCP 工具。未配置则返回空列表。"""
    url = os.getenv("HOTEL_MCP_URL")
    if not url:
        logger.info("HOTEL_MCP_URL 未配置，住宿专家不可用")
        return []

    api_key = os.getenv("HOTEL_MCP_API_KEY") or os.getenv("BIGMODEL_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    client = MultiServerMCPClient({
        "hotel": {
            "transport": os.getenv("HOTEL_MCP_TRANSPORT", "http"),
            "url": url,
            "headers": headers,
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(seconds=300),
        }
    })
    return await client.get_tools()


# ---- 工具分组 ----

def split_tools(amap_tools: list, weather_tools: list) -> dict:
    """按 Agent 职责分组高德+墨迹工具。"""
    tool_index = {t.name: t for t in amap_tools}

    groups = {
        "supervisor": ["maps_geo", "maps_distance", "maps_schema_personal_map"],
        "weather": ["maps_weather"],  # 回退用，优先用墨迹
        "route": [
            "maps_geo",
            "maps_direction_transit_integrated",
            "maps_direction_driving",
            "maps_direction_walking",
            "maps_direction_bicycling",
            "maps_distance",
        ],
        "poi": [
            "maps_geo",
            "maps_around_search",
            "maps_text_search",
            "maps_search_detail",
        ],
    }

    result = {}
    for group, names in groups.items():
        result[group] = [tool_index[n] for n in names if n in tool_index]

    # 墨迹天气覆盖高德天气
    if weather_tools:
        result["weather"] = weather_tools

    return result


async def get_travel_tools() -> dict:
    """获取并分组所有 MCP 工具。

    返回:
        {
            "supervisor": [...],   # maps_geo, maps_distance, maps_schema_personal_map
            "weather": [...],      # 墨迹全套 或 maps_weather
            "route": [...],        # maps_geo + 4个 direction + maps_distance
            "poi": [...],          # maps_geo + search 工具
            "train": [...],        # 12306 工具（未配置则空）
            "hotel": [...],        # 住宿工具（未配置则空）
        }
    """
    amap_tools = await get_amap_tools()
    weather_tools = await get_weather_mcp_tools()
    train_tools = await get_train_mcp_tools()
    hotel_tools = await get_hotel_mcp_tools()

    result = split_tools(amap_tools, weather_tools)
    result["train"] = train_tools
    result["hotel"] = hotel_tools

    logger.info(
        f"MCP 工具加载完成: weather={len(result['weather'])}, "
        f"route={len(result['route'])}, poi={len(result['poi'])}, "
        f"train={len(result['train'])}, hotel={len(result['hotel'])}"
    )
    return result
