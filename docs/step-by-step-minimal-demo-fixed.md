# 市内出行企划多智能体 - 可手敲修正版

这份文档只做一件事：把 Claude 那份 `step-by-step-minimal-demo.md` 改成你可以照着一步一步手敲的版本。

原则：

- 不提前加复杂兜底。
- 不加课程外的奇怪辅助函数。
- 不改你的 Python 文件，只告诉你每一步应该写什么。
- 每个模块先用 `#` 注释写清楚“这个模块是干什么的”。
- 每一步只写当前文件需要的代码，不把整套工程一次性糊给你。

原文档主要问题：

- `state.py` 只讲了字段，没有给出完整类代码。
- `prompts.py` 只讲 prompt 内容，没有给出可直接写入文件的常量。
- `router_agent.py`、`orchestrator_agent.py`、`mcp_client.py` 等步骤有说明，但缺少完整可实现代码。
- 原文档写的是 `subagents.py`，但你项目里现在创建的是 `subagent.py`。如果你想沿用自己已有文件名，后面 import 必须用 `subagent`。
- `travel.py` 现在如果还是 skeleton，需要改成 SSE 接口，否则不会真正走 pipeline。

下面开始按步骤写。

---

## Step 1: `app/agents/travel/state.py`

这个文件只负责定义 Travel Agent 在多轮对话中要保存哪些状态。

```python
# 作用：定义出行企划 Agent 的多轮对话状态
# 这些字段会被 orchestrator_agent.py 里的工具不断更新

from typing import NotRequired

from langchain.agents import AgentState


class TravelState(AgentState):
    phase: NotRequired[str]
    destination: NotRequired[str]
    origin_location: NotRequired[str]
    travel_date: NotRequired[str]
    preferences: NotRequired[list[str]]
    weather_ok: NotRequired[bool]
    destination_result: NotRequired[str]
    weather_result: NotRequired[str]
    transport_result: NotRequired[str]
    chosen_plan: NotRequired[str]
    need_email: NotRequired[bool]
    invitee_name: NotRequired[str]
    invitee_email: NotRequired[str]
    travel_plan: NotRequired[str]
```

验证：

```bash
uv run python -c "from app.agents.travel.state import TravelState; print('Step 1 OK')"
```

---

## Step 2: `app/agents/travel/prompts.py`

这个文件只负责集中保存各个 Agent 的 prompt。

```python
# 作用：保存所有 travel 相关 Agent 的系统提示词
# router、orchestrator、subagent、planner 都会从这里导入 prompt

ROUTER_PROMPT = """
角色：意图路由器

可选意图：
- outing_plan：用户想规划一次出行
- weather_only：用户只想查天气
- chitchat：普通闲聊

规则：
- 只输出 JSON
- 不规划行程
- 不追问用户

输出格式：
{"intent": "outing_plan", "confidence": 0.9}
"""


ORCHESTRATOR_PROMPT = """
角色：出行企划协调者

你的工作流程：
1. 提取用户想去的地点和日期
2. 先调用 check_weather 查天气
3. 天气不好时告诉用户，建议换地方或换日期，等用户回复
4. 天气 OK 后，问用户从哪里出发
5. 信息齐全后调用 start_research 启动调研
6. 调研结果出来后，给用户 2-3 个方案选择
7. 用户选好后，问是否要发邮件约朋友
8. 如果要发邮件，收集朋友姓名和邮箱

你可以调用的工具：
- update_slots
- check_weather
- start_research

追问规则：
- 一次最多问 2 个问题
- 用自然中文
- 不要自己编造天气、景点、路线
"""


DESTINATION_PROMPT = """
角色：本地景点推荐 Agent

任务：
- 推荐目的地周边 3-5 个值得去的地方
- 可以包括景点、餐厅、咖啡馆
- 使用高德地图 MCP 工具搜索，不要编造

输出：
- JSON 列表
"""


WEATHER_PROMPT = """
角色：天气评估 Agent

任务：
- 通过高德地图 MCP 的 maps_weather 工具查天气
- 判断是否适合户外活动

输出 JSON：
- weather
- temperature_high
- temperature_low
- suitable_outdoor
- suggestion
"""


TRANSPORT_PROMPT = """
角色：市内交通规划 Agent

任务：
- 通过高德地图 MCP 的路线规划工具规划路线
- 路线规划前，先用 maps_geo 把地点转成经纬度

可用路线工具：
- maps_direction_walking
- maps_direction_driving
- maps_direction_transit_integrated
- maps_bicycling

输出 JSON 列表：
- from
- to
- method
- duration_minutes
"""


PLANNER_PROMPT = """
角色：行程规划 Agent

任务：
- 综合景点、天气、交通结果
- 生成 2-3 个不同风格的方案

每个方案包含：
- 方案名称
- 时间表
- 活动
- 地点
- 交通方式
- 总耗时

规则：
- 只用调研结果中的真实地点
- 不要编造
"""
```

