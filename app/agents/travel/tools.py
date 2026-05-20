# app/agents/travel/tools.py
"""自定义工具：方案保存、方案邮件发送。"""

from langchain_core.tools import tool
from langgraph.store.base import BaseStore
from langgraph.types import interrupt


# ---- store 引用（supervisor init 时注入） ----
_store: BaseStore | None = None


def set_store(store: BaseStore):
    global _store
    _store = store


@tool
async def save_travel_plan(plan: str) -> str:
    """保存最终确定的出行方案。用户选定交通方式后调用。

    参数：
    - plan: 完整方案文本（目的地、日期、出发地、天气、交通方式及耗时、周边推荐）
    """
    if not _store:
        return "store 未初始化。"
    await _store.aput(
        namespace=("travel_plans", "default"),
        key="current_plan",
        value={"plan": plan},
    )
    return "出行方案已保存。"


@tool
async def send_plan_email(to_email: str) -> str:
    """将已保存的出行方案发送到用户邮箱。发送前会暂停让用户确认方案内容。

    参数：
    - to_email: 用户邮箱地址
    """
    # 1. 从 store 读取方案
    if not _store:
        return "store 未初始化。"
    items = await _store.asearch(("travel_plans", "default"))
    if not items:
        return "暂无已保存的方案，请先完成出行规划。"
    plan = items[0].value["plan"]

    # 2. interrupt：展示方案让用户确认
    result = interrupt({
        "action_requests": [{
            "name": "send_plan_email",
            "args": {
                "to_email": to_email,
                "subject": "CityBuddy 出行方案",
                "body": plan,
            },
        }]
    })

    # 3. 解析用户决策
    decisions = result.get("decisions", []) if isinstance(result, dict) else []
    if not decisions:
        return "用户取消了发送。"

    decision = decisions[0] if isinstance(decisions[0], dict) else {}

    if decision.get("type") == "approve":
        # 确认 → 发送邮件
        from app.integrations.gmail_tools import get_gmail_tools
        gmail_tools = get_gmail_tools()
        send_tool = next(
            (t for t in gmail_tools if t.name == "send_gmail_message"), None
        )
        if not send_tool:
            return "Gmail 未配置，无法发送。请先完成 OAuth 授权。"
        try:
            await send_tool.ainvoke({
                "to": to_email,
                "subject": "CityBuddy 出行方案",
                "message": plan,
            })
            return f"出行方案已发往 {to_email}，请查收！"
        except Exception as e:
            return f"邮件发送失败：{e}"
    else:
        # 拒绝 → 返回修改原因给 supervisor
        reason = decision.get("message", "")
        return f"用户要求修改方案：{reason}" if reason else "用户取消了发送。"
