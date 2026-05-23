# app/agents/hangout/tools.py
"""Hangout 自定义工具 + HangoutState 定义。

架构：
- 结构化字段（目的地/日期/天气状态等）→ State，每轮自动注入 prompt
- 方案全文（太长）→ Store，按 thread 隔离
- 流程控制（天气未查不能查路线等）→ State 字段 + prompt 阶段提示
"""

from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.store.base import BaseStore
from langgraph.types import Command, interrupt
from langchain.agents import AgentState


# ---- Store（方案全文用 Store，结构化字段用 State） ----

_store: BaseStore | None = None


def set_store(store: BaseStore):
    """由 supervisor 初始化时注入。"""
    global _store
    _store = store


# ---- State 定义 ----

class HangoutState(AgentState):
    """扩展 AgentState，增加出行相关字段。

    所有字段都有默认值，旧 checkpoint 加载不会报错。
    非 Annotated 字段使用 replace 语义（最后写入覆盖）。
    """
    destination: str = ""
    date: str = ""
    origin: str = ""
    weather_checked: bool = False
    weather_ok: bool = False
    weather_summary: str = ""
    trip_type: str = ""                # "same_city" | "cross_city"
    transport_preference: str = ""
    dining_preference: str = ""
    nearby_preference: str = ""
    hotel_needed: str = ""
    plan_saved: bool = False


# ═══════════════════════════════════════
# Supervisor 工具（返回 Command 更新 State）
# ═══════════════════════════════════════

@tool
def update_trip_info(
    tool_call_id: Annotated[str, InjectedToolCallId],
    destination: str = "",
    date: str = "",
    origin: str = "",
    transport_preference: str = "",
    dining_preference: str = "",
    nearby_preference: str = "",
    hotel_needed: str = "",
) -> Command:
    """保存或更新用户出行信息。每拿到一个字段就调用一次，只传用户明确说出的字段。"""
    labels = {
        "destination": "目的地", "date": "日期", "origin": "出发地",
        "transport_preference": "交通偏好", "dining_preference": "用餐偏好",
        "nearby_preference": "周边偏好", "hotel_needed": "住宿需求",
    }
    raw = {
        "destination": destination, "date": date, "origin": origin,
        "transport_preference": transport_preference,
        "dining_preference": dining_preference,
        "nearby_preference": nearby_preference,
        "hotel_needed": hotel_needed,
    }
    updates = {k: v for k, v in raw.items() if v}
    if not updates:
        return Command(update={
            "messages": [ToolMessage("没有需要更新的字段。", tool_call_id=tool_call_id)],
        })

    summary = "、".join(f"{labels[k]}={v}" for k, v in updates.items())
    return Command(update={
        **updates,
        "messages": [ToolMessage(f"已更新出行信息：{summary}", tool_call_id=tool_call_id)],
    })


@tool
def mark_weather_result(
    tool_call_id: Annotated[str, InjectedToolCallId],
    weather_summary: str,
    weather_ok: bool,
) -> Command:
    """天气专家返回结果后调用。记录天气结论到状态。

    参数：
    - weather_summary: 一句话概括天气（如"晴，26°C，适合出行"）
    - weather_ok: True=适合出行，False=有风险
    """
    status = "适合出行" if weather_ok else "不太理想"
    return Command(update={
        "weather_checked": True,
        "weather_ok": weather_ok,
        "weather_summary": weather_summary,
        "messages": [ToolMessage(
            f"天气已标记：{status}（{weather_summary}）", tool_call_id=tool_call_id,
        )],
    })


@tool
def mark_trip_type(
    tool_call_id: Annotated[str, InjectedToolCallId],
    trip_type: str,
) -> Command:
    """判断同城/跨城后调用。trip_type: "same_city" 或 "cross_city"。"""
    label = "同城" if trip_type == "same_city" else "跨城"
    return Command(update={
        "trip_type": trip_type,
        "messages": [ToolMessage(f"已标记为{label}出行。", tool_call_id=tool_call_id)],
    })


# ═══════════════════════════════════════
# 天气确认（interrupt，返回字符串）
# ═══════════════════════════════════════

