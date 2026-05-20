from typing import NotRequired

from langchain.agents import AgentState


class TravelState(AgentState):
    """Travel 多 Agent 流程的共享状态。

    继承 AgentState，自带 messages 字段（对话历史）。
    下面的字段是各个 Agent 读写的共享数据。
    """

    # ---- Coordinator 写入 ----
    destination: NotRequired[str]       # 目的地，如"西溪湿地"
    origin: NotRequired[str]            # 出发地，如"城西银泰"
    date_text: NotRequired[str]         # 日期描述，如"本周末"

    # ---- 并发 Subagent 写入 ----
    weather_result: NotRequired[str]    # Weather Agent 的调研结果
    poi_result: NotRequired[str]        # POI Agent 的调研结果
    route_result: NotRequired[str]      # Route Agent 的调研结果

    # ---- Planner 写入 ----
    final_plan: NotRequired[str]        # 最终方案文本

    # ---- Email Handoff 用 ----
    invitee_name: NotRequired[str]      # 朋友名字
    invitee_email: NotRequired[str]     # 朋友邮箱
    need_email: NotRequired[bool]       # 是否需要发邮件
    email_thread_id: NotRequired[str]   # EmailAgent 子会话 thread_id
    email_interrupt: NotRequired[dict]  # EmailAgent HITL 中断信息
