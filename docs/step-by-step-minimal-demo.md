# 市内出行企划 — Multi-Agent 并发架构搭建指南

> **场景**：用户说"周末想去西溪湿地玩"，Coordinator 收集信息后，Weather / POI / Route 三个 Subagent **并发执行**调研，Planner 综合生成方案，用户满意后可 Handoff 给 EmailAgent 发邮件约朋友。

---

## 架构

```
用户输入
  ↓
┌─────────────────── StateGraph(TravelState) ───────────────────┐
│                                                                │
│  [coordinator]  ← create_agent + save_trip_info 工具           │
│   │  多轮对话收集 destination / origin / date                   │
│   │  信息收齐 → 调 save_trip_info 写入 state                    │
│   │                                                            │
│   ▼  conditional_edge: 信息齐了且没调研过？                      │
│   │                                                            │
│   ├─── YES ─→ Send() 并发 fan-out ──────────────────┐         │
│   │          ┌──────────┐ ┌────────┐ ┌───────────┐  │         │
│   │          │ Weather  │ │  POI   │ │   Route   │  │         │
│   │          │  Agent   │ │ Agent  │ │   Agent   │  │         │
│   │          └────┬─────┘ └───┬────┘ └─────┬─────┘  │         │
│   │               └───────────┼────────────┘        │         │
│   │                           ▼  fan-in             │         │
│   │                    [planner_agent]               │         │
│   │                     │  综合三方结果生成方案        │         │
│   │                     │  问 solo / with friends     │         │
│   │                     ↓                            │         │
│   │              need_email? ──YES──→ [email_node]   │         │
│   │                    │              └ EmailAgent    │         │
│   │                    NO               + HITL       │         │
│   │                    ↓                             │         │
│   └─── NO ──→ END (等用户下一轮输入)                   │         │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**并发点**：Weather / POI / Route 三个 Agent 用 `Send()` 同时启动，不是串行等。

**每个 Agent 的工具分配**：

| Agent | 高德 MCP 工具 | 职责 |
|-------|-------------|------|
| Coordinator | `maps_geo`, `maps_ip_location` | 多轮对话收集信息 |
| Weather Agent | `maps_weather` | 查天气 |
| POI Agent | `maps_text_search`, `maps_around_search`, `maps_search_detail` | 搜景点/餐厅 |
| Route Agent | `maps_direction_*`, `maps_distance` | 规划交通路线 |
| Planner Agent | 无 MCP 工具，有 `send_invite_email` | 综合生成方案 |
| EmailAgent | Gmail 工具（已有） | 发邮件 + HITL |

---

## 项目结构

```
app/
├── main.py                              ← lifespan 初始化 travel + email
├── api/v1/
│   ├── chat.py                          ← EmailAgent 路由（已有，不动）
│   └── travel.py                        ← Step 7：Travel 路由
├── models/
│   └── schemas.py                       ← 已有，不动
├── common/
│   └── logger.py                        ← 已有，不动
├── integrations/
│   └── gmail_*.py                       ← 已有，不动
└── agents/
    ├── email_agent.py                   ← 已有，不动
    └── travel/
        ├── state.py                     ← Step 1：TravelState
        ├── prompts.py                   ← Step 2：5 个 prompt
        ├── mcp_client.py               ← Step 3：MCP 工具获取 + 分组
        ├── subagents/                   ← Step 4：4 个 subagent
        │   ├── __init__.py
        │   ├── weather_agent.py
        │   ├── poi_agent.py
        │   ├── route_agent.py
        │   └── planner_agent.py
        └── pipeline.py                  ← Step 5-6：TravelPipeline 类

tests/
└── test_demo.py                         ← Step 8：端到端测试
```

**跟 EmailAgent 一个套路**：
- `EmailAgent` 是一个类，`init()` 初始化，`generate_sse()` 处理请求
- `TravelPipeline` 也是一个类，`init()` 构建图，`generate_sse()` 处理请求
- `main.py` 的 `lifespan` 里统一初始化

---

## 外部依赖

| 依赖 | 状态 |
|------|------|
| DeepSeek API Key | ✅ `.env` 里有 |
| 高德 Web 服务 API Key | ✅ `.env` 里有 |
| Node.js + npx | ✅ |
| Gmail OAuth | ✅ `credentials.json` + `token.json` |

---

## Step 1：`app/agents/travel/state.py`

整个流程的共享状态。所有 Agent 通过 state 共享数据。

### 完整代码

```python
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
```

### 验证

```bash
uv run python -c "
from app.agents.travel.state import TravelState
print('TravelState fields:')
for k, v in TravelState.__annotations__.items():
    print(f'  {k}: {v}')