@tool
def ask_weather_concern(weather_summary: str) -> str:
    """天气不理想时，暂停询问用户是否在意以及是否换时间。"""
    result = interrupt({
        "type": "weather_confirm",
        "message": "天气看起来不太理想，你是否在意？如果在意，可以换个时间。",
        "weather_summary": weather_summary,
    })
    if _is_approved(result):
        return "用户表示不介意当前天气，可以继续规划。"
    message = _decision_message(result)
    return f"用户在意天气，需要调整时间。用户补充：{message}" if message else "用户在意天气，请询问新的出行时间。"


# ═══════════════════════════════════════
# 方案保存与邮件（用 Store 存全文）
# ═══════════════════════════════════════

@tool
async def save_final_plan(
    config: RunnableConfig,
    tool_call_id: Annotated[str, InjectedToolCallId],
    plan: str,
) -> Command:
    """保存用户审核满意后的最终方案。方案全文存入 Store，状态标记 plan_saved。"""
    if _store:
        await _store.aput(_plan_namespace(config), "final", {"plan": plan})
    return Command(update={
        "plan_saved": True,
        "messages": [ToolMessage("最终出行方案已保存。", tool_call_id=tool_call_id)],
    })


@tool
async def get_final_plan(config: RunnableConfig) -> str:
    """读取当前会话保存的最终出行方案（email_expert 调用）。"""
    if not _store:
        return "store 未初始化。"
    data = await _get_value(_plan_namespace(config), "final")
    return data.get("plan", "") or "暂无已保存的最终方案。"


@tool
async def send_final_plan_email(
    config: RunnableConfig,
    to_email: str = "googeorge1212@gmail.com",
    subject: str = "你的出行计划",
) -> str:
    """把已保存的最终出行方案发送到指定邮箱。发送前会 interrupt 让用户确认。"""
    if not _store:
        return "store 未初始化。"
    plan_data = await _get_value(_plan_namespace(config), "final")
    plan = plan_data.get("plan", "")
    if not plan:
        return "暂无已保存的最终方案，请先调用 save_final_plan。"

    decision = interrupt({
        "type": "email_confirm",
        "action_requests": [{
            "name": "send_final_plan_email",
            "args": {
                "to_email": to_email,
                "subject": subject,
                "body": plan,
            },
        }],
    })

    if not _is_approved(decision):
        reason = _decision_message(decision)
        return f"用户拒绝发送邮件：{reason}" if reason else "用户取消了邮件发送。"

    from app.integrations.gmail_tools import get_gmail_tools

    gmail_tools = get_gmail_tools()
    send_tool = next((t for t in gmail_tools if t.name == "send_gmail_message"), None)
    if not send_tool:
        return "Gmail 未配置，无法发送邮件。请先完成 OAuth 授权。"

    await send_tool.ainvoke({
        "to": to_email,
        "subject": subject,
        "message": plan,
    })
    return f"最终出行方案已发送到 {to_email}。"


# ═══════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════

def _user_id(config: RunnableConfig | None) -> str:
    if not config:
        return "default"
    return config.get("configurable", {}).get("user_id", "default")


def _thread_id(config: RunnableConfig | None) -> str:
    if not config:
        return "default"
    return config.get("configurable", {}).get("thread_id", "default")


def _plan_namespace(config: RunnableConfig | None) -> tuple[str, str, str]:
    return ("travel_plan", _user_id(config), _thread_id(config))


async def _get_value(namespace: tuple[str, ...], key: str) -> dict[str, Any]:
    if not _store:
        return {}
    item = await _store.aget(namespace, key)
    return dict(item.value) if item and item.value else {}


def _is_approved(result: Any) -> bool:
    if isinstance(result, dict):
        if result.get("type") == "approve":
            return True
        decisions = result.get("decisions", [])
        return bool(
            decisions
            and isinstance(decisions[0], dict)
            and decisions[0].get("type") == "approve"
        )
    return False


def _decision_message(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if result.get("message"):
        return str(result["message"])
    decisions = result.get("decisions", [])
    if decisions and isinstance(decisions[0], dict):
        return str(decisions[0].get("message", ""))
    return ""
