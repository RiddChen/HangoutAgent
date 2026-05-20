# app/agents/travel/agents.py
"""子 Agent 定义：天气、路线、POI、邮件、12306（预留）、住宿（预留）。"""

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from app.agents.travel.prompts import (
    WEATHER_PROMPT, ROUTE_PROMPT, POI_PROMPT,
    EMAIL_PROMPT, TRAIN_PROMPT, HOTEL_PROMPT,
)
from app.agents.travel.tools import get_saved_plan, send_invite_email


def _model():
    return init_chat_model("deepseek-chat", streaming=True)


def create_weather_agent(tools: list):
    """天气专家：墨迹天气全套 + 高德天气备用。"""
    return create_agent(
        _model(), tools=tools,
        name="weather_expert",
        system_prompt=WEATHER_PROMPT,
    )


def create_route_agent(tools: list):
    """路线专家：地理编码 + 多种交通路线规划。"""
    return create_agent(
        _model(), tools=tools,
        name="route_expert",
        system_prompt=ROUTE_PROMPT,
    )


def create_poi_agent(tools: list):
    """POI 专家：周边搜索、关键词搜索、详情查询。"""
    return create_agent(
        _model(), tools=tools,
        name="poi_expert",
        system_prompt=POI_PROMPT,
    )


def create_email_agent():
    """邮件专家：读方案 + 发邀请邮件（interrupt 确认）。"""
    return create_agent(
        _model(),
        tools=[get_saved_plan, send_invite_email],
        name="email_expert",
        system_prompt=EMAIL_PROMPT,
    )


def create_train_agent(tools: list):
    """12306 专家：查火车票（跨城时启用）。"""
    return create_agent(
        _model(), tools=tools,
        name="train_expert",
        system_prompt=TRAIN_PROMPT,
    )


def create_hotel_agent(tools: list):
    """住宿专家：查酒店（跨城时启用）。"""
    return create_agent(
        _model(), tools=tools,
        name="hotel_expert",
        system_prompt=HOTEL_PROMPT,
    )