验证：

```bash
uv run python -c "from app.agents.travel.prompts import ROUTER_PROMPT, ORCHESTRATOR_PROMPT, DESTINATION_PROMPT, WEATHER_PROMPT, TRANSPORT_PROMPT, PLANNER_PROMPT; print('Step 2 OK')"
```

---

## Step 3: `app/agents/travel/router_agent.py`

这个文件只负责把用户输入分类成 `outing_plan`、`weather_only` 或 `chitchat`。

先写导入：

```python
# 作用：识别用户意图，决定后续是否进入出行企划流程

import json

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.travel.prompts import ROUTER_PROMPT

load_dotenv()
```

再写主函数：

```python
async def classify_intent(user_message: str) -> dict:
    agent = create_agent(
        "deepseek-chat",
        tools=[],
        system_prompt=ROUTER_PROMPT,
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=user_message)]}
    )

    content = result["messages"][-1].content

    try:
        return json.loads(content)
    except Exception:
        return {"intent": "chitchat", "confidence": 0.5}
```

说明：

- 这里先按最小版本写。
- 如果后面模型返回 ```json 包裹导致解析失败，再单独修。
- 现在不要提前加辅助函数。

验证：

注意：`async def` 不能放在分号后面写成一行，否则 Python 会报 `SyntaxError`。
另外要先回到项目根目录运行。如果你现在在 `docs` 目录，先执行：

```bash
cd /Users/riddler/PycharmProjects/travel-agent
```

然后用下面这种多行写法：

```bash
uv run python - <<'PY'
import asyncio
from app.agents.travel.router_agent import classify_intent

async def main():
    print(await classify_intent("周末想去西溪湿地玩"))

asyncio.run(main())
PY
```

---

## Step 4: `app/agents/travel/mcp_client.py`

这个文件只负责连接高德 MCP，并把 MCP 工具提供给其他 Agent 使用。

先写导入和环境变量：

```python
# 作用：连接高德地图 MCP Server，获取天气、搜索、路线等工具

import os

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()
```

再写获取全部工具：

```python
async def get_amap_tools():
    api_key = os.getenv("AMAP_MAPS_API_KEY")

    if not api_key:
        raise ValueError("AMAP_MAPS_API_KEY 未设置，请先写入 .env")

    client = MultiServerMCPClient(
        {
            "amap-maps": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@amap/amap-maps-mcp-server"],
                "env": {
                    "AMAP_MAPS_API_KEY": api_key,
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "http_proxy": "",
                    "https_proxy": "",
                    "PATH": os.environ.get("PATH", ""),
                },
            }
        }
    )

    tools = await client.get_tools()
    return tools
```

再写按名字取工具：

```python
async def get_amap_tool_by_name(name: str):
    tools = await get_amap_tools()

    for tool in tools:
        if tool.name == name:
            return tool

    raise ValueError(f"找不到工具：{name}")
```

验证：

```bash
uv run python - <<'PY'
import asyncio
from app.agents.travel.mcp_client import get_amap_tools

async def main():
    tools = await get_amap_tools()
    print("工具数量：", len(tools))
    for tool in tools:
        print(tool.name)

asyncio.run(main())
PY
```

---

## Step 5: `app/agents/travel/orchestrator_agent.py`

这个文件负责多轮对话协调：收集目的地、日期、出发地，查天气，然后决定什么时候进入调研阶段。

先写导入：

```python
# 作用：协调多轮对话，更新 TravelState，并在信息齐全时启动调研

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.travel.mcp_client import get_amap_tool_by_name
from app.agents.travel.prompts import ORCHESTRATOR_PROMPT
from app.agents.travel.state import TravelState

load_dotenv()
```

写第一个工具 `update_slots`：

```python
@tool
def update_slots(
    runtime: ToolRuntime,
    destination: str | None = None,
    origin_location: str | None = None,
    travel_date: str | None = None,
    preferences: str | None = None,
    need_email: bool | None = None,
    invitee_name: str | None = None,
    invitee_email: str | None = None,
) -> Command:
    """把用户提供的信息写入 state。"""

    update = {}

    if destination:
        update["destination"] = destination
    if origin_location:
        update["origin_location"] = origin_location
    if travel_date:
        update["travel_date"] = travel_date
    if preferences:
        update["preferences"] = [p.strip() for p in preferences.split(",")]
    if need_email is not None:
        update["need_email"] = need_email
    if invitee_name:
        update["invitee_name"] = invitee_name
    if invitee_email:
        update["invitee_email"] = invitee_email

    update["messages"] = [
        ToolMessage(
            content=f"已更新信息：{list(update.keys())}",
            tool_call_id=runtime.tool_call_id,
        )
    ]

    return Command(update=update)