"
```

---

## Step 2：`app/agents/travel/prompts.py`

5 个 Agent，5 个 prompt。

### 完整代码

```python
COORDINATOR_PROMPT = """你是 CityBuddy 出行企划的协调者。用自然中文和用户聊天。

## 你的任务
收集出行三要素：
1. **目的地**（必须）
2. **出发地**（必须主动问）
3. **日期**（用户没说就默认本周末）

## 规则
- 一次最多问 1 个问题
- 三个信息都收集到了，立即调用 save_trip_info 工具保存
- 调完工具后回复："正在为您调研天气、景点和路线，请稍候 ⏳"
- 不要自己查天气、搜景点、规划路线，那是其他 Agent 的事
- 用自然中文，不要输出 JSON
"""

WEATHER_PROMPT = """你是天气调研 Agent。

## 任务
用 maps_weather 工具查询目的地所在城市的天气，判断是否适合户外出行。

## 输出格式
用自然语言总结：
- 天气状况和温度
- 是否适合户外
- 如果不适合，给出建议（换日期 / 室内备选）

## 规则
- 必须调用 maps_weather 工具，不要编造天气
- 如果目的地是景点名（如"西溪湿地"），自己推断城市名（→"杭州"）再查
"""

POI_PROMPT = """你是景点调研 Agent。

## 任务
用高德地图工具搜索目的地及周边值得去的地方。

## 工具使用
- maps_text_search：按关键词搜（如"西溪湿地 景点"）
- maps_around_search：按经纬度搜周边（餐厅、咖啡馆等）
- maps_search_detail：查某个地点的详细信息

## 输出格式
列出 3-5 个推荐地点，每个包含：
- 名称、类型（景点/餐厅/咖啡馆）、简短推荐理由

## 规则
- 必须调用工具搜索，不要编造地点
"""

ROUTE_PROMPT = """你是交通路线调研 Agent。

## 任务
规划从出发地到目的地的交通路线，提供多种方式对比。

## 工具使用
1. 先用 maps_geo 把出发地和目的地转成经纬度
2. 再分别调用：
   - maps_direction_driving（驾车）
   - maps_direction_transit_integrated（公交地铁）
   - maps_direction_walking 或 maps_bicycling（距离近的话）

## 输出格式
每种方式列出：交通方式、预计耗时、路线摘要

## 规则
- maps_direction_* 需要经纬度不是地名，必须先 maps_geo 转换
- 不要编造路线数据
"""

PLANNER_PROMPT = """你是行程规划 Agent。用自然中文回复。

## 任务
综合天气、景点、交通的调研结果，生成 2-3 个不同风格的出行方案。

## 方案风格示例
- 🌿 悠闲半日游（轻松为主）
- 🏃 深度一日游（玩得全面）
- 🍜 美食探店路线（吃为主）

## 每个方案包含
1. 方案名称 + 风格标签
2. 时间表：时间 → 活动 → 地点 → 交通方式
3. 预计总耗时和费用估算

## 最后
- 问用户"你觉得哪个方案好？"
- 再问"一个人去还是约朋友一起？"
- 如果用户要约朋友发邮件，问名字和邮箱，然后调 send_invite_email

## 规则
- 只用调研结果中的真实数据，不要编造
- send_invite_email 只在用户明确要求时才调用
"""
```

### 验证

```bash
uv run python -c "
from app.agents.travel.prompts import (
    COORDINATOR_PROMPT, WEATHER_PROMPT, POI_PROMPT,
    ROUTE_PROMPT, PLANNER_PROMPT
)
for name in ['COORDINATOR', 'WEATHER', 'POI', 'ROUTE', 'PLANNER']:
    p = eval(f'{name}_PROMPT')
    print(f'{name}_PROMPT: {len(p)} chars ✓')
"
```

---

## Step 3：`app/agents/travel/mcp_client.py`

获取高德 MCP 工具，然后按 Agent 职责分组。

### 完整代码

```python
import os

from dotenv import load_dotenv

load_dotenv()

from langchain_mcp_adapters.client import MultiServerMCPClient


