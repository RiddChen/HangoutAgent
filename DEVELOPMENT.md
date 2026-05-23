# HangoutAgent 开发文档：从零到完整项目

本文档记录 HangoutAgent 的完整开发过程。按照这个顺序，你可以从一个空目录搭建出完整的多智能体出行规划系统。

---

## 目录

- [Phase 1：项目初始化](#phase-1项目初始化)
- [Phase 2：基础设施层](#phase-2基础设施层)
- [Phase 3：MCP 外部工具接入](#phase-3mcp-外部工具接入)
- [Phase 4：State 与 Command 工具](#phase-4state-与-command-工具)
- [Phase 5：子 Agent 定义](#phase-5子-agent-定义)
- [Phase 6：Supervisor 编排](#phase-6supervisor-编排)
- [Phase 7：API 路由与流式输出](#phase-7api-路由与流式输出)
- [Phase 8：前端 SPA](#phase-8前端-spa)
- [Phase 9：Gmail 邮件集成](#phase-9gmail-邮件集成)
- [Phase 10：调试与优化](#phase-10调试与优化)
- [附录：核心概念速查](#附录核心概念速查)

---

## Phase 1：项目初始化

### 1.1 创建项目目录

```bash
mkdir HangoutAgent && cd HangoutAgent
uv init
```

### 1.2 编写 `pyproject.toml`

定义项目元数据和所有依赖。每个依赖的用途：

```toml
[project]
name = "hangout-agent"
version = "0.1.0"
description = "Multi-Agent 智能出行规划系统"
requires-python = ">=3.13"

dependencies = [
    # --- LLM ---
    "langchain>=1.3.1",              # Agent 框架核心（create_agent, middleware, AgentState）
    "langchain-deepseek>=1.0.1",     # DeepSeek LLM 接入

    # --- MCP 工具 ---
    "langchain-mcp-adapters>=0.2.2", # MCP 协议适配器（连接高德/天气/12306等外部工具）

    # --- 状态持久化 ---
    "langgraph>=1.2.0",              # 图执行引擎（Command, interrupt, Store）
    "langgraph-checkpoint-redis>=0.4.1",   # Redis 持久化后端
    "langgraph-checkpoint-sqlite>=3.1.0",  # SQLite 持久化后端（Redis 不可用时回退）
    "aiosqlite>=0.22.1",             # SQLite 异步驱动

    # --- Web 后端 ---
    "fastapi>=0.136.1",              # REST API 框架
    "uvicorn>=0.47.0",               # ASGI 服务器
    "sse-starlette>=3.4.4",          # SSE 流式输出支持

    # --- 邮件 ---
    "langchain-google-community[gmail]>=4.0.0",  # Gmail API 集成

    # --- 工具 ---
    "python-dotenv>=1.2.2",          # .env 环境变量加载
    "pydantic>=2.13.4",              # 数据模型验证
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]      # 告诉 hatchling 打包 app/ 目录（不是默认的 src/）
```

### 1.3 创建目录结构

```bash
mkdir -p app/{agents/hangout,api/v1,common,integrations,models,static,db}
touch app/__init__.py
touch app/agents/__init__.py
touch app/agents/hangout/__init__.py
touch app/api/__init__.py
touch app/api/v1/__init__.py
touch app/common/__init__.py
touch app/integrations/__init__.py
touch app/models/__init__.py
```

### 1.4 创建 `.env`

```bash
# LLM
DEEPSEEK_API_KEY=your_key

# 高德地图 MCP
AMAP_MAPS_API_KEY=your_key

# 墨迹天气 MCP
WEATHER_MCP_TRANSPORT=http
WEATHER_MCP_API_KEY=your_bigmodel_key
WEATHER_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/moji-weather/mcp?Authorization=your_key

# 12306 火车票 MCP
TRAIN_MCP_TRANSPORT=stdio
TRAIN_MCP_COMMAND=npx
TRAIN_MCP_ARGS=-y 12306-mcp

# 航班 MCP
FLIGHT_MCP_TRANSPORT=http
FLIGHT_MCP_API_KEY=your_bigmodel_key
FLIGHT_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/aviation/mcp?Authorization=your_key

# LangSmith（可选）
LANGSMITH_API_KEY=your_key
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=hangout-agent
```

### 1.5 安装依赖

```bash
uv sync
```

此时目录结构：

```
HangoutAgent/
├── app/
│   ├── __init__.py
│   ├── agents/
│   │   ├── __init__.py
│   │   └── hangout/
│   │       └── __init__.py
│   ├── api/v1/
│   ├── common/
│   ├── integrations/
│   ├── models/
│   ├── static/
│   └── db/
├── pyproject.toml
├── uv.lock
└── .env
```

---

## Phase 2：基础设施层

这一阶段搭建日志、SSE 序列化、会话管理——项目的"地基"。

### 2.1 `app/common/logger.py` — 日志

**作用**：全局日志配置，所有模块通过 `from app.common.logger import logger` 使用。

```python
import logging
import sys

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

logger = logging.getLogger("hangout_agent")
```

### 2.2 `app/common/sse.py` — SSE 事件序列化

**作用**：将后端数据封装成 SSE（Server-Sent Events）格式，前端通过 `EventSource` 实时接收。

SSE 是一种服务器向浏览器推送事件的协议。每个事件有 `event`（类型）和 `data`（JSON 数据）两个字段。

本项目定义了 4 种事件类型：
- `message_delta`：文字流式输出（逐字推送）
- `status`：状态提示（"天气专家正在调研..."）
- `interrupt`：人机确认弹窗（天气确认/邮件确认）
- `done` / `error`：结束/错误

```python
import json

def sse_event(event_type: str, content: str) -> dict:
    """普通文本事件。"""
    return {
        "event": event_type,
        "data": json.dumps({"type": event_type, "content": content}, ensure_ascii=False),
    }

def sse_json_event(event_type: str, payload: dict) -> dict:
    """JSON 事件（用于 interrupt 等复杂数据）。"""
    return {
        "event": event_type,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }

def serialize(obj):
    """将 LangGraph 对象（Interrupt 等）转为可 JSON 序列化的格式。"""
    if hasattr(obj, "value"):
        return serialize(obj.value)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {key: serialize(value) for key, value in obj.items()}
    return obj
```

### 2.3 `app/models/session.py` — 会话持久化管理

**作用**：管理两个持久化后端——

- **checkpointer**：保存对话历史（每个 `thread_id` 一个对话线程）。用户关闭浏览器再打开，对话还在。
- **store**：保存方案全文等大块数据（按 `user_id` + `thread_id` 隔离）。

优先用 Redis（生产级），连不上自动降级到 SQLite + 内存。

```python
import os
from langchain_core.messages import AIMessage, HumanMessage
from app.common.logger import logger

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

class SessionManager:
    def __init__(self):
        self.checkpointer = None
        self.store = None
        self._conn = None

    async def init(self):
        try:
            await self._init_redis()
        except Exception as e:
            logger.warning(f"Redis 连接失败（{e}），回退到 SQLite + InMemoryStore")
            await self._init_sqlite_fallback()

    async def _init_redis(self):
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        from langgraph.store.redis import AsyncRedisStore
        self.checkpointer = AsyncRedisSaver(redis_url=REDIS_URL)
        await self.checkpointer.asetup()
        self.store = AsyncRedisStore(redis_url=REDIS_URL)
        await self.store.setup()

    async def _init_sqlite_fallback(self):
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.store.memory import InMemoryStore
        db_path = os.path.join(os.path.dirname(__file__), "../db/hangout.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = await aiosqlite.connect(db_path)
        self.checkpointer = AsyncSqliteSaver(conn=self._conn)
        await self.checkpointer.setup()
        self.store = InMemoryStore()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def get_messages(self, graph, thread_id: str) -> dict:
        """从 checkpointer 读取对话历史，返回给前端展示。"""
        config = {"configurable": {"thread_id": thread_id}}
        state = await graph.aget_state(config)
        if not state or not state.values:
            return {"messages": []}
        result = []
        for msg in state.values.get("messages", []):
            content = msg.content if hasattr(msg, "content") else ""
            if not content:
                continue
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": content})
            elif isinstance(msg, AIMessage):
                result.append({"role": "assistant", "content": content})
        return {"messages": result}

    async def clear_messages(self, thread_id: str):
        if self.checkpointer and hasattr(self.checkpointer, "adelete_thread"):
            await self.checkpointer.adelete_thread(thread_id)

session_manager = SessionManager()   # 单例
```

**为什么需要 checkpointer 和 store 两个？**

- checkpointer 存对话消息（`messages` 列表）——内容短，每条都要存
- store 存方案全文（可能几千字）——太长不适合放在 State 里，按 namespace 隔离

---

## Phase 3：MCP 外部工具接入

### 3.1 什么是 MCP

MCP（Model Context Protocol）是一种标准化协议，让 LLM 调用外部工具。类似于 USB 接口——不同工具（高德地图、天气、12306）都通过统一的 MCP 协议暴露能力，LangChain 通过 `langchain-mcp-adapters` 连接。

MCP 支持两种传输方式：
- **HTTP**：远程服务（高德、天气、航班），通过 URL 连接
- **stdio**：本地进程（12306-mcp），通过 `npx` 启动子进程

### 3.2 `app/agents/hangout/mcp_client.py` — MCP 连接与工具分组

**作用**：启动时连接所有 MCP 服务，获取工具列表，按子 Agent 职责分组。

核心逻辑：
1. 每个 MCP 服务用 `MultiServerMCPClient` 连接
2. 获取到的工具是 `BaseTool` 对象列表
3. 按 Agent 需要分组：天气工具给 weather_expert，路线工具给 route_expert...

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

# 示例：连接高德地图 MCP
async def get_amap_tools() -> list:
    client = MultiServerMCPClient({
        "gaode-map": {
            "transport": "http",
            "url": "https://open.bigmodel.cn/api/mcp-broker/proxy/gaode-map/mcp?Authorization=your_key",
            "headers": {"Authorization": "Bearer your_key"},
            "timeout": timedelta(seconds=30),
        }
    })
    return await client.get_tools()
    # 返回: [maps_geo, maps_distance, maps_direction_driving, maps_around_search, ...]
```

**工具分组规则**（`split_tools` 函数）：

| 分组 | 包含的工具 | 分配给 |
|------|-----------|--------|
| supervisor | maps_geo, maps_distance | Supervisor 直接用（判断同城/跨城） |
| weather | 墨迹天气全套（优先）或 maps_weather（回退） | weather_expert |
| route | maps_geo + 4 种路线规划 + maps_distance | route_expert |
| poi | maps_geo + maps_around_search + maps_text_search | poi_expert |
| train | 12306-mcp 工具 | train_expert |
| flight | 航班 MCP 工具 | flight_expert |
| hotel | 住宿 MCP 工具 | hotel_expert |

**注意**：maps_geo 出现在多个分组里——路线专家需要先地理编码才能规划路线，POI 专家也需要坐标才能搜索周边。

**容错设计**：可选 MCP（12306/航班/住宿）连接失败不会阻塞启动，只是该子 Agent 不可用。

---

## Phase 4：State 与 Command 工具

### 4.1 `app/agents/hangout/tools.py` — HangoutState 定义

**作用**：定义出行会话的结构化状态 + 状态更新工具。

#### HangoutState

`AgentState` 是 LangChain 提供的基类，自带 `messages` 字段（对话历史）。我们扩展 12 个出行相关字段：

```python
from langchain.agents import AgentState

class HangoutState(AgentState):
    destination: str = ""           # 目的地（如"杭州西湖"）
    date: str = ""                  # 出行日期（如"5月31日"）
    origin: str = ""                # 出发地（如"上海"）
    weather_checked: bool = False   # 是否已查过天气
    weather_ok: bool = False        # 天气是否适合出行
    weather_summary: str = ""       # 天气一句话总结
    trip_type: str = ""             # "same_city" 或 "cross_city"
    transport_preference: str = ""  # 交通偏好（公交/自驾/步行）
    dining_preference: str = ""     # 用餐偏好
    nearby_preference: str = ""     # 周边偏好
    hotel_needed: str = ""          # 是否需要住宿
    plan_saved: bool = False        # 最终方案是否已保存
```

**设计原则**：
- 所有字段都有默认值（旧 checkpoint 加载不会报错）
- 结构化字段（不是自由文本）存在 State，方案全文存在 Store
- 非 `Annotated` 字段用 replace 语义（最后写入覆盖）

#### Command 工具

Command 工具是 Handoffs 模式的核心。工具函数返回 `Command(update={...})`，LangGraph 引擎收到后会原子性更新 State。

```python
from langgraph.types import Command

@tool
def update_trip_info(
    tool_call_id: Annotated[str, InjectedToolCallId],
    destination: str = "",
    date: str = "",
    origin: str = "",
    # ...
) -> Command:
    """用户说了目的地/日期/出发地时调用，保存到 State。"""
    updates = {k: v for k, v in raw.items() if v}
    return Command(update={
        **updates,                    # 更新 State 字段
        "messages": [ToolMessage(     # 给 LLM 的回执
            f"已更新出行信息：{summary}",
            tool_call_id=tool_call_id,
        )],
    })
```

**为什么用 Command 而不是直接返回字符串？**

普通 tool 返回字符串只能告诉 LLM 结果。Command 可以同时：
1. 更新 State 字段（`weather_checked = True`）
2. 给 LLM 回执消息（ToolMessage）
3. 触发中间件重新注入 Prompt（因为 State 变了）

#### interrupt 工具

`ask_weather_concern` 使用 `interrupt()` 暂停执行，等待用户在前端弹窗里做决策：

```python
from langgraph.types import interrupt

@tool
def ask_weather_concern(weather_summary: str) -> str:
    """天气不理想时暂停，等用户确认。"""
    result = interrupt({
        "type": "weather_confirm",
        "message": "天气看起来不太理想，你是否在意？",
        "weather_summary": weather_summary,
    })
    # interrupt() 会暂停整个图执行
    # 前端收到 interrupt 事件后弹窗
    # 用户点击后，前端发送 Command(resume={...}) 恢复执行
    # result 就是用户的决策
    if _is_approved(result):
        return "用户不介意当前天气，继续规划。"
    return "用户在意天气，需要调整时间。"
```

#### Store 工具

方案全文太长（可能几千字），不适合放在 State 里（每轮都要传给 LLM）。用 Store 按 namespace 存取：

```python
@tool
async def save_final_plan(config: RunnableConfig, ..., plan: str) -> Command:
    """保存最终方案到 Store。"""
    namespace = ("travel_plan", user_id, thread_id)
    await store.aput(namespace, "final", {"plan": plan})
    return Command(update={"plan_saved": True, ...})

@tool
async def get_final_plan(config: RunnableConfig) -> str:
    """从 Store 读取最终方案（email_expert 调用）。"""
    data = await store.aget(namespace, "final")
    return data.get("plan", "")
```

---

## Phase 5：子 Agent 定义

### 5.1 `app/agents/hangout/prompts.py` — 所有 Prompt

**作用**：集中管理所有 Agent 的系统提示词。每个子 Agent 有独立 Prompt，定义它的职责和规则。

Prompt 设计原则：
- **明确职责边界**：天气专家只查天气，不规划路线
- **规定输出格式**：列出需要输出的信息项
- **写明禁止项**：不要编造数据、不要调用不该调的工具
- **Supervisor Prompt 最复杂**：包含完整的 10 步主流程和约束规则

```python
SUPERVISOR_PROMPT = """你是 HangoutAgent 出行助手的 Supervisor。
...
## 必须遵守的主流程
0. 不做 IP 定位...
1. 先收集基础信息...
2. 目的地和日期齐全后，先查天气...
3. 天气不好时必须 ask_weather_concern...
4. 天气通过后问出发地...
5. 判断同城/跨城...
...
"""

WEATHER_PROMPT = """你是天气调研专家。任务：查询天气，判断是否适合出行。..."""
ROUTE_PROMPT   = """你是路线规划专家。任务：规划交通路线。..."""
POI_PROMPT     = """你是周边推荐专家。任务：搜索周边 POI。..."""
EMAIL_PROMPT   = """你是邮件发送专家。任务：读取方案并发送邮件。..."""
TRAIN_PROMPT   = """你是火车查询专家。任务：查询火车/高铁车次。..."""
FLIGHT_PROMPT  = """你是航班查询专家。任务：查询可用航班。..."""
HOTEL_PROMPT   = """你是住宿推荐专家。任务：查询目的地酒店。..."""
```

### 5.2 `app/agents/hangout/agents.py` — 子 Agent 工厂

**作用**：用 `create_agent` 创建每个子 Agent。每个子 Agent = 一个 LLM + 专属工具集 + 专属 Prompt。

```python
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

def _model():
    return init_chat_model("deepseek-chat", streaming=True)

def create_weather_agent(tools: list):
    """天气专家：接收墨迹天气 MCP 工具。"""
    return create_agent(
        _model(),
        tools=tools,                    # 墨迹天气 MCP 工具集
        name="weather_expert",
        system_prompt=WEATHER_PROMPT,
    )

def create_route_agent(tools: list):
    """路线专家：接收高德路线规划工具。"""
    return create_agent(_model(), tools=tools, name="route_expert", system_prompt=ROUTE_PROMPT)

def create_poi_agent(tools: list):
    """POI 专家：接收高德搜索工具。"""
    return create_agent(_model(), tools=tools, name="poi_expert", system_prompt=POI_PROMPT)

def create_email_agent():
    """邮件专家：工具是 get_final_plan + send_final_plan_email（不需要 MCP）。"""
    return create_agent(_model(), tools=[get_final_plan, send_final_plan_email],
                        name="email_expert", system_prompt=EMAIL_PROMPT)

# create_train_agent, create_flight_agent, create_hotel_agent 同理
```

**`create_agent` 返回什么？**

返回 `CompiledStateGraph`——一个已编译的 LangGraph 图，可以直接 `.invoke()` / `.ainvoke()` 调用。内部是 `model ↔ tools` 循环：LLM 决定调哪个工具 → 执行工具 → 结果回传 LLM → LLM 决定继续还是输出。

---

## Phase 6：Supervisor 编排

### 6.1 `app/agents/hangout/supervisor.py` — 核心编排逻辑

这是整个项目最复杂的文件。它做三件事：
1. **初始化**：连接 MCP、创建子 Agent、组装 Supervisor
2. **`@dynamic_prompt` 中间件**：每轮注入状态到 Prompt
3. **SSE 流式输出**：将 LangGraph 的流式事件转为前端可消费的 SSE

#### 6.1.1 Subagents 模式：子 Agent 包装为 Tool

核心思想：每个子 Agent 被包装成一个普通的 `@tool` 函数。Supervisor 看到的是 "weather_expert 工具"，不知道它背后是另一个 Agent。

```python
from langchain_core.tools import tool

def _wrap_agent_as_tool(agent, name: str, description: str):
    @tool(name, description=description)
    async def _call_agent(request: str) -> str:
        # 子 Agent 独立执行：接收请求 → 调用 MCP 工具 → 返回结果
        result = await agent.ainvoke({"messages": [{"role": "user", "content": request}]})
        return result["messages"][-1].content
    return _call_agent
```

#### 6.1.2 `@dynamic_prompt` 中间件

LangChain 的中间件系统允许在每轮 LLM 调用前/后插入逻辑。`@dynamic_prompt` 是专门用于动态替换系统提示词的装饰器。

**为什么需要动态 Prompt？**

静态 Prompt 无法感知当前状态。比如用户已经说了目的地和日期，你需要告诉 LLM "现在必须查天气"——这个约束只有在特定状态下才应该出现。

```python
from langchain.agents.middleware import dynamic_prompt, ModelRequest

@dynamic_prompt
def _inject_state_prompt(request: ModelRequest) -> str:
    """每轮模型调用前，读取 State，生成动态 Prompt。"""
    state = request.state
    parts = [SUPERVISOR_PROMPT]

    # 注入已收集的信息
    if state.get("destination"):
        parts.append(f"- 目的地：{state['destination']}")
    if state.get("weather_checked"):
        parts.append(f"- 天气：{'适合' if state['weather_ok'] else '不理想'}")

    # 注入阶段约束
    if state.get("destination") and state.get("date") and not state.get("weather_checked"):
        parts.append("⚠️ 下一步必须调用 weather_expert 查天气！")

    return "\n".join(parts)
```

**运行时流程**：

```
用户消息 → State 更新 → @dynamic_prompt 读 State 生成 Prompt → LLM 看到新 Prompt → 做出决策
```

#### 6.1.3 Supervisor 组装

```python
class HangoutSupervisor:
    async def init(self):
        # 1. 初始化持久化
        await session_manager.init()

        # 2. 连接 MCP，获取工具
        tools = await get_hangout_tools()

        # 3. 创建子 Agent 并包装为 tool
        agent_tools = [
            _wrap_agent_as_tool(create_weather_agent(tools["weather"]),
                                "weather_expert", "查询天气"),
            _wrap_agent_as_tool(create_route_agent(tools["route"]),
                                "route_expert", "规划路线"),
            # ... 其他子 Agent
        ]

        # 4. 组合所有工具
        all_tools = agent_tools + [
            update_trip_info,       # Command 工具
            mark_weather_result,
            mark_trip_type,
            ask_weather_concern,    # interrupt 工具
            save_final_plan,
        ]

        # 5. 创建 Supervisor Agent
        model = init_chat_model("deepseek-chat", streaming=True)
        self.graph = create_agent(
            model=model,
            tools=all_tools,
            system_prompt=SUPERVISOR_PROMPT,
            state_schema=HangoutState,        # 自定义 State
            middleware=[_inject_state_prompt], # 动态 Prompt 中间件
            checkpointer=session_manager.checkpointer,
            store=session_manager.store,
        )
```

**create_agent 参数解释**：

| 参数 | 作用 |
|------|------|
| `model` | LLM 实例（DeepSeek Chat） |
| `tools` | Supervisor 可用的所有工具（子 Agent + Command + 地图） |
| `system_prompt` | 基础 Prompt（会被 middleware 覆盖） |
| `state_schema` | 自定义 State 类型（扩展了 AgentState） |
| `middleware` | 中间件列表（@dynamic_prompt） |
| `checkpointer` | 对话持久化后端 |
| `store` | 数据存储后端（方案全文） |

返回的 `self.graph` 是一个 `CompiledStateGraph`，图结构为 `model ↔ tools` 循环。

#### 6.1.4 SSE 流式输出

`generate_sse` 方法监听 LangGraph 的流式事件，转成前端可消费的 SSE：

```python
async def generate_sse(self, thread_id, message, interrupt_decision=None):
    # 准备输入
    if interrupt_decision:
        inp = Command(resume={"decisions": [dict(interrupt_decision)]})
    else:
        inp = {"messages": [{"role": "user", "content": message}]}

    # 流式处理
    async for chunk in self.graph.astream(inp, config=config,
                                          stream_mode=["messages", "updates"]):
        kind, data = _unpack(chunk)

        if kind == "messages":
            token, meta = data

            # 检测 tool 调用 → 发状态提示
            for tc in (token.tool_call_chunks or []):
                if tc.get("name") in _TOOL_HINTS:
                    yield sse_event("status", _TOOL_HINTS[tc["name"]])

            # 只展示 model 节点的文字（Supervisor 的回复）
            if meta.get("langgraph_node") == "model":
                yield sse_event("message_delta", text)

        elif kind == "updates":
            # 检测 interrupt → 发弹窗事件
            for item in _interrupts(data):
                yield sse_json_event("interrupt", {...})

    yield sse_event("done", "")
```

**stream_mode 说明**：

- `"messages"`：每个 LLM token、每条 ToolMessage 都会推送。用于逐字流式输出。
- `"updates"`：每个节点执行完毕后推送完整更新。用于捕获 interrupt。

**过滤逻辑**：
- `langgraph_node == "model"` → Supervisor 的回复，展示给用户
- `langgraph_node == "tools"` → 工具执行过程（包括子 Agent 内部），不展示
- `_is_noise()` → 过滤掉 "transferring..." 等框架噪音

---

## Phase 7：API 路由与流式输出

### 7.1 `app/api/v1/hangout.py` — REST API

**作用**：定义 3 个 HTTP 接口。

```python
from fastapi import APIRouter
from sse_starlette import EventSourceResponse

router = APIRouter()

# POST /api/v1/hangout/send — 发消息（SSE 流式返回）
@router.post("/hangout/send")
async def send_hangout(request: HangoutRequest):
    return EventSourceResponse(
        hangout_supervisor.generate_sse(
            thread_id=request.thread_id,
            message=request.message,
            interrupt_decision=request.interrupt_decision,
        )
    )

# GET /api/v1/hangout/messages — 获取历史（切换会话时用）
@router.get("/hangout/messages")
async def get_messages(thread_id: str):
    return await hangout_supervisor.get_messages(thread_id)

# DELETE /api/v1/hangout/messages — 清除历史
@router.delete("/hangout/messages")
async def clear_messages(thread_id: str):
    await hangout_supervisor.clear_messages(thread_id)
```

**HangoutRequest 模型**：

```python
class HangoutRequest(BaseModel):
    message: str = ""                              # 用户消息
    thread_id: str = "default"                     # 会话 ID
    interrupt_decision: Optional[Dict] = None      # interrupt 恢复时的用户决策
```

### 7.2 `app/main.py` — FastAPI 入口

**作用**：应用入口，通过 `lifespan` 管理生命周期。

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    await hangout_supervisor.init()   # 启动时：连 MCP、创建 Agent
    yield
    await hangout_supervisor.close()  # 关闭时：释放连接

app = FastAPI(title="HangoutAgent 出行企划助手", lifespan=lifespan)
app.include_router(hangout.router, prefix="/api/v1")

# 挂载前端静态文件
app.mount("/", StaticFiles(directory="app/static", html=True))
```

**为什么用 lifespan 而不是 @app.on_event？**

`lifespan` 是 FastAPI 推荐的新方式。`on_event("startup")` 已废弃。lifespan 用 async context manager，启动和关闭逻辑写在同一个函数里，更清晰。

---

## Phase 8：前端 SPA

### 8.1 `app/static/index.html` — 单文件前端

**作用**：1199 行的单文件 SPA（HTML + CSS + JS），实现：

- 左侧：会话列表（新建/切换/删除会话）
- 右侧：聊天界面（消息展示 + 输入框）
- interrupt 弹窗（天气确认 / 邮件确认）
- Markdown 渲染（表格、列表、代码块）
- SSE 流式接收 + 逐字展示

**核心 JS 逻辑**：

```javascript
// 发送消息（SSE 流式接收）
async function sendMessage(text) {
    const response = await fetch("/api/v1/hangout/send", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            message: text,
            thread_id: currentThreadId(),
        }),
    });

    const reader = response.body.getReader();
    // 逐行读取 SSE 事件
    while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        const lines = decode(value).split("\n");
        for (const line of lines) {
            if (line.startsWith("event: ")) eventType = line.slice(7);
            if (line.startsWith("data: ")) handleEvent(eventType, JSON.parse(line.slice(6)));
        }
    }
}

// 处理 interrupt 事件 → 弹窗
function handleEvent(type, data) {
    if (type === "message_delta") appendText(data.content);
    if (type === "status") showStatus(data.content);
    if (type === "interrupt") showInterruptDialog(data.interrupt);
    if (type === "done") finishMessage();
}

// interrupt 弹窗确认 → 恢复执行
function approveInterrupt() {
    fetch("/api/v1/hangout/send", {
        method: "POST",
        body: JSON.stringify({
            thread_id: currentThreadId(),
            interrupt_decision: {type: "approve"},
        }),
    });
}
```

**会话管理**：用 `localStorage` 存会话列表和活跃会话 ID，刷新页面不丢失。

---

## Phase 9：Gmail 邮件集成

### 9.1 `app/integrations/gmail_auth.py` — OAuth 路径管理

```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"   # Google OAuth 凭据
TOKEN_PATH = PROJECT_ROOT / "token.json"               # 授权后生成的 token
```

### 9.2 `app/integrations/gmail_tools.py` — Gmail 工具

```python
from langchain_google_community import GmailToolkit

def get_gmail_tools():
    toolkit = GmailToolkit()
    return toolkit.get_tools()  # 返回 [send_gmail_message, ...]
```

### 9.3 邮件发送流程

邮件不是直接发的——经过 interrupt 人机确认：

```
1. 用户确认方案 → Supervisor 调用 save_final_plan
2. Supervisor 调用 email_expert 工具
3. email_expert 内部：
   a. get_final_plan → 从 Store 读取方案
   b. send_final_plan_email → 触发 interrupt
4. 前端收到 interrupt 事件 → 弹窗显示收件人和内容
5. 用户点"确认" → 前端发送 Command(resume={type: "approve"})
6. send_final_plan_email 恢复执行 → 调用 Gmail API 发送
```

---

## Phase 10：调试与优化

### 10.1 LangSmith 追踪

在 `.env` 中配置 `LANGSMITH_TRACING=true` 后，所有 LLM 调用、工具执行、Agent 流转都会上传到 LangSmith。可以看到：

- 每轮的完整 Prompt（包括 @dynamic_prompt 注入的部分）
- 每次 tool call 的参数和返回值
- 子 Agent 内部的多轮调用过程
- Token 消耗和延迟

### 10.2 已踩过的坑

**QPS 限制**：route_expert 和 poi_expert 都用高德 API，并发调用会触发 `CUQPS_HAS_EXCEEDED_THE_LIMIT`。

解决方案：在 Supervisor Prompt 里加约束——"route_expert 和 poi_expert 不能并发，必须串行调用"。用 Prompt 约束比代码重试更可靠（试过 monkey-patch 工具加重试，Pydantic 模型限制导致失败）。

**State 字段设计**：一开始方案全文也放在 State 里，导致每轮 Prompt 都包含几千字的方案。改为 Store 后，State 只存标志位 `plan_saved: bool`，方案全文按 namespace 存 Store。

**子 Agent 信息泄露**：子 Agent 的内部思考、工具调用过程不应展示给用户。通过 `_visible(meta)` 过滤，只展示 `model` 节点（Supervisor）的输出。

---

## 附录：核心概念速查

### LangChain / LangGraph 关系

```
LangChain（核心库）
├── create_agent          # 创建 Agent（返回 CompiledStateGraph）
├── init_chat_model       # 初始化 LLM
├── @tool                 # 定义工具
├── AgentState            # Agent 状态基类
├── @dynamic_prompt       # 动态 Prompt 中间件
└── langchain-mcp-adapters  # MCP 协议适配

LangGraph（图执行引擎）
├── Command               # 状态更新指令
├── interrupt()            # 暂停执行，等待人工确认
├── Checkpointer           # 对话持久化
└── Store                  # 数据存储（方案全文等）
```

### Subagents vs Handoffs

| 概念 | 本项目中的体现 |
|------|--------------|
| **Subagents** | 子 Agent 包装为 tool，Supervisor 通过 tool calling 调度 |
| **Handoffs** | HangoutState + Command 驱动阶段流转（先天气 → 再路线） |
| **混合使用** | Supervisor 既调度子 Agent（Subagents），又管理状态流转（Handoffs） |

### 数据流向

```
用户输入
  → FastAPI 路由
    → HangoutSupervisor.generate_sse()
      → self.graph.astream()
        → model 节点：LLM 看到 @dynamic_prompt 注入的 Prompt → 决定调哪个 tool
          → tools 节点：执行 tool
            → 如果是子 Agent tool：子 Agent 内部 model ↔ tools 循环
            → 如果是 Command tool：更新 State → 触发下轮 @dynamic_prompt 重新注入
            → 如果是 interrupt tool：暂停，等前端回传决策
          → 结果回到 model 节点
        → model 节点生成回复
      → SSE 事件推送给前端
    → 前端逐字展示 / 弹窗确认
```

### 文件依赖关系

```
main.py
  ├── api/v1/hangout.py          → supervisor.py
  └── supervisor.py
        ├── agents.py            → prompts.py, tools.py
        ├── tools.py             → (AgentState, Command, interrupt, Store)
        ├── mcp_client.py        → (.env 配置)
        ├── prompts.py           → (纯文本，无依赖)
        └── models/session.py    → (Redis / SQLite)
```