```

写第二个工具 `check_weather`：

```python
@tool
async def check_weather(
    runtime: ToolRuntime,
    city: str,
    date: str,
) -> Command:
    """调用高德 MCP 查询天气，并更新 state。"""

    weather_tool = await get_amap_tool_by_name("maps_weather")
    result = await weather_tool.ainvoke({"city": city})

    result_text = str(result)

    bad_weather_words = ["雨", "雪", "雷", "暴", "台风"]
    weather_ok = True

    for word in bad_weather_words:
        if word in result_text:
            weather_ok = False

    return Command(
        update={
            "phase": "weather_check",
            "weather_ok": weather_ok,
            "weather_result": result_text,
            "messages": [
                ToolMessage(
                    content=f"已查询天气，适合户外：{weather_ok}",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )
```

写第三个工具 `start_research`：

```python
@tool
def start_research(runtime: ToolRuntime) -> Command:
    """检查信息是否齐全，齐全则进入 researching 阶段。"""

    state = runtime.state
    missing = []

    if not state.get("destination"):
        missing.append("目的地")
    if not state.get("origin_location"):
        missing.append("出发地")
    if not state.get("travel_date"):
        missing.append("出行日期")

    if missing:
        return Command(
            update={
                "phase": "collecting",
                "messages": [
                    ToolMessage(
                        content=f"还缺少：{','.join(missing)}",
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )

    return Command(
        update={
            "phase": "researching",
            "messages": [
                ToolMessage(
                    content="信息已齐全，可以开始调研。",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )
```

最后写创建 Agent 的函数：

```python
def create_orchestrator(checkpointer=None):
    return create_agent(
        "deepseek-chat",
        tools=[update_slots, check_weather, start_research],
        state_schema=TravelState,
        checkpointer=checkpointer or InMemorySaver(),
        system_prompt=ORCHESTRATOR_PROMPT,
    )
```

验证：

```bash
uv run python -c "from app.agents.travel.orchestrator_agent import create_orchestrator; print('Step 5 OK')"
```

---

## Step 6: `app/agents/travel/subagent.py`

这个文件负责三个调研 Agent：景点、天气、交通。

注意：原 Claude 文档写的是 `subagents.py`，但你现在创建的是 `subagent.py`。后面 pipeline 里要按你的文件名导入。

先写导入：

```python
# 作用：定义三个调研子 Agent，分别查景点、天气、交通

import asyncio

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.travel.mcp_client import get_amap_tools
from app.agents.travel.prompts import (
    DESTINATION_PROMPT,
    WEATHER_PROMPT,
    TRANSPORT_PROMPT,
)

load_dotenv()
```

写工具筛选函数：

```python
async def filter_tools(names: list[str]):
    all_tools = await get_amap_tools()
    result = []

    for tool in all_tools:
        if tool.name in names:
            result.append(tool)

    return result
```

写景点 Agent：

```python
async def run_destination_agent(destination: str, preferences: str) -> str:
    tools = await filter_tools(
        ["maps_text_search", "maps_around_search", "maps_search_detail"]
    )

    agent = create_agent(
        "deepseek-chat",
        tools=tools,
        system_prompt=DESTINATION_PROMPT,
    )

    message = f"请搜索 {destination} 附近值得去的景点、餐厅、咖啡馆。用户偏好：{preferences or '无特别偏好'}"

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]}
    )

    return result["messages"][-1].content
```

写天气 Agent：

```python
async def run_weather_agent(destination: str, date: str) -> str:
    tools = await filter_tools(["maps_weather"])

    agent = create_agent(
        "deepseek-chat",
        tools=tools,
        system_prompt=WEATHER_PROMPT,
    )

    message = f"请查询 {destination} 在 {date} 的天气情况，判断是否适合户外活动"

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]}
    )

    return result["messages"][-1].content
```

写交通 Agent：

```python
async def run_transport_agent(origin: str, destination: str) -> str:
    tools = await filter_tools(
        [
            "maps_geo",
            "maps_direction_walking",
            "maps_direction_driving",
            "maps_direction_transit_integrated",
            "maps_bicycling",
        ]
    )

    agent = create_agent(
        "deepseek-chat",
        tools=tools,
        system_prompt=TRANSPORT_PROMPT,
    )

    message = f"请规划从 {origin} 到 {destination} 的交通路线，包括步行、公交、驾车等方式对比"

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]}
    )

    return result["messages"][-1].content