async def get_amap_tools() -> list:
    """启动本地 npx 高德 MCP，获取全部工具。"""
    api_key = os.getenv("AMAP_MAPS_API_KEY")
    if not api_key:
        raise ValueError("AMAP_MAPS_API_KEY 未设置！请检查 .env")

    client = MultiServerMCPClient({
        "amap-maps": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {
                "AMAP_MAPS_API_KEY": api_key,
                # 清掉代理，高德是国内接口，走代理会 TLS 失败
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
                "PATH": os.environ.get("PATH", ""),
            },
        }
    })
    return await client.get_tools()


def split_tools(all_tools: list) -> dict:
    """把 MCP 工具按 Agent 职责分组。

    返回 dict，key 是 agent 名，value 是工具列表：
        {
            "coordinator": [maps_geo, maps_ip_location, ...],
            "weather":     [maps_weather],
            "poi":         [maps_text_search, maps_around_search, ...],
            "route":       [maps_direction_driving, ...],
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

    # 建索引：工具名 → 工具对象
    tool_index = {t.name: t for t in all_tools}

    result = {}
    for group_name, tool_names in groups.items():
        result[group_name] = [
            tool_index[name] for name in tool_names if name in tool_index
        ]

    return result
```

### 验证

```bash
uv run python -c "
import asyncio
from app.agents.travel.mcp_client import get_amap_tools, split_tools

async def main():
    tools = await get_amap_tools()
    print(f'共 {len(tools)} 个工具')

    groups = split_tools(tools)
    for name, group_tools in groups.items():
        names = [t.name for t in group_tools]
        print(f'  {name}: {names}')

asyncio.run(main())
"
```

看到 4 组工具分配正确就通过。

---

## Step 4：`app/agents/travel/subagents/` — 4 个 Subagent

每个文件导出两样东西：
1. `create_xxx_agent(tools)` — 创建 agent
2. `xxx_node(state)` — 包装成 StateGraph 的节点函数

这是 LangGraph 的标准模式：agent 负责"思考和调工具"，node 函数负责"从 state 读输入、把结果写回 state"。

### 4a. `weather_agent.py`

```python
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.travel.prompts import WEATHER_PROMPT
from app.agents.travel.state import TravelState


_agent = None


def create_weather_agent(tools: list):
    """用 MCP 的 maps_weather 工具创建 Weather Agent。"""
    global _agent
    _agent = create_agent(
        "deepseek-chat",
        tools=tools,
        name="weather_agent",
        state_schema=TravelState,
        system_prompt=WEATHER_PROMPT,
    )


async def weather_node(state: TravelState) -> dict:
    """StateGraph 节点：调用 Weather Agent，结果写入 state.weather_result。"""
    dest = state.get("destination", "")
    date = state.get("date_text", "本周末")

    result = await _agent.ainvoke({
        "messages": [HumanMessage(content=f"查询 {dest} {date} 的天气")],
    })

    last_ai = _extract_last_ai(result)
    return {"weather_result": last_ai}


def _extract_last_ai(result: dict) -> str:
    """从 agent 结果中提取最后一条 AI 消息。"""
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
```

### 4b. `poi_agent.py`

```python
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

    result = await _agent.ainvoke({
        "messages": [HumanMessage(content=f"搜索 {dest} 及周边值得去的景点、餐厅")],
    })

    last_ai = _extract_last_ai(result)
    return {"poi_result": last_ai}


def _extract_last_ai(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
```

### 4c. `route_agent.py`

```python
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.travel.prompts import ROUTE_PROMPT
from app.agents.travel.state import TravelState


_agent = None


def create_route_agent(tools: list):
    """用 MCP 的路线规划工具创建 Route Agent。"""
    global _agent
    _agent = create_agent(
        "deepseek-chat",
        tools=tools,
        name="route_agent",
        state_schema=TravelState,
        system_prompt=ROUTE_PROMPT,
    )


async def route_node(state: TravelState) -> dict:
    """StateGraph 节点：调用 Route Agent，结果写入 state.route_result。"""
    origin = state.get("origin", "")
    dest = state.get("destination", "")

    result = await _agent.ainvoke({
        "messages": [HumanMessage(content=f"规划从 {origin} 到 {dest} 的交通路线")],
    })

    last_ai = _extract_last_ai(result)
    return {"route_result": last_ai}


def _extract_last_ai(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
```

### 4d. `planner_agent.py`

Planner 特殊一点：它有一个 `send_invite_email` 工具用于 Handoff 到 EmailAgent。

```python
from langchain.agents import create_agent
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.agents.travel.prompts import PLANNER_PROMPT
from app.agents.travel.state import TravelState


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
    weather = state.get("weather_result", "")
    poi = state.get("poi_result", "")
    route = state.get("route_result", "")

    # 第一次进 planner：把调研结果打包给它
    if not state.get("final_plan"):
        input_msg = (
            f"## 调研结果\n\n"
            f"### 天气\n{weather}\n\n"
            f"### 景点推荐\n{poi}\n\n"
            f"### 交通路线\n{route}\n\n"
            f"请根据以上信息生成 2-3 个出行方案。"
        )
    else:
        # 后续对话：拿用户最新消息
        user_msgs = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
        input_msg = user_msgs[-1].content if user_msgs else ""

    result = await _agent.ainvoke({
        "messages": [HumanMessage(content=input_msg)],
    })

    last_ai = _extract_last_ai(result)
    return {
        "final_plan": last_ai,
        "messages": [AIMessage(content=last_ai)],
    }


def _extract_last_ai(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content
    return "（无结果）"
```

### `__init__.py`

```python
from app.agents.travel.subagents.weather_agent import create_weather_agent, weather_node
from app.agents.travel.subagents.poi_agent import create_poi_agent, poi_node
from app.agents.travel.subagents.route_agent import create_route_agent, route_node
from app.agents.travel.subagents.planner_agent import create_planner_agent, planner_node

__all__ = [
    "create_weather_agent", "weather_node",
    "create_poi_agent", "poi_node",
    "create_route_agent", "route_node",
    "create_planner_agent", "planner_node",
]
```

### 验证

```bash
uv run python -c "
from app.agents.travel.subagents import (
    create_weather_agent, weather_node,
    create_poi_agent, poi_node,
    create_route_agent, route_node,
    create_planner_agent, planner_node,
)
print('4 个 subagent 导入成功 ✓')
"
```

---

## Step 5：`app/agents/travel/pipeline.py` — TravelPipeline 类

跟 `EmailAgent` 一个套路：一个类，`init()` 构建图，`generate_sse()` 处理请求。

### 回顾课程知识
- **Runtime**（第1节）：`ToolRuntime` + `Command(update={...})` — `save_trip_info` 用
- **HITL**（第3节）：EmailAgent 的 `HumanInTheLoopMiddleware` — email_node 里复用
- **Multi-Agent**（第5节）：`Send()` fan-out — 并发调研；`Command(goto=..., graph=PARENT)` — handoff 到 EmailAgent

### 完整代码

```python
import json

from langchain.agents import create_agent
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, Send

from app.agents.email.email_agent import email_agent
from app.agents.travel.mcp_client import get_amap_tools, split_tools
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
# Email 节点
# ================================================================

async def email_node(state: TravelState) -> dict:
    """EmailAgent 节点：复用已有的 EmailAgent 发邮件 + HITL。"""
    to_name = state.get("invitee_name", "朋友")
    to_email = state.get("invitee_email", "")
    plan = state.get("final_plan", "")

    message = (
        f"请帮我给 {to_name}（{to_email}）发一封邮件，"
        f"邀请 ta 一起出行。\n\n行程安排：\n{plan}"
    )

    reply_parts = []
    async for event in email_agent.generate_sse(
            thread_id=f"travel-email-{id(state)}",
            message=message,
            interrupt_decision=None,
    ):
        evt = event.get("event", "")
        if evt == "message":
            data = json.loads(event["data"])
            reply_parts.append(data.get("content", ""))

    content = "".join(reply_parts) or "邮件草稿已生成，等待确认发送。"
    return {"messages": [AIMessage(content=content)]}


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

    async def init(self):
        """启动时调用：获取 MCP 工具 → 创建 Subagent → 构建 StateGraph。"""
        logger.info("TravelPipeline 开始初始化...")

        # 1. 获取高德 MCP 工具并分组
        all_tools = await get_amap_tools()
        tools = split_tools(all_tools)
        logger.info(f"MCP 工具获取完成，共 {len(all_tools)} 个")

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
        graph.add_node("coordinator", coordinator)
        graph.add_node("weather_node", weather_node)
        graph.add_node("poi_node", poi_node)
        graph.add_node("route_node", route_node)
        graph.add_node("planner_node", planner_node)
        graph.add_node("email_node", email_node)

        # 添加边
        graph.set_entry_point("coordinator")

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

        # 编译（InMemorySaver 让每个 thread_id 有独立对话历史）
        self.graph = graph.compile(checkpointer=InMemorySaver())
        logger.info("TravelPipeline 初始化完成 ✓")

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

        # 邮件确认回调（用户在 HITL interrupt 里点了同意/拒绝）
        if interrupt_decision:
            async for event in email_agent.generate_sse(
                    thread_id=f"travel-email-{thread_id}",
                    message="",
                    interrupt_decision=interrupt_decision,
            ):
                yield event
            return

        try:
            async for chunk in self.graph.astream(
                    {"messages": [HumanMessage(content=message)]},
                    config=config,
                    stream_mode="updates",
            ):
                for node_name, updates in chunk.items():
                    if node_name == "__start__":
                        continue

                    # coordinator 的回复
                    if node_name == "coordinator":
                        for msg in updates.get("messages", []):
                            if isinstance(msg, AIMessage) and msg.content:
                                yield _sse("message", msg.content)

                    # 并发调研完成状态
                    if node_name in ("weather_node", "poi_node", "route_node"):
                        label = {
                            "weather_node": "🌤 天气",
                            "poi_node": "📍 景点",
                            "route_node": "🚗 路线",
                        }
                        yield _sse("status", f"{label[node_name]}调研完成")

                    # planner 的方案
                    if node_name == "planner_node":
                        for msg in updates.get("messages", []):
                            if isinstance(msg, AIMessage) and msg.content:
                                yield _sse("message", msg.content)

                    # email 节点
                    if node_name == "email_node":
                        yield _sse("status", "📧 正在生成邮件草稿...")
                        for msg in updates.get("messages", []):
                            if isinstance(msg, AIMessage) and msg.content:
                                yield _sse("message", msg.content)

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


# ---- 单例 ----

travel_pipeline = TravelPipeline()
```

### 关键点

1. **`TravelPipeline` 类** — 跟 `EmailAgent` 一个套路。`init()` 在 `main.py` 的 lifespan 里调用，`generate_sse()` 在每次请求时调用。

2. **`Send()` 并发 fan-out** — `should_research()` 返回 3 个 `Send`，LangGraph 同时跑 Weather/POI/Route，全部完成后才进 Planner。

3. **`state_schema=TravelState`** — 每个 `create_agent` 都传这个，子图才能读写共享 state 字段。

4. **`Command(goto="email_node", graph=Command.PARENT)`** — Planner 的 `send_invite_email` 工具返回这个，控制权自动 Handoff 到 EmailAgent。

5. **`InMemorySaver`** — 每个 `thread_id` 独立对话历史。前端新建会话 = 新 thread_id。

### 验证

暂时不验证（要等 Step 6 改完 main.py 才能完整跑），Step 8 统一测。

---

## Step 6：`app/main.py` — 添加 TravelPipeline 初始化

在 lifespan 里加一行 `await travel_pipeline.init()`。

### 要改的部分

```python
# 新增 import
from app.agents.travel.pipeline import travel_pipeline

# lifespan 里加初始化
@asynccontextmanager
async def lifespan(app: FastAPI):
    email_agent_started = False
    if os.path.exists("credentials.json") or os.path.exists("token.json"):
        await email_agent.init()
        email_agent_started = True
    else:
        print("EmailAgent skipped: credentials.json/token.json not found.")

    # 新增：初始化 Travel Pipeline
    await travel_pipeline.init()

    yield

    if email_agent_started:
        await email_agent.close()
```

就加两行：一行 import，一行 `await travel_pipeline.init()`。

---

## Step 7：`app/api/v1/travel.py`

### 完整代码

```python
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.travel.pipeline import travel_pipeline

router = APIRouter()


class TravelChatRequest(BaseModel):
    message: str = ""
    thread_id: str = "default"
    user_id: str | None = None
    interrupt_decision: Optional[Dict[str, Any]] = None


@router.post("/travel/send", tags=["出行企划 Agent"])
async def send_travel(request: TravelChatRequest):
    return EventSourceResponse(
        travel_pipeline.generate_sse(
            thread_id=request.thread_id,
            message=request.message or "",
            interrupt_decision=request.interrupt_decision,
        )
    )
```

---

## Step 8：`tests/test_demo.py`

### 完整代码

```python
"""端到端测试：模拟多轮对话。"""
import asyncio

from app.agents.travel.pipeline import travel_pipeline


async def main():
    # 先初始化（正常运行时 main.py 的 lifespan 会做这一步）
    await travel_pipeline.init()

    tid = "e2e-test-001"

    conversations = [
        "周末想去西溪湿地玩",
        "我从城西银泰出发",
        # 等上一轮出方案后可以继续：
        # "选方案A，约朋友一起",
        # "发给小王 xw@gmail.com",
    ]

    for i, msg in enumerate(conversations, 1):
        print(f"\n{'='*60}")
        print(f"第{i}轮 | 用户：{msg}")
        print("=" * 60)

        async for event in travel_pipeline.generate_sse(tid, msg):
            etype = event.get("event", "?")
            data = event.get("data", "")
            print(f"  [{etype}] {data[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 完整数据流

```
前端 POST /api/v1/travel/send  {message, thread_id, interrupt_decision?}
    │
    ▼
travel.py → EventSourceResponse(travel_pipeline.generate_sse)
    │
    ├── interrupt_decision? → EmailAgent.generate_sse(resume)
    │
    └── 正常对话 → graph.astream(message)
        │
        ▼
    ┌──── StateGraph(TravelState) + InMemorySaver ──────────────┐
    │                                                            │
    │  [coordinator]  ← create_agent                            │
    │   │  工具：maps_geo + save_trip_info                       │
    │   │  多轮对话收集 destination / origin / date               │
    │   │                                                        │
    │   ▼  conditional_edge: should_research()                   │
    │   │                                                        │
    │   ├── 信息没收齐 → END (等下一轮)                           │
    │   │                                                        │
    │   └── 信息齐了 → Send() 并发 fan-out                       │
    │       ┌──────────────┬──────────────┬──────────────┐      │
    │       │ weather_node │  poi_node    │  route_node  │      │
    │       │ maps_weather │ maps_search  │ maps_dir_*   │      │
    │       └──────┬───────┴──────┬───────┴──────┬───────┘      │
    │              └──────────────┼──────────────┘              │
    │                             ▼  fan-in                     │
    │                      [planner_node]                        │
    │                       │  综合结果 → 生成方案               │
    │                       ▼  conditional_edge: after_planner() │
    │                       ├── need_email → [email_node]       │
    │                       │                └ EmailAgent + HITL │
    │                       └── 不需要 → END                     │
    │                                                            │
    └────────────────────────────────────────────────────────────┘
```

---

## 常见问题

### MCP 工具调用 TLS 失败
你有代理，高德是国内接口。`mcp_client.py` 已经清掉了代理环境变量。

### `NotImplementedError: cannot be used as a context manager`
`langchain-mcp-adapters` 0.2.x 不支持 `async with`。用 `client = MultiServerMCPClient({...})` + `await client.get_tools()`。

### 高德路线规划需要经纬度
`maps_direction_*` 需要经纬度不是地名。`ROUTE_PROMPT` 已告诉 AI 先用 `maps_geo` 转换。

### `Send()` 是怎么并发的？
`should_research()` 返回 `[Send("weather_node", state), Send("poi_node", state), Send("route_node", state)]`。LangGraph 看到 `Send` 列表会同时启动所有目标节点，全部完成后 fan-in 到 Planner。

### `state_schema=TravelState` 为什么必须传？
`create_agent` 默认用 `AgentState`（只有 `messages`）。不传的话子图不认识 `destination`、`weather_result` 这些字段，`Command(update={...})` 写不进去。

### 为什么用类而不是全局函数？
跟你的 `EmailAgent` 保持一致。类的好处：`init()` 在 lifespan 统一初始化，`generate_sse()` 处理请求，状态（`self.graph`）封装在实例里，不需要 `global` 变量。

### InMemorySaver 重启后丢数据
只存内存，重启就没了。要持久化换 `AsyncSqliteSaver`（你 EmailAgent 里用的那个）。

---

## 总结：要写/改的文件清单

| 步骤 | 文件 | 操作 | 说明 |
|------|------|------|------|
| Step 1 | `state.py` | 重写 | TravelState |
| Step 2 | `prompts.py` | 重写 | 5 个 prompt |
| Step 3 | `mcp_client.py` | 重写 | MCP 工具 + split_tools |
| Step 4 | `subagents/*.py` | 新建 | 4 个 subagent |
| Step 5 | `pipeline.py` | 重写 | TravelPipeline 类 |
| Step 6 | `main.py` | 加 2 行 | import + init |
| Step 7 | `travel.py` | 重写 | FastAPI endpoint |
| Step 8 | `test_demo.py` | 新建 | 端到端测试 |

**可以删除的旧文件**：`router_agent.py`、`orchestrator_agent.py`、`subagents.py`（注意是根目录的文件，不是 subagents 文件夹）、`planner_agent.py`、`handoff.py`。

**从 Step 1 开始写，写完一个告诉我 review。**
