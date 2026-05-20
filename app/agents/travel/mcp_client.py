# app/agents/travel/mcp_client.py
"""MCP 工具获取与分组。"""

import asyncio
import os
from datetime import timedelta
from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.common.logger import logger

load_dotenv()


# ---- QPS 重试包装 ----

_QPS_ERRORS = ("CUQPS_HAS_EXCEEDED_THE_LIMIT", "QPS_EXCEEDED", "rate limit")
_MAX_RETRIES = 3
_RETRY_DELAY = 1.5  # 秒


def _is_qps_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(e in msg for e in _QPS_ERRORS)


def _wrap_tool_with_retry(tool: BaseTool) -> BaseTool:
    """给 MCP 工具的 _arun 加 QPS 重试。"""
    original_arun = tool._arun

    async def retrying_arun(*args, **kwargs):
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await original_arun(*args, **kwargs)
            except Exception as e:
                if _is_qps_error(e) and attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAY * attempt
                    logger.warning(f"QPS 超限，{delay}s 后重试 ({attempt}/{_MAX_RETRIES}): {tool.name}")
                    await asyncio.sleep(delay)
                else:
                    raise

    # _arun 是普通方法（不是 Pydantic field），可以直接替换
    tool._arun = retrying_arun
    return tool


def _wrap_tools_with_retry(tools: list) -> list:
    return [_wrap_tool_with_retry(t) for t in tools]


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
    """获取 12306 火车票 MCP 工具。默认使用 npx 12306-mcp stdio。"""
    transport = os.getenv("TRAIN_MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        command = os.getenv("TRAIN_MCP_COMMAND", "npx")
        args = os.getenv("TRAIN_MCP_ARGS", "-y 12306-mcp").split()
        client = MultiServerMCPClient({
            "12306-mcp": {
                "transport": "stdio",
                "command": command,
                "args": args,
            }
        })
        return await client.get_tools()

    url = os.getenv("TRAIN_MCP_URL")
    if not url:
        logger.info("TRAIN_MCP_URL 未配置，12306 专家不可用")
        return []

    api_key = (
        os.getenv("TRAIN_MCP_API_KEY")
        or os.getenv("BIGMODEL_API_KEY")
        or os.getenv("WEATHER_MCP_API_KEY")
    )
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


async def get_flight_mcp_tools() -> list:
    """获取飞机票/航班 MCP 工具。默认使用 BigModel aviation MCP。"""
    api_key = (
        os.getenv("FLIGHT_MCP_API_KEY")
        or os.getenv("BIGMODEL_API_KEY")
        or os.getenv("WEATHER_MCP_API_KEY")
    )
    url = os.getenv("FLIGHT_MCP_URL") or (
        "https://open.bigmodel.cn/api/mcp-broker/proxy/"
        f"aviation/mcp?Authorization={api_key}"
        if api_key else ""
    )
    if not url:
        logger.info("FLIGHT_MCP_URL 未配置，飞机票专家不可用")
        return []

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    client = MultiServerMCPClient({
        "flight": {
            "transport": os.getenv("FLIGHT_MCP_TRANSPORT", "http"),
            "url": url,
            "headers": headers,
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(seconds=300),
        }
    })
    return await client.get_tools()


async def get_tavily_mcp_tools() -> list:
    """获取 Tavily/Web Search MCP 工具。未配置则返回空列表。"""
    url = os.getenv("TAVILY_MCP_URL")
    if not url:
        logger.info("TAVILY_MCP_URL 未配置，Web 搜索工具不可用")
        return []

    api_key = os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY_MCP_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    client = MultiServerMCPClient({
        "tavily": {
            "transport": os.getenv("TAVILY_MCP_TRANSPORT", "http"),
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
            "maps_bicycling",
            "maps_distance",
        ],
        "poi": [
            "maps_geo",
            "maps_around_search",
            "maps_text_search",
            # maps_search_detail 需要 poiId，POI 专家没有这个值，移除避免误调
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

    # 可选 MCP：连不上就跳过，不阻塞启动
    try:
        train_tools = await get_train_mcp_tools()
    except Exception as e:
        logger.warning(f"12306 MCP 连接失败，跳过: {e}")
        train_tools = []

    try:
        hotel_tools = await get_hotel_mcp_tools()
    except Exception as e:
        logger.warning(f"住宿 MCP 连接失败，跳过: {e}")
        hotel_tools = []

    try:
        flight_tools = await get_flight_mcp_tools()
    except Exception as e:
        logger.warning(f"航班 MCP 连接失败，跳过: {e}")
        flight_tools = []

    try:
        tavily_tools = await get_tavily_mcp_tools()
    except Exception as e:
        logger.warning(f"Tavily MCP 连接失败，跳过: {e}")
        tavily_tools = []

    result = split_tools(amap_tools, weather_tools)
    result["train"] = train_tools
    result["flight"] = flight_tools
    result["hotel"] = hotel_tools + tavily_tools

    logger.info(
        f"MCP 工具加载完成: weather={len(result['weather'])}, "
        f"route={len(result['route'])}, poi={len(result['poi'])}, "
        f"train={len(result['train'])}, flight={len(result['flight'])}, "
        f"hotel={len(result['hotel'])}"
    )
    return result
