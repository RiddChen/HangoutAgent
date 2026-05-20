import json
import os

import aiosqlite
from langchain.agents import create_agent
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, Send, interrupt

from app.agents.travel.mcp_client import get_travel_tools
from app.agents.travel.prompts import COORDINATOR_PROMPT
from app.agents.travel.state import TravelState
from app.agents.travel.subagents import (
    create_planner_agent,
    create_poi_agent,
    create_route_agent,
    create_weather_agent,
    planner_node,
    poi_node,
    route_node,
    weather_node,
)
from app.common.logger import logger


# ================================================================
# Coordinator 的工具
# ================================================================

@tool
def save_trip_info(
    runtime: ToolRuntime,
    destination: str,
    origin: str,
    date_text: str = "本周末",
) -> Command:
    """当收集到目的地、出发地、日期后，调用此工具保存信息并启动调研。

    参数：
    - destination: 目的地（如"西溪湿地"）
    - origin: 出发地（如"城西银泰"）
    - date_text: 日期描述（如"本周末"、"下周六"）
    """
    return Command(
        update={
            "destination": destination,
            "origin": origin,
            "date_text": date_text,
            "messages": [
                ToolMessage(
                    f"已保存：去{destination}，从{origin}出发，{date_text}。",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


# ================================================================
# Email 节点（直接用 interrupt，不经过 EmailAgent LLM）
# ================================================================

def _get_send_gmail_tool():
    """懒加载 Gmail 发送工具。"""
    from app.integrations.gmail_tools import get_gmail_tools
    tools = get_gmail_tools()
    for t in tools:
        if t.name == "send_gmail_message":
            return t
    return None


async def email_node(state: TravelState) -> dict:
    """Email 节点：构造邮件 → interrupt 让用户确认 → 发送。"""
    to_name = state.get("invitee_name", "朋友")
    to_email = state.get("invitee_email", "")
    destination = state.get("destination", "旅行")
    plan = state.get("final_plan", "")

    # 1. 构造邮件内容
    subject = f"出行邀请 - 一起去{destination}吧！"
    body = (
        f"Hi {to_name}，\n\n"
        f"我计划了一趟出行，想邀请你一起！以下是行程安排：\n\n"
        f"{plan}\n\n"
        f"期待你的回复！😊"
    )

    # 2. interrupt —— 暂停图，把邮件预览发给前端，等用户确认
    decision = interrupt({
        "type": "email_confirm",
        "to": to_email,
        "to_name": to_name,
        "subject": subject,
        "body": body,
    })

    logger.info(f"Email interrupt decision: {decision}")

    # 3. 用户确认后继续
    if isinstance(decision, dict) and decision.get("type") == "approve":
        # 调用 Gmail API 发送
        send_tool = _get_send_gmail_tool()
        if send_tool:
            try:
                result = send_tool.invoke({
                    "to": to_email,
                    "subject": subject,
                    "message": body,
                })
                logger.info(f"Gmail 发送成功: {result}")
                return {
                    "messages": [AIMessage(content=f"✅ 邮件已成功发送给 {to_name}（{to_email}）！")],
                    "need_email": False,
                }
            except Exception as e:
                logger.error(f"Gmail 发送失败: {e}", exc_info=True)
                return {
                    "messages": [AIMessage(content=f"❌ 邮件发送失败：{e}")],
                    "need_email": False,
                }
        else:
            return {
                "messages": [AIMessage(content="❌ Gmail 工具未配置，无法发送邮件。")],
                "need_email": False,
            }
    else:
        # 用户拒绝
        reason = decision.get("message", "") if isinstance(decision, dict) else ""
        msg = "📝 已取消邮件发送。"
        if reason:
            msg += f"你的反馈：{reason}"
        return {
            "messages": [AIMessage(content=msg)],
            "need_email": False,
        }


# ================================================================
# 条件边
# ================================================================

def should_research(state: TravelState):
    """coordinator 之后：信息齐了且没调研过 → 并发 fan-out，否则 → END。"""
    has_info = state.get("destination") and state.get("origin")
    not_researched = not state.get("weather_result")
    if has_info and not_researched:
        return [
            Send("weather_node", state),
            Send("poi_node", state),
            Send("route_node", state),
        ]
    return END


def after_planner(state: TravelState):
    """planner 之后：需要发邮件 → email_node，否则 → END。"""
    if state.get("need_email"):
        return "email_node"
    return END


def route_entry(state: TravelState):
    """已有方案后的用户追问直接交给 Planner 处理。"""
    if state.get("final_plan") or state.get("weather_result") or state.get("poi_result") or state.get("route_result"):
        return "planner_node"
    return "coordinator"


def entry_node(state: TravelState) -> dict:
    """Graph 入口占位节点，用于根据当前状态分流。"""
    return {}


# ================================================================
# TravelPipeline 类
# ================================================================

class TravelPipeline:
    """Travel Multi-Agent Pipeline。

    跟 EmailAgent 一个套路：
    - init() 启动时调用，构建 StateGraph
    - generate_sse() 处理每次用户请求，返回 SSE 事件流
    """

    def __init__(self):
        self.graph = None
        self.conn: aiosqlite.Connection | None = None
        self.checkpointer: AsyncSqliteSaver | None = None

    async def init(self):
        """启动时调用：获取 MCP 工具 → 创建 Subagent → 构建 StateGraph。"""
        logger.info("TravelPipeline 开始初始化...")
        await self.init_checkpointer()

        # 1. 获取 MCP 工具并分组
        tools = await get_travel_tools()
        logger.info(
            "MCP 工具获取完成："
            f"coordinator={len(tools['coordinator'])}, "
            f"weather={len(tools['weather'])}, "
            f"poi={len(tools['poi'])}, "
            f"route={len(tools['route'])}"
        )

        # 2. 创建 4 个 Subagent
        create_weather_agent(tools["weather"])
        create_poi_agent(tools["poi"])
        create_route_agent(tools["route"])
        create_planner_agent()
        logger.info("4 个 Subagent 创建完成")

        # 3. 创建 Coordinator（带 maps_geo + save_trip_info）
        coordinator = create_agent(
            "deepseek-chat",
            tools=[*tools["coordinator"], save_trip_info],
            name="coordinator",
            state_schema=TravelState,
            system_prompt=COORDINATOR_PROMPT,
        )

        # 4. 构建 StateGraph
        graph = StateGraph(TravelState)

        # 添加节点
        graph.add_node("entry_node", entry_node)
        graph.add_node("coordinator", coordinator)
        graph.add_node("weather_node", weather_node)
        graph.add_node("poi_node", poi_node)
        graph.add_node("route_node", route_node)
        graph.add_node("planner_node", planner_node)
        graph.add_node("email_node", email_node)

        # 添加边
        graph.set_entry_point("entry_node")

        # 已经生成方案后，后续用户消息直接回到 Planner；
        # 这样“发给某某邮箱”会触发 send_invite_email handoff。
        graph.add_conditional_edges(
            "entry_node",
            route_entry,
            {"coordinator": "coordinator", "planner_node": "planner_node"},
        )

        # coordinator → 并发调研 or END
        graph.add_conditional_edges(
            "coordinator",
            should_research,
            [END, "weather_node", "poi_node", "route_node"],
        )

        # 三个并发节点 → planner（fan-in 汇合）
        graph.add_edge("weather_node", "planner_node")
        graph.add_edge("poi_node", "planner_node")
        graph.add_edge("route_node", "planner_node")

        # planner → email_node or END
        graph.add_conditional_edges(
            "planner_node",
            after_planner,
            {"email_node": "email_node", END: END},
        )

        graph.add_edge("email_node", END)

        # 编译（SQLite checkpointer 让每个 thread_id 有独立且可持久化的对话历史）
        self.graph = graph.compile(checkpointer=self.checkpointer)
        logger.info("TravelPipeline 初始化完成 ✓")

    async def init_checkpointer(self):
        db_path = os.path.join(os.path.dirname(__file__), "../../db/travel_agent.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = await aiosqlite.connect(db_path)
        self.checkpointer = AsyncSqliteSaver(conn=self.conn)
        await self.checkpointer.setup()

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def clear_messages(self, thread_id: str):
        if not self.checkpointer:
            return
        await self.checkpointer.adelete_thread(thread_id)

    async def get_messages(self, thread_id: str) -> dict:
        if not self.graph:
            return {"messages": []}
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.graph.aget_state(config)
        if state is None or not state.values:
            return {"messages": []}

        messages = state.values.get("messages", [])
        result = []
        for msg in messages:
            content = _message_content(msg)
            if not content:
                continue
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": content})
        return {"messages": result}

    async def generate_sse(
        self,
        thread_id: str,
        message: str,
        interrupt_decision: dict | None = None,
    ):
        """处理用户请求，返回 SSE 事件流。

        跟 EmailAgent.generate_sse 一个套路。
        """
        config = {"configurable": {"thread_id": thread_id}}

        # 邮件确认回调（用户在 interrupt 弹窗里点了确认/拒绝）
        if interrupt_decision:
            try:
                async for ns, stream_type, data in self.graph.astream(
                    Command(resume=interrupt_decision),
                    config=config,
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                ):
                    if stream_type == "messages":
                        token, metadata = data
                        content = _message_content(token)
                        if content:
                            yield _sse("message", content)
                    elif stream_type == "updates" and isinstance(data, dict):
                        pass  # 不需要额外处理
                yield _sse("done", "")
            except Exception as exc:
                logger.error(f"Email resume 失败: {exc}", exc_info=True)
                yield _sse("error", str(exc))
            return

        try:
            current_node = None
            # 记录已通过子图 token 流式输出的节点，用于去重
            # subgraphs=True 时，create_agent 子图节点会发两波 messages：
            #   1) 子图 token（ns=('coordinator:xxx',)）—— 逐 token 流式
            #   2) 根图完整消息（ns=()）—— 一次性重复发完整内容
            # 对于已经逐 token 流式过的节点，跳过根图的重复消息
            subgraph_streamed = set()

            # stream_mode=["messages","updates"] + subgraphs=True
            # 格式：(namespace, stream_type, data) 三元组
            #   stream_type="messages" → data=(token, metadata)  逐 token
            #   stream_type="updates"  → data={node_name: ...}   节点完成
            async for ns, stream_type, data in self.graph.astream(
                {
                    "messages": [HumanMessage(content=message)],
                    "email_thread_id": f"travel-email-{thread_id}",
                },
                config=config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
            ):
                # ---- 逐 token 流式输出 ----
                if stream_type == "messages":
                    token, metadata = data
                    content = _message_content(token)
                    if not content:
                        continue

                    source = _get_source_node(ns, metadata)
                    if source not in ("coordinator", "planner_node", "email_node"):
                        continue

                    # 去重：子图 token 来自 ns != ()，根图重复来自 ns == ()
                    if ns:
                        # 子图 token —— 正常输出，并记录该 source
                        subgraph_streamed.add(source)
                    else:
                        # 根图消息 —— 如果该 source 已经子图流式过，跳过
                        if source in subgraph_streamed:
                            continue

                    # 节点切换时断开气泡
                    if source != current_node:
                        if current_node is not None:
                            yield _sse("message_end", "")
                        current_node = source

                    yield _sse("message", content)

                # ---- 节点完成事件 ----
                elif stream_type == "updates" and isinstance(data, dict):
                    # 只关心根图节点（ns=()）
                    if ns != ():
                        continue
                    for node_name in data:
                        if node_name == "__start__":
                            continue

                        # 并发调研节点完成 → 实时发状态
                        if node_name == "weather_node":
                            yield _sse("status", "🌤 天气调研完成")
                        elif node_name == "poi_node":
                            yield _sse("status", "📍 景点调研完成")
                        elif node_name == "route_node":
                            yield _sse("status", "🚗 路线调研完成")

                    # LangGraph interrupt（email_node 的 interrupt() 调用）
                    if "__interrupt__" in data:
                        interrupt_list = data["__interrupt__"]
                        for item in interrupt_list:
                            value = item.value if hasattr(item, "value") else item
                            if isinstance(value, dict) and value.get("type") == "email_confirm":
                                yield _sse_payload("interrupt", {
                                    "type": "interrupt",
                                    "interrupt": value,
                                })

            yield _sse("done", "")

        except Exception as exc:
            logger.error(f"Travel SSE 流中断: {exc}", exc_info=True)
            yield _sse("error", str(exc))


# ---- SSE 辅助函数 ----

def _sse(event_type: str, content: str) -> dict:
    return {
        "event": event_type,
        "data": json.dumps(
            {"type": event_type, "content": content},
            ensure_ascii=False,
        ),
    }


def _sse_payload(event_type: str, payload: dict) -> dict:
    return {
        "event": event_type,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }


def _last_ai_message(updates: dict) -> str | None:
    for msg in reversed(updates.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return None


def _get_source_node(ns: tuple, metadata: dict) -> str:
    """判断 token 来自哪个父图节点。

    子图 token: ns=('coordinator:90ea...',) → 'coordinator'
    根图 token: ns=(), metadata={'langgraph_node': 'planner_node'} → 'planner_node'
    """
    if ns:
        # 子图 token：namespace 第一段 "node_name:uuid"
        first = ns[0]
        return first.split(":")[0] if ":" in first else first
    else:
        # 根图 token：从 metadata 拿节点名
        return metadata.get("langgraph_node", "")


def _message_content(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


# ---- 单例 ----

travel_pipeline = TravelPipeline()
