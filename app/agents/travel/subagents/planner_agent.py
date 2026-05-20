import re

from langchain.agents import create_agent
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.agents.travel.prompts import PLANNER_PROMPT
from app.agents.travel.state import TravelState
from app.common.logger import logger


# ---- Handoff 工具 ----

@tool
def send_invite_email(
    runtime: ToolRuntime,
    to_name: str,
    to_email: str,
    plan_summary: str,
) -> Command:
    """当用户确认要发邮件给朋友时，调用此工具。

    参数：
    - to_name: 朋友的名字
    - to_email: 朋友的邮箱
    - plan_summary: 行程摘要（用于邮件内容）
    """
    return Command(
        goto="email_node",
        graph=Command.PARENT,
        update={
            "invitee_name": to_name,
            "invitee_email": to_email,
            "need_email": True,
            "messages": [
                ToolMessage(
                    f"正在转交给邮件助手，准备给 {to_name}（{to_email}）发送邀请...",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        },
    )


# ---- 从对话记录中提取邮箱和名字的辅助函数 ----

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _extract_email_info_from_messages(messages: list) -> tuple[str | None, str | None]:
    """从用户消息中提取邮箱和名字。返回 (email, name)。"""
    email = None
    name = None

    for msg in messages:
        if not isinstance(msg, HumanMessage):
            continue
        text = msg.content if isinstance(msg.content, str) else ""

        # 提取邮箱
        if not email:
            match = _EMAIL_RE.search(text)
            if match:
                email = match.group()

        # 提取名字（常见模式："朋友叫XXX"、"叫XXX"、"名字是XXX"、"发给XXX"）
        if not name:
            for pattern in [
                r"(?:朋友|他|她|ta)\s*(?:叫|是|名字是)\s*(\S+)",
                r"(?:叫|名字是)\s*(\S+)",
                r"发给\s*(\S+?)(?:的|\s|，|,|$)",
            ]:
                m = re.search(pattern, text)
                if m:
                    candidate = m.group(1).strip("，。！？,. ")
                    # 过滤掉邮箱地址被错误匹配为名字
                    if candidate and "@" not in candidate and len(candidate) <= 10:
                        name = candidate
                        break

    return email, name


# ---- Agent + Node ----

_agent = None


def create_planner_agent():
    """创建 Planner Agent（无 MCP 工具，只有 send_invite_email）。"""
    global _agent
    _agent = create_agent(
        "deepseek-chat",
        tools=[send_invite_email],
        name="planner_agent",
        state_schema=TravelState,
        system_prompt=PLANNER_PROMPT,
    )


async def planner_node(state: TravelState) -> dict:
    """StateGraph 节点：综合调研结果生成方案，支持后续多轮对话。"""
    destination = state.get("destination", "")
    origin = state.get("origin", "")
    date = state.get("date_text", "")
    weather = state.get("weather_result", "")
    poi = state.get("poi_result", "")
    route = state.get("route_result", "")
    final_plan = state.get("final_plan", "")

    # ---- 检查是否应该直接走邮件 handoff（不依赖 LLM 调工具）----
    # 从所有用户消息中提取邮箱和名字
    all_messages = state.get("messages", [])
    extracted_email, extracted_name = _extract_email_info_from_messages(all_messages)

    # 用 state 里已有的信息补充
    invitee_name = state.get("invitee_name") or extracted_name
    invitee_email = state.get("invitee_email") or extracted_email

    # 判断是否要发邮件：
    # 核心逻辑——用户同时提供了名字和邮箱，意图就是要发邮件邀请朋友
    # 不需要用户额外说"发邮件"这三个字
    user_wants_email = bool(invitee_name and invitee_email)

    # 如果信息齐全 → 直接 handoff 到 email_node，不再让 LLM 磨叽
    if final_plan and user_wants_email:
        logger.info(
            f"Planner: 直接 handoff 到 email_node "
            f"(name={invitee_name}, email={invitee_email})"
        )
        return {
            "invitee_name": invitee_name,
            "invitee_email": invitee_email,
            "need_email": True,
            "messages": [
                AIMessage(
                    content=f"好的，正在帮你给 {invitee_name}（{invitee_email}）发送邀请邮件... 📧"
                )
            ],
        }

    # ---- 正常 LLM 流程 ----
    # 第一次进 planner：把调研结果打包给它
    if not final_plan:
        input_msg = (
            f"## 调研结果\n\n"
            f"### 天气\n{weather}\n\n"
            f"### 景点推荐\n{poi}\n\n"
            f"### 交通路线\n{route}\n\n"
            f"请根据以上信息生成 2-3 个出行方案。"
        )
    else:
        # 后续对话：收集最近的用户消息作为上下文
        user_msgs = [m for m in all_messages if isinstance(m, HumanMessage)]
        recent_context = "\n".join(
            f"- 用户：{m.content}" for m in user_msgs[-5:]
        )

        input_msg = (
            "你正在继续处理同一个出行企划会话，不要说自己没有历史记录。\n\n"
            f"## 已知出行信息\n"
            f"- 目的地：{destination}\n"
            f"- 出发地：{origin}\n"
            f"- 日期：{date}\n\n"
            f"## 已生成方案\n{final_plan}\n\n"
            f"## 最近的对话记录\n{recent_context}\n\n"
        )
        if invitee_name or invitee_email:
            input_msg += (
                f"## 已收集的邮件信息\n"
                f"- 朋友名字：{invitee_name or '未知'}\n"
                f"- 朋友邮箱：{invitee_email or '未知'}\n\n"
            )
        input_msg += (
            "请基于以上上下文继续回复。\n"
            "重要规则：\n"
            "- 不要重复询问用户已经提供过的信息（看对话记录）\n"
            "- 如果名字和邮箱都已知，立即调用 send_invite_email 工具\n"
            "- send_invite_email 的 plan_summary 参数用已生成方案的摘要\n"
            "- 不要重新询问目的地、出发地或日期\n"
        )

    result = await _agent.ainvoke({
        "messages": [HumanMessage(content=input_msg)],
    })

    last_ai = _extract_last_ai(result)
    updates = {"messages": [AIMessage(content=last_ai)]}
    if not final_plan:
        updates["final_plan"] = last_ai
    for key in ("invitee_name", "invitee_email", "need_email"):
        if key in result:
            updates[key] = result[key]
    return updates


def _extract_last_ai(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