```

写并行调用函数：

```python
async def run_all_subagents(
    destination: str,
    date: str,
    preferences: str,
    origin_location: str,
) -> dict:
    destination_result, weather_result, transport_result = await asyncio.gather(
        run_destination_agent(destination, preferences),
        run_weather_agent(destination, date),
        run_transport_agent(origin_location, destination),
    )

    return {
        "destination": destination_result,
        "weather": weather_result,
        "transport": transport_result,
    }
```

验证：

```bash
uv run python -c "from app.agents.travel.subagent import run_all_subagents; print('Step 6 OK')"
```

---

## Step 7: `app/agents/travel/planner_agent.py`

这个文件负责把三个调研结果整理成 2-3 个行程方案。

先写导入：

```python
# 作用：根据景点、天气、交通调研结果生成最终行程方案

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.travel.prompts import PLANNER_PROMPT

load_dotenv()
```

写生成方案函数：

```python
async def generate_plan(
    destination: str,
    origin_location: str,
    date: str,
    preferences: str,
    destination_result: str,
    weather_result: str,
    transport_result: str,
) -> str:
    agent = create_agent(
        "deepseek-chat",
        tools=[],
        system_prompt=PLANNER_PROMPT,
    )

    message = f"""
请根据以下调研结果，生成 2-3 个不同风格的市内游方案供用户选择。

## 用户信息
- 目的地：{destination}
- 出发地点：{origin_location}
- 日期：{date}
- 偏好：{preferences}

## 景点调研
{destination_result}

## 天气调研
{weather_result}

## 交通调研
{transport_result}

请为每个方案给出：方案名称、行程时间表、总耗时。
用户需要从中选一个。
"""

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]}
    )

    return result["messages"][-1].content
```

验证：

```bash
uv run python -c "from app.agents.travel.planner_agent import generate_plan; print('Step 7 OK')"
```

---

## Step 8: `app/agents/travel/handoff.py`

这个文件负责把“发邮件”这件事交给已有的 EmailAgent。

先写导入：

```python
# 作用：把出行方案交给 EmailAgent，由 EmailAgent 生成邮件草稿并等待人工确认

from app.agents.email.email_agent import email_agent
```

写首次 handoff：

```python
async def handoff_to_email(
    thread_id: str,
    to_email: str,
    to_name: str,
    travel_plan_summary: str,
):
    message = f"请帮我给 {to_name}（{to_email}）发邮件，邀请 ta 一起出行。行程：{travel_plan_summary}"

    async for event in email_agent.generate_sse(
        thread_id=f"travel-email-{thread_id}",
        message=message,
        interrupt_decision=None,
    ):
        yield event
```

写人工确认后的恢复：

```python
async def handoff_to_email_resume(
    thread_id: str,
    interrupt_decision: dict,
):
    async for event in email_agent.generate_sse(
        thread_id=f"travel-email-{thread_id}",
        message="",
        interrupt_decision=interrupt_decision,
    ):
        yield event
```

验证：

```bash
uv run python -c "from app.agents.travel.handoff import handoff_to_email, handoff_to_email_resume; print('Step 8 OK')"
```

---

## Step 9: `app/agents/travel/pipeline.py`

这个文件负责把 Router、Orchestrator、Subagent、Planner、EmailAgent 串起来，并输出 SSE。

先写导入和全局 orchestrator：

```python
# 作用：完整编排 travel 多智能体流程，并以 SSE 事件返回给前端

import json

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.travel.router_agent import classify_intent
from app.agents.travel.orchestrator_agent import create_orchestrator
from app.agents.travel.subagent import run_all_subagents
from app.agents.travel.planner_agent import generate_plan
from app.agents.travel.handoff import handoff_to_email, handoff_to_email_resume

load_dotenv()

checkpointer = InMemorySaver()
orchestrator = create_orchestrator(checkpointer=checkpointer)
```

写 SSE 辅助函数：

```python
def sse(event_type: str, content: str):
    return {
        "event": event_type,
        "data": json.dumps(
            {"type": event_type, "content": content},
            ensure_ascii=False,
        ),
    }
```

写 pipeline 函数的开头：

```python
async def travel_pipeline_sse(
    thread_id: str,
    message: str,
    interrupt_decision: dict | None = None,
):
    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    if interrupt_decision:
        async for event in handoff_to_email_resume(thread_id, interrupt_decision):
            yield event
        return
```

写 Router 分支：

```python
    intent_result = await classify_intent(message)
    intent = intent_result.get("intent")

    if intent == "chitchat":
        yield sse("message", "你好！告诉我你想去哪玩吧。")
        yield sse("done", "处理完成")
        return

    if intent == "weather_only":
        yield sse("message", "你可以告诉我城市和日期，我会结合出行企划一起处理。")
        yield sse("done", "处理完成")
        return
```

写 Orchestrator 调用：

```python
    response = await orchestrator.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )

    state_snapshot = await orchestrator.aget_state(config)
    state = state_snapshot.values

    if state.get("phase") != "researching":
        yield sse("message", response["messages"][-1].content)
        yield sse("done", "处理完成")
        return
```

写调研和规划：

```python
    destination = state.get("destination")
    origin_location = state.get("origin_location")
    travel_date = state.get("travel_date")
    preferences = state.get("preferences", [])

    if isinstance(preferences, list):
        preferences_text = "、".join(preferences)
    else:
        preferences_text = str(preferences)

    yield sse("status", "正在调研景点、天气、交通...")

    results = await run_all_subagents(
        destination=destination,
        date=travel_date,
        preferences=preferences_text,
        origin_location=origin_location,
    )

    yield sse("status", "正在生成行程方案...")

    plan = await generate_plan(
        destination=destination,
        origin_location=origin_location,
        date=travel_date,
        preferences=preferences_text,
        destination_result=results["destination"],
        weather_result=results["weather"],
        transport_result=results["transport"],
    )

    yield sse("plan", plan)
```

写邮件 handoff 和结束：

```python
    if state.get("need_email") and state.get("invitee_email"):
        yield sse("status", "正在生成邮件草稿...")

        async for event in handoff_to_email(
            thread_id=thread_id,
            to_email=state["invitee_email"],
            to_name=state.get("invitee_name", "朋友"),
            travel_plan_summary=plan,
        ):
            yield event

    yield sse("done", "处理完成")
```

验证：

```bash
uv run python -c "from app.agents.travel.pipeline import travel_pipeline_sse; print('Step 9 OK')"
```

---

## Step 10: `app/api/v1/travel.py`

这个文件负责提供 FastAPI SSE 接口。

先写导入：

```python
# 作用：提供 /api/v1/travel/send 接口，把用户消息交给 travel_pipeline_sse

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.travel.pipeline import travel_pipeline_sse

router = APIRouter()
```

写请求模型：

```python
class TravelChatRequest(BaseModel):
    message: str = ""
    thread_id: str = "default"
    user_id: str | None = None
    interrupt_decision: Optional[Dict[str, Any]] = None
```

写接口：

```python
@router.post("/travel/send")
async def send_travel(request: TravelChatRequest):
    return EventSourceResponse(
        travel_pipeline_sse(
            thread_id=request.thread_id,
            message=request.message or "",
            interrupt_decision=request.interrupt_decision,
        )
    )
```

验证：

```bash
uv run python -c "from app.api.v1.travel import router; print('Step 10 OK')"
```

---

## Step 11: `tests/test_demo.py`

这个文件负责用终端模拟多轮对话，不通过 FastAPI。

```python
# 作用：在终端里测试 travel pipeline 的多轮对话

import asyncio

from app.agents.travel.pipeline import travel_pipeline_sse


async def main():
    thread_id = "demo-test-001"

    messages = [
        "周末想去西溪湿地玩",
        "我从城西银泰出发，想轻松一点",
    ]

    for message in messages:
        print("=" * 60)
        print("用户：", message)

        async for event in travel_pipeline_sse(thread_id, message):
            print(event["event"], event["data"][:200])


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
uv run python tests/test_demo.py
```

---

## 最终启动服务

```bash
uv run python -m app.main
```

另开终端测试：

```bash
curl -N -X POST http://127.0.0.1:8002/api/v1/travel/send \
  -H "Content-Type: application/json" \
  -d '{"message":"周末想去西溪湿地玩","thread_id":"curl-test-001"}'
```

---

## 你手敲时的顺序

建议严格按这个顺序：

1. `state.py`
2. `prompts.py`
3. `router_agent.py`
4. `mcp_client.py`
5. `orchestrator_agent.py`
6. `subagent.py`
7. `planner_agent.py`
8. `handoff.py`
9. `pipeline.py`
10. `travel.py`
11. `tests/test_demo.py`

每写完一个文件，就跑对应 Step 的验证命令。不要等全部写完再测。
