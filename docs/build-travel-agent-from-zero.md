# 从零构建出行企划 Multi-Agent 系统

> 基于 LangChain + LangGraph Supervisor 模式，使用 `create_supervisor` + `create_agent` 构建，不写一行自定义 StateGraph。

## 目录

1. [项目概览](#1-项目概览)
2. [架构设计](#2-架构设计)
3. [环境准备](#3-环境准备)
4. [Step 1：项目骨架](#step-1项目骨架) — `logger.py`、`main.py`
5. [Step 2：MCP 工具接入](#step-2mcp-工具接入) — `mcp_client.py`
6. [Step 3 ~ 9：按文件编写代码](#step-3--9按文件编写代码) — **每个文件一份完整代码，直接复制**
   - [`common/sse.py`](#appcommonssepy) — SSE 序列化工具
   - [`models/schemas.py`](#appmodelsschemaspy) — 请求/响应模型
   - [`models/session.py`](#appmodelssessionpy) — 会话管理（checkpointer + store）
   - [`prompts.py`](#appagentstravelpromptspy) — 所有 prompt
   - [`tools.py`](#appagentstraveltoolspy) — 邮件 + IP定位HITL + 长期记忆
   - [`agents.py`](#appagentstravelagentspy) — 4 个子 agent + HITL middleware
   - [`supervisor.py`](#appagentstravelsupervisorpy) — 核心：编排 + 流式调用（精简版）
   - [`api/v1/travel.py`](#appapiv1travelpy) — FastAPI 路由
   - [`static/index.html`](#appstaticindexhtmlscript-部分) — 前端多会话 + HITL 弹窗
7. [附录：常见问题](#附录常见问题)

---

## 1. 项目概览

### 做什么

一个出行企划助手，用户说"我想去西湖玩"，系统自动：
1. **IP 定位**出发地 → 需要**人工确认**才执行（隐私 HITL）
2. 查天气 → 墨迹天气 MCP 判断是否适合出行
3. 搜景点 → 推荐周边好去处
4. 规划路线 → 对比驾车/公交/步行
5. 生成方案 → 2-3 个不同风格的行程
6. 发邮件邀请朋友 → **人工确认后发送**（邮件 HITL）

### 两个人工介入点

| 触发时机 | 原因 | 用户操作 |
|---------|------|---------|
| Supervisor 调用 `maps_ip_location` | 获取用户 IP 位置涉及隐私 | 确认/拒绝（拒绝后手动输入出发地） |
| Planner 调用 `send_invite_email` | 发邮件是不可撤回操作 | 确认/拒绝/修改邮件内容 |

### 技术栈

| 组件 | 技术 |
|------|------|
| Agent 框架 | LangChain `create_agent` + LangGraph `create_supervisor` |
| LLM | DeepSeek（兼容 OpenAI 接口） |
| 地图工具 | 高德 MCP Server（地理编码、搜索、路线） |
| 天气工具 | 墨迹天气 MCP Server（实况/预报/空气质量/预警） |
| 邮件 | Gmail API（langchain-google-community） |
| 后端 | FastAPI + SSE |
| 持久化 | SQLite（AsyncSqliteSaver） |

### 为什么用 Supervisor 而不是自定义 StateGraph

LangChain 官方文档列出了 5 种 multi-agent 模式：

| 模式 | 说明 | 适合场景 |
|------|------|---------|
| **Subagents** | 主 agent 把子 agent 当 tool 调 | 并行化、多步骤 |
| **Handoffs** | agent 之间动态交接控制权 | 多轮对话、有状态 |
| **Skills** | 单 agent 按需加载专业 prompt | 简单聚焦任务 |
| **Router** | 路由层分类 → 分发 → 汇总 | 并行、多领域 |
| **Custom Workflow** | 自定义 StateGraph | 高度特殊化需求 |

我们的出行助手是典型的 **Supervisor** 场景：一个协调者管理多个专业 agent。`create_supervisor` 帮你搞定路由、状态管理、handoff，不用手写 `add_node`/`add_edge`/`Send()`/条件边。

`parallel_tool_calls=True` 让 supervisor 可以一次调多个子 agent，**自动并发**。

---

## 2. 架构设计

### 系统架构图

```
用户
 │
 ▼
FastAPI (SSE)
 │
 ▼
┌──────────────────────────────────────────────┐
│            Supervisor（协调者）                 │
│  "收集出行信息，分配任务给专家 agent"             │
│                                               │
│  tools: maps_ip_location ← HITL 确认！         │
│         maps_geo                               │
│                                               │
│  parallel_tool_calls=True                      │
│  ┌──────────┬──────────┬──────────┐            │
│  ▼          ▼          ▼          │            │
│ Weather   POI Agent  Route       │            │
│ Agent     (景点)     Agent       │            │
│ (墨迹MCP)            (路线)      │            │
│  │          │          │          │            │
│  └──────────┴──────────┘          │            │
│             │                     │            │
│             ▼                     │            │
│        Planner Agent              │            │
│        (生成方案)                  │            │
│             │                     │            │
│             ▼                     │            │
│     send_invite_email ← HITL 确认！│            │
│             │                     │            │
│             ▼                     │            │
│        Gmail API 发送              │            │
└──────────────────────────────────────────────┘
```

### 代码结构

```
app/
├── main.py                     # FastAPI 入口
├── agents/
│   └── travel/
│       ├── supervisor.py       # Supervisor 编排（只管编排 + 流式调用）
│       ├── agents.py           # 4 个子 agent 定义
│       ├── tools.py            # 自定义 tools（邮件、IP定位、记忆）
│       ├── prompts.py          # 所有 prompt
│       └── mcp_client.py       # MCP 工具获取
├── models/
│   ├── schemas.py              # 请求/响应 Pydantic 模型
│   └── session.py              # 会话管理（checkpointer/store 初始化、历史查询）
├── api/v1/
│   └── travel.py               # REST API
├── integrations/
│   ├── gmail_auth.py           # Gmail OAuth
│   └── gmail_tools.py          # Gmail 工具
├── common/
│   ├── logger.py               # 日志
│   └── sse.py                  # SSE 序列化工具函数
└── static/
    └── index.html              # 前端
```

### 和旧架构的对比

| | 旧架构（自定义 StateGraph） | 新架构（Supervisor） |
|---|---|---|
| 核心文件 | `pipeline.py`（400+ 行） | `supervisor.py`（~120 行） |
| 状态管理 | 手写 `TravelState`（12 个字段） | supervisor 自动管理 |
| 路由 | `entry_node` + 3 个条件边函数 | supervisor LLM 自动路由 |
| 并发 | `Send()` fan-out + fan-in 边 | `parallel_tool_calls=True` |
| 子 agent | 包装函数 + `ainvoke` 手动调 | supervisor 自动 handoff |
| HITL | 手写 `interrupt()` + 收集 payload | `HumanInTheLoopMiddleware`（两个触发点） |
| 流式输出 | 手动解析 namespace 去重 | `stream_mode` 直接用 |

---

## 3. 环境准备

### 安装依赖

```bash
pip install langchain langgraph langgraph-supervisor \
    langchain-mcp-adapters langchain-google-community \
    fastapi sse-starlette uvicorn aiosqlite python-dotenv
```

### 配置 .env

```env
# LLM
DEEPSEEK_API_KEY=your_deepseek_key

# 高德地图 MCP
AMAP_MAPS_API_KEY=your_amap_key

# 墨迹天气 MCP（通过智谱 BigModel 代理）
WEATHER_MCP_TRANSPORT=http
WEATHER_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/moji-weather/mcp?Authorization=your_key
WEATHER_MCP_API_KEY=your_bigmodel_key

# Gmail（可选，不配则跳过邮件功能）
# 需要 credentials.json + token.json
```

### Gmail OAuth 配置（可选）

1. 在 Google Cloud Console 创建 OAuth 2.0 客户端
2. 下载 `credentials.json` 放到项目根目录
3. 首次运行时会引导浏览器完成授权，生成 `token.json`

---

## Step 1：项目骨架

创建目录结构：

```bash
mkdir -p app/agents/travel app/api/v1 app/integrations app/common app/static app/db
touch app/__init__.py app/agents/__init__.py app/agents/travel/__init__.py
touch app/api/__init__.py app/api/v1/__init__.py
touch app/integrations/__init__.py app/common/__init__.py
```

### app/common/logger.py

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("travel-agent")
```

### app/main.py

```python
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.agents.travel.supervisor import travel_supervisor
from app.api.v1 import travel


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化 supervisor
    await travel_supervisor.init()
    yield
    # 关闭时清理资源
    await travel_supervisor.close()


app = FastAPI(title="TripCrew 出行企划助手", lifespan=lifespan)
app.include_router(travel.router, prefix="/api/v1")

# 静态文件（前端）
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)
```

---

## Step 2：MCP 工具接入

MCP（Model Context Protocol）让 agent 能调用外部工具。我们用两个 MCP Server：
- **高德地图 MCP**：地理编码、搜索、路线规划
- **墨迹天气 MCP**：实况天气、15天预报、空气质量、天气预警

### 工具分配

| Agent | 工具来源 | 具体工具 |
|-------|---------|---------|
| Supervisor | 高德 MCP | `maps_ip_location`（IP定位，需 HITL）、`maps_geo`（地理编码） |
| Weather Agent | 墨迹天气 MCP | `condition`（实况）、`forecast15Days`（15天预报）、`aqi`（空气质量）、`alert`（预警）等 8 个 |
| POI Agent | 高德 MCP | `maps_text_search`、`maps_around_search`、`maps_search_detail` |
| Route Agent | 高德 MCP | `maps_geo`、`maps_direction_driving`、`maps_direction_transit_integrated`、`maps_direction_walking` |

### app/agents/travel/mcp_client.py

```python
import os
from datetime import timedelta
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()


async def get_amap_tools() -> list:
    """启动高德 MCP Server，获取所有工具。"""
    api_key = os.getenv("AMAP_MAPS_API_KEY")
    if not api_key:
        raise ValueError("AMAP_MAPS_API_KEY 未设置！")

    client = MultiServerMCPClient({
        "amap-maps": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {"AMAP_MAPS_API_KEY": api_key},
        }
    })
    return await client.get_tools()


async def get_weather_mcp_tools() -> list:
    """获取墨迹天气 MCP 工具。

    需要配置 WEATHER_MCP_URL（智谱 BigModel 代理地址）。
    未配置则返回空列表，回退到高德的 maps_weather。
    """
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


def split_tools(amap_tools: list, weather_tools: list) -> dict:
    """按 Agent 职责分组工具。

    返回:
        {
            "supervisor": [...],  # maps_ip_location, maps_geo（Supervisor 自己的工具）
            "weather": [...],     # 墨迹天气工具（或高德 maps_weather 回退）
            "poi": [...],         # maps_text_search, maps_around_search
            "route": [...],       # maps_direction_*, maps_geo
        }
    """
    groups = {
        "supervisor": {"maps_ip_location", "maps_geo"},
        "weather": {"maps_weather"},  # 回退用，优先用墨迹
        "poi": {"maps_text_search", "maps_around_search", "maps_search_detail"},
        "route": {
            "maps_geo",
            "maps_direction_driving",
            "maps_direction_transit_integrated",
            "maps_direction_walking",
            "maps_bicycling",
        },
    }

    tool_index = {t.name: t for t in amap_tools}
    result = {}
    for group_name, tool_names in groups.items():
        result[group_name] = [tool_index[n] for n in tool_names if n in tool_index]

    # 墨迹天气覆盖高德天气
    if weather_tools:
        result["weather"] = weather_tools

    return result


async def get_travel_tools() -> dict:
    """获取并分组所有 MCP 工具。"""
    amap_tools = await get_amap_tools()
    weather_tools = await get_weather_mcp_tools()
    return split_tools(amap_tools, weather_tools)
```

> **关键设计**：
> - `maps_ip_location` 分给 Supervisor 而不是子 agent——因为它需要 HITL 确认，Supervisor 的 middleware 会拦截它
> - `split_tools` 让每个 agent 只拿到自己需要的工具，这就是 LangChain 说的 **context engineering**
> - 墨迹天气优先，没配置时回退到高德天气

---

## Step 3 ~ 9：按文件编写代码

> **阅读方式变了！** 从这里开始，按**文件**组织，每个文件给出**完整最终版代码**，直接复制就能用。
> 不再分散到多个 Step 里让你拼凑。

每个文件标题下会标注它涉及的功能点：

| 文件 | 包含的功能 |
|------|-----------|
| `common/sse.py` | SSE 序列化工具函数 |
| `models/schemas.py` | 请求/响应 Pydantic 模型 |
| `models/session.py` | 会话管理：checkpointer + store 初始化、历史查询、会话清除 |
| `prompts.py` | 所有 agent 的 system prompt |
| `tools.py` | 邮件工具 + IP定位 HITL 包装 + 长期记忆读写 |
| `agents.py` | 4 个子 agent 定义（含 HITL middleware） |
| `supervisor.py` | Supervisor 编排 + 流式调用（精简，依赖 session 和 sse 模块） |
| `api/v1/travel.py` | FastAPI 路由 |
| `static/index.html` | 前端（SSE + HITL 弹窗 + 多会话） |

---

### app/common/sse.py

SSE 序列化工具，所有需要返回 SSE 事件的地方都用这个。

```python
# app/common/sse.py
"""SSE（Server-Sent Events）序列化工具。"""

import json


def sse_event(event_type: str, content: str) -> dict:
    """构造普通文本 SSE 事件。"""
    return {
        "event": event_type,
        "data": json.dumps(
            {"type": event_type, "content": content},
            ensure_ascii=False,
        ),
    }


def sse_json_event(event_type: str, payload: dict) -> dict:
    """构造 JSON SSE 事件（用于 interrupt 等复杂数据）。"""
    return {
        "event": event_type,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }


def serialize(obj):
    """将 LangGraph 对象（Interrupt、Command 等）转为可 JSON 序列化的格式。"""
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

---

### app/models/schemas.py

请求和响应的 Pydantic 模型，API 层和 Supervisor 都引用它。

```python
# app/models/schemas.py
"""请求 / 响应数据模型。"""

from typing import Any, Dict, Optional
from pydantic import BaseModel


class TravelRequest(BaseModel):
    """出行对话请求。"""
    message: str = ""
    thread_id: str = "default"
    interrupt_decision: Optional[Dict[str, Any]] = None
```

---

### app/models/session.py

会话管理：负责 checkpointer（短期记忆）和 store（长期记忆）的初始化，以及会话历史的读取/清除。

Supervisor 只管编排，**不碰持久化细节**。

```python
# app/models/session.py
"""会话管理：短期记忆（checkpointer）+ 长期记忆（store）。"""

import os

import aiosqlite
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.memory import InMemoryStore

from app.common.logger import logger

# 生产环境替换：
# from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
# from langgraph.store.postgres import AsyncPostgresStore


class SessionManager:
    """管理所有会话的生命周期。

    职责：
    - 初始化 checkpointer（短期记忆，按 thread_id 隔离）
    - 初始化 store（长期记忆，按 user_id 隔离）
    - 查询某个会话的历史消息
    - 清除某个会话
    """

    def __init__(self):
        self.conn = None
        self.checkpointer = None
        self.store = InMemoryStore()  # 长期记忆（重启丢失，生产用 PostgresStore）

    async def init(self):
        """初始化 SQLite checkpointer。"""
        db_path = os.path.join(
            os.path.dirname(__file__), "../db/travel.db"
        )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = await aiosqlite.connect(db_path)
        self.checkpointer = AsyncSqliteSaver(conn=self.conn)
        await self.checkpointer.setup()
        logger.info("SessionManager 初始化完成 ✓")

    async def close(self):
        """关闭数据库连接。"""
        if self.conn:
            await self.conn.close()

    async def get_messages(self, graph, thread_id: str) -> dict:
        """获取某个会话的历史消息（前端切换会话时调用）。

        Args:
            graph: 编译后的 LangGraph 图（用来读 state）
            thread_id: 会话 ID
        Returns:
            {"messages": [{"role": "user"|"assistant", "content": "..."}]}
        """
        if not graph:
            return {"messages": []}

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
        """清除某个会话的历史（前端删除会话时调用）。"""
        if self.checkpointer:
            await self.checkpointer.adelete_thread(thread_id)
            logger.info(f"会话 {thread_id} 已清除")


# 单例
session_manager = SessionManager()
```

> **为什么 `get_messages` 需要传 `graph`？**
> 因为 `aget_state` 是 graph 的方法——它从 checkpointer 里读状态，但需要知道 graph 的 schema 才能反序列化。
> SessionManager 不持有 graph 引用（那是 Supervisor 的事），所以调用时传进来。

---

### app/agents/travel/prompts.py

```python
# app/agents/travel/prompts.py
"""所有 Agent 的 System Prompt。"""

SUPERVISOR_PROMPT = """你是 CityBuddy 出行企划的总协调者。

## 你的职责
1. 和用户聊天，收集出行信息（目的地、日期）
2. 用 locate_user_by_ip 自动获取用户当前位置作为出发地（系统会请求用户确认）
3. 信息收集齐后，**同时**派出天气、景点、路线三个专家去调研
4. 三个专家都回来后，派 planner 生成出行方案
5. 如果用户要约朋友，让 planner 处理邮件邀请

## 出发地规则
- 不要主动问用户出发地，先调用 locate_user_by_ip 自动定位
- 如果用户拒绝定位或者定位不准，再问出发地
- 如果用户主动说了出发地，直接用，不用定位

## 其他规则
- 一次最多问 1 个问题
- 目的地必须问，日期没说就默认本周末
- 三个信息齐了就立刻派专家调研，不要等用户确认
- 调研任务尽量同时派出（parallel tool calls）
- 用自然中文回复
"""

WEATHER_PROMPT = """你是天气调研专家。

使用墨迹天气 MCP 工具查询目的地天气，总结：
- 当前实况天气和温度（用 condition 工具）
- 未来几天预报趋势（用 forecast15Days 工具）
- 空气质量（用 aqi 工具）
- 是否有天气预警（用 alert 工具）
- 综合判断是否适合户外出行

规则：
- 必须调用工具查询，不要编造天气
- 如果目的地是景点名（如"西溪湿地"），推断城市名（→"杭州"）再查
- 不需要调用所有工具，根据实际需要选择
"""

POI_PROMPT = """你是景点调研专家。

用搜索工具找目的地及周边值得去的地方，推荐 3-5 个：
- 名称、类型（景点/餐厅/咖啡馆）、推荐理由

规则：必须调用工具搜索，不要编造地点。
"""

ROUTE_PROMPT = """你是交通路线专家。

规划从出发地到目的地的路线：
1. 先用 maps_geo 把地名转成经纬度
2. 再调用 maps_direction_driving（驾车）和 maps_direction_transit_integrated（公交）

每种方式列出：交通方式、预计耗时、路线摘要。

规则：maps_direction_* 需要经纬度，必须先 maps_geo 转换。
"""

PLANNER_PROMPT = """你是行程规划专家。

## 任务
综合天气、景点、交通的调研结果，生成 2-3 个不同风格的出行方案：
- 悠闲半日游（轻松为主）
- 深度一日游（玩得全面）
- 美食探店路线（吃为主）

每个方案包含：方案名称、时间表、预计费用。

## 邮件邀请
- 方案生成后，问用户"一个人去还是约朋友？"
- 如果约朋友，问名字和邮箱
- 信息齐全后，调用 send_invite_email 工具发送邀请
- send_invite_email 会触发人工确认，不用担心误发

## 规则
- 只用调研结果中的真实数据
- 不要重复问用户已经提供过的信息
"""
```

---

### app/agents/travel/tools.py

包含：邮件发送工具、IP 定位 HITL 包装、长期记忆读写。

```python
# app/agents/travel/tools.py
"""自定义工具：邮件邀请、IP 定位（HITL）、用户偏好记忆。"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.store.base import BaseStore
from langgraph.types import interrupt


# ============================================================
# 1. 邮件邀请工具（Planner 调用，HITL 由 middleware 在外部拦截）
# ============================================================

@tool
def send_invite_email(
    to_name: str,
    to_email: str,
    subject: str,
    body: str,
) -> str:
    """发送出行邀请邮件给朋友。

    参数：
    - to_name: 收件人名字
    - to_email: 收件人邮箱
    - subject: 邮件主题
    - body: 邮件正文（Markdown 格式）
    """
    from app.integrations.gmail_tools import get_gmail_tools

    gmail_tools = get_gmail_tools()
    send_tool = next(
        (t for t in gmail_tools if t.name == "send_gmail_message"), None
    )

    if not send_tool:
        return "Gmail 未配置，无法发送邮件。请先完成 OAuth 授权。"

    try:
        result = send_tool.invoke({
            "to": to_email,
            "subject": subject,
            "message": body,
        })
        return f"邮件已成功发送给 {to_name}（{to_email}）！"
    except Exception as e:
        return f"邮件发送失败：{e}"


# ============================================================
# 2. IP 定位工具（Supervisor 调用，内置 interrupt 实现 HITL）
# ============================================================

@tool
def locate_user_by_ip(ip: str = "") -> str:
    """通过 IP 定位获取用户当前所在城市。
    调用前系统会请求用户确认（涉及位置隐私）。
    """
    # HITL：暂停执行，等用户在前端确认
    decision = interrupt({
        "type": "location_confirm",
        "message": "应用想要获取你的位置信息来确定出发地，是否允许？",
        "tool": "maps_ip_location",
        "ip": ip,
    })

    if isinstance(decision, dict) and decision.get("type") == "approve":
        # 用户同意 → 调用实际的高德 IP 定位 MCP 工具
        from app.agents.travel.mcp_client import _ip_location_tool
        return _ip_location_tool.invoke({"ip": ip})
    else:
        return "用户拒绝了位置获取。请直接询问用户的出发地。"


# ============================================================
# 3. 长期记忆工具（任何 agent 都可以调用）
# ============================================================

@tool
def save_user_preference(
    preference_key: str,
    preference_value: str,
    config: RunnableConfig,
    store: BaseStore,
) -> str:
    """保存用户偏好（如常用出发地、出行风格等）。"""
    user_id = config["configurable"].get("user_id", "default")
    namespace = ("user_preferences", user_id)

    store.put(
        namespace=namespace,
        key=preference_key,
        value={"data": preference_value},
    )
    return f"已保存偏好：{preference_key} = {preference_value}"


@tool
def get_user_preferences(
    config: RunnableConfig,
    store: BaseStore,
) -> str:
    """读取用户的所有偏好设置。"""
    user_id = config["configurable"].get("user_id", "default")
    namespace = ("user_preferences", user_id)

    items = store.search(namespace)
    if not items:
        return "暂无保存的偏好。"
    return "\n".join(f"- {item.key}: {item.value['data']}" for item in items)
```

> **为什么有两种 HITL 方式？**
> - `send_invite_email`：工具本身是正常的，HITL 由 agents.py 里的 `HumanInTheLoopMiddleware` 在外部拦截
> - `locate_user_by_ip`：工具内部直接调用 `interrupt()`，因为 Supervisor 自己的工具不走子 agent 的 middleware

---

### app/agents/travel/agents.py

包含：4 个子 agent 定义，Planner 带 HITL middleware。

```python
# app/agents/travel/agents.py
"""子 Agent 定义。每个 agent 专注一个领域。"""

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware

from app.agents.travel.prompts import (
    WEATHER_PROMPT,
    POI_PROMPT,
    ROUTE_PROMPT,
    PLANNER_PROMPT,
)


def create_weather_agent(tools: list):
    """天气调研 Agent（墨迹天气 MCP）。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="weather_expert",
        prompt=WEATHER_PROMPT,
    )


def create_poi_agent(tools: list):
    """景点调研 Agent（高德搜索 MCP）。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="poi_expert",
        prompt=POI_PROMPT,
    )


def create_route_agent(tools: list):
    """路线规划 Agent（高德路线 MCP）。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="route_expert",
        prompt=ROUTE_PROMPT,
    )


def create_planner_agent(tools: list):
    """行程规划 Agent。

    send_invite_email 被 HumanInTheLoopMiddleware 拦截，
    调用时会 interrupt → 前端弹窗让用户确认/拒绝。
    """
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="planner",
        prompt=PLANNER_PROMPT,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "send_invite_email": True,  # 发邮件前必须人工确认
                }
            ),
        ],
    )
```

> `create_agent` 是 LangChain 的 `langchain.agents.create_agent`，返回编译好的 Agent 图。
> `name` 很重要——Supervisor 用它生成 handoff 工具名（如 `transfer_to_weather_expert`）。

---

### app/agents/travel/supervisor.py

**只管编排 + 流式调用。** 持久化交给 `SessionManager`，SSE 序列化交给 `sse.py`。

```python
# app/agents/travel/supervisor.py
"""TravelSupervisor：用 create_supervisor 编排多个子 Agent。"""

from langgraph.types import Command
from langgraph_supervisor import create_supervisor

from app.agents.travel.agents import (
    create_weather_agent,
    create_poi_agent,
    create_route_agent,
    create_planner_agent,
)
from app.agents.travel.mcp_client import get_travel_tools
from app.agents.travel.prompts import SUPERVISOR_PROMPT
from app.agents.travel.tools import locate_user_by_ip, send_invite_email
from app.common.logger import logger
from app.common.sse import sse_event, sse_json_event, serialize
from app.models.session import session_manager


class TravelSupervisor:
    """出行企划 Supervisor。

    职责：
    - 编排子 Agent（weather / poi / route / planner）
    - 处理 SSE 流式输出
    其他所有持久化 / 会话管理逻辑在 SessionManager 里。
    """

    def __init__(self):
        self.graph = None

    async def init(self):
        """初始化：获取工具 → 创建子 Agent → 构建 Supervisor。"""
        logger.info("TravelSupervisor 初始化中...")

        # 1. 初始化会话管理（checkpointer + store）
        await session_manager.init()

        # 2. 获取 MCP 工具
        tools = await get_travel_tools()
        logger.info(
            f"MCP 工具: weather={len(tools['weather'])}, "
            f"poi={len(tools['poi'])}, route={len(tools['route'])}"
        )

        # 3. Supervisor 自己的工具
        maps_geo_tool = next(
            (t for t in tools["supervisor"] if t.name == "maps_geo"), None
        )
        supervisor_tools = [locate_user_by_ip]
        if maps_geo_tool:
            supervisor_tools.append(maps_geo_tool)

        # 4. 创建子 Agent
        weather_agent = create_weather_agent(tools["weather"])
        poi_agent = create_poi_agent(tools["poi"])
        route_agent = create_route_agent(tools["route"])
        planner_agent = create_planner_agent([send_invite_email])

        # 5. 创建 Supervisor
        workflow = create_supervisor(
            agents=[weather_agent, poi_agent, route_agent, planner_agent],
            model="deepseek-chat",
            prompt=SUPERVISOR_PROMPT,
            tools=supervisor_tools,
            parallel_tool_calls=True,
            output_mode="full_history",
        )

        # 6. 编译（checkpointer + store 都来自 session_manager）
        self.graph = workflow.compile(
            checkpointer=session_manager.checkpointer,
            store=session_manager.store,
        )
        logger.info("TravelSupervisor 初始化完成 ✓")

    async def close(self):
        await session_manager.close()

    # ================================================================
    # SSE 流式输出
    # ================================================================

    async def generate_sse(
        self,
        thread_id: str,
        message: str,
        interrupt_decision: dict | None = None,
    ):
        """处理用户请求，yield SSE 事件。"""
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": "default",      # 多用户时替换
            }
        }

        # HITL 恢复 or 正常消息
        if interrupt_decision:
            agent_input = Command(
                resume={"decisions": [interrupt_decision]}
            )
        else:
            agent_input = {
                "messages": [{"role": "user", "content": message}],
            }

        try:
            async for chunk in self.graph.astream(
                agent_input,
                config=config,
                stream_mode=["messages", "updates"],
            ):
                event_type = chunk["type"]
                data = chunk["data"]

                if event_type == "messages":
                    token, metadata = data
                    node = metadata.get("langgraph_node", "")
                    if (
                        node == "supervisor"
                        and hasattr(token, "content")
                        and token.content
                    ):
                        yield sse_event("message", token.content)

                elif event_type == "updates":
                    if "__interrupt__" in data:
                        for item in data["__interrupt__"]:
                            value = (
                                item.value
                                if hasattr(item, "value")
                                else item
                            )
                            yield sse_json_event("interrupt", {
                                "type": "interrupt",
                                "interrupt": serialize(value),
                            })

            yield sse_event("done", "")

        except Exception as exc:
            logger.error(f"SSE 流错误: {exc}", exc_info=True)
            yield sse_event("error", str(exc))


# 单例
travel_supervisor = TravelSupervisor()
```

> 对比旧架构：没有 `StateGraph`、没有 `add_node`/`add_edge`、没有 `Send()` fan-out、没有条件边函数。
> 对比上一版 supervisor.py：没有 checkpointer 初始化、没有 SSE 工具函数、没有 get_messages/clear_messages。
> **每个模块只干一件事。**

---

### app/api/v1/travel.py

```python
# app/api/v1/travel.py
"""出行企划 REST API。"""

from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.agents.travel.supervisor import travel_supervisor
from app.models.schemas import TravelRequest
from app.models.session import session_manager

router = APIRouter()


@router.post("/travel/send")
async def send_travel(request: TravelRequest):
    """SSE 流式对话接口。"""
    return EventSourceResponse(
        travel_supervisor.generate_sse(
            thread_id=request.thread_id,
            message=request.message,
            interrupt_decision=request.interrupt_decision,
        )
    )


@router.get("/travel/messages")
async def get_messages(thread_id: str):
    """获取会话历史（前端切换会话时调用）。"""
    return await session_manager.get_messages(
        graph=travel_supervisor.graph,
        thread_id=thread_id,
    )


@router.delete("/travel/messages")
async def clear_messages(thread_id: str):
    """清除会话历史（前端删除会话时调用）。"""
    await session_manager.clear_messages(thread_id)
    return {"status": "ok"}
```

> 注意 `get_messages` 和 `clear_messages` 直接调 `session_manager`，不经过 `travel_supervisor`。
> `TravelRequest` 从 `models/schemas.py` 导入，不在路由文件里定义。

---

### app/static/index.html（`<script>` 部分）

前端需要实现 3 个核心能力：SSE 流式接收、HITL 弹窗、多会话管理。

#### 多会话数据结构 + 管理

```javascript
// ---- 数据 ----
let sessions = JSON.parse(localStorage.getItem("travel_sessions") || "[]");
let activeSessionId = null;

// ---- 创建新会话 ----
function createSession() {
  const session = {
    id: `travel-${Date.now()}`,    // 用作后端的 thread_id
    title: "新会话",
    createdAt: Date.now(),
  };
  sessions.unshift(session);
  saveSessions();
  switchToSession(session.id);
}

function saveSessions() {
  localStorage.setItem("travel_sessions", JSON.stringify(sessions));
}

// ---- 切换会话（核心！从后端加载历史消息）----
async function switchToSession(sessionId) {
  activeSessionId = sessionId;
  clearChatArea();
  renderSessionList();
  await loadSessionMessages(sessionId);  // 从后端拉历史
}

// ---- 删除会话 ----
async function deleteSession(sessionId) {
  await fetch(`/api/v1/travel/messages?thread_id=${sessionId}`, {
    method: "DELETE",
  });
  sessions = sessions.filter(s => s.id !== sessionId);
  saveSessions();

  if (activeSessionId === sessionId) {
    sessions.length > 0 ? switchToSession(sessions[0].id) : createSession();
  }
  renderSessionList();
}
```

#### 从后端加载历史消息

```javascript
async function loadSessionMessages(sessionId) {
  try {
    const resp = await fetch(`/api/v1/travel/messages?thread_id=${sessionId}`);
    const { messages } = await resp.json();

    for (const msg of messages) {
      if (msg.role === "user") {
        appendUserBubble(msg.content);
      } else if (msg.role === "assistant") {
        appendAssistantBubble(msg.content);
      }
    }
    scrollToBottom();
  } catch (err) {
    console.error("加载历史消息失败:", err);
  }
}
```

#### 侧栏会话列表

```javascript
function renderSessionList() {
  const listEl = document.getElementById("session-list");
  listEl.innerHTML = "";

  for (const session of sessions) {
    const item = document.createElement("div");
    item.className = "session-item"
      + (session.id === activeSessionId ? " active" : "");
    item.innerHTML = `
      <span class="session-title">${session.title}</span>
      <span class="session-date">
        ${new Date(session.createdAt).toLocaleDateString()}
      </span>
      <button class="delete-btn"
        onclick="event.stopPropagation(); deleteSession('${session.id}')">×</button>
    `;
    item.onclick = () => switchToSession(session.id);
    listEl.appendChild(item);
  }
}
```

#### SSE 流式接收

```javascript
async function sendMessage(message, interruptDecision = null) {
  if (!activeSessionId) createSession();

  const response = await fetch("/api/v1/travel/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      thread_id: activeSessionId,
      interrupt_decision: interruptDecision,
    }),
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() || "";

    for (const part of parts) {
      const { event, data } = parseSseBlock(part);
      const payload = JSON.parse(data);

      switch (event) {
        case "message":
          appendToCurrentBubble(payload.content);
          break;
        case "interrupt":
          showInterruptDialog(payload.interrupt);
          break;
        case "done":
          finishLoading();
          break;
        case "error":
          showError(payload.content);
          break;
      }
    }
  }

  // 更新会话标题（用第一条消息的前 20 个字）
  if (message) {
    const session = sessions.find(s => s.id === activeSessionId);
    if (session && session.title === "新会话") {
      session.title = message.slice(0, 20) + (message.length > 20 ? "..." : "");
      saveSessions();
      renderSessionList();
    }
  }
}
```

#### HITL 中断弹窗（两种类型）

```javascript
function showInterruptDialog(interruptData) {
  const type = interruptData.type;

  if (type === "location_confirm") {
    // ---- IP 定位确认 ----
    showLocationConfirm(interruptData);
  } else {
    // ---- 邮件发送确认 ----
    showEmailConfirm(interruptData);
  }
}

function showLocationConfirm(data) {
  const panel = createInterruptPanel({
    title: "📍 位置权限请求",
    message: data.message,
    onApprove: () => sendMessage(null, { type: "approve" }),
    onReject: () => sendMessage(null, { type: "reject" }),
  });
  showPanel(panel);
}

function showEmailConfirm(data) {
  const panel = createInterruptPanel({
    title: "📧 邮件发送确认",
    message: `收件人：${data.to_name}（${data.to_email}）\n主题：${data.subject}`,
    detail: data.body,
    onApprove: () => sendMessage(null, { type: "approve" }),
    onReject: (reason) => sendMessage(null, { type: "reject", message: reason }),
    showRejectReason: true,
  });
  showPanel(panel);
}
```

#### 页面初始化

```javascript
window.addEventListener("DOMContentLoaded", () => {
  sessions = JSON.parse(localStorage.getItem("travel_sessions") || "[]");

  if (sessions.length > 0) {
    switchToSession(sessions[0].id);   // 恢复上次会话
  } else {
    createSession();                   // 首次使用，创建新会话
  }
  renderSessionList();
});
```

> **多会话完整数据流**：
> ```
> 用户点击侧栏 "西湖出行"
>   → switchToSession("travel-xxx")
>   → clearChatArea()
>   → GET /travel/messages?thread_id=travel-xxx
>   → 后端 graph.aget_state(config) 从 checkpointer 读取
>   → 返回 [{role: "user", ...}, {role: "assistant", ...}]
>   → 前端逐条渲染到聊天区域
>   → 用户继续发消息 → POST /travel/send（带 thread_id）
>   → checkpointer 自动续接对话上下文
> ```

---

### 两种 HITL 机制对比

| | IP 定位（Supervisor 层） | 邮件发送（Planner 层） |
|---|---|---|
| **工具** | `locate_user_by_ip` | `send_invite_email` |
| **HITL 方式** | 工具内部调用 `interrupt()` | `HumanInTheLoopMiddleware` 外部拦截 |
| **为什么不同** | Supervisor 的工具不走子 agent 的 middleware | 子 agent 的工具可以被 middleware 拦截 |
| **前端弹窗** | 简单的 允许/拒绝 | 展示邮件内容 + 允许/拒绝/修改 |

### 记忆系统总结

```
┌───────────────────────────────────────────────┐
│              用户发消息                          │
│                  │                              │
│    ┌─────────────┴─────────────┐                │
│    │                           │                │
│    ▼                           ▼                │
│  checkpointer               store               │
│  (短期记忆)                (长期记忆)             │
│                                                 │
│  • thread_id 隔离           • user_id 隔离       │
│  • 自动保存/恢复对话         • 手动 put / search   │
│  • 支持 interrupt 续传       • 跨会话持久化        │
│  • SQLite / PostgreSQL      • InMemory / PG      │
│                                                 │
│  场景：同一会话多轮对话      场景：用户偏好         │
│        HITL 中断恢复              历史行程         │
│        前端会话历史展示           常用出发地         │
└───────────────────────────────────────────────┘
```

### SSE 事件类型

| SSE 事件 | 说明 | 前端处理 |
|---------|------|---------|
| `message` | supervisor 的文字回复（逐 token） | 追加到聊天气泡 |
| `interrupt` | HITL 中断，包含工具调用详情 | 弹出确认弹窗 |
| `done` | 本轮处理完成 | 结束 loading |
| `error` | 发生错误 | 显示错误提示 |

> **为什么只输出 supervisor 节点的 token？** 子 agent 的内部对话会被 supervisor 综合后再呈现给用户。`output_mode="full_history"` 保证 supervisor 能看到所有子 agent 的回复，但前端只展示 supervisor 的最终输出。

---

## 附录：常见问题

### Q：parallel_tool_calls 真的会并发吗？

是的。当 supervisor 的 LLM 一次返回多个 tool call（比如同时调 weather_expert、poi_expert、route_expert），LangGraph 的 ToolNode 内部用 `asyncio.gather` 并发执行。前提是 LLM 支持 parallel tool calls（DeepSeek、GPT-4o、Claude 都支持）。

### Q：子 agent 的内部对话会暴露给用户吗？

取决于 `output_mode`：
- `"full_history"`：supervisor 能看到子 agent 的完整对话，但你可以在 SSE 层只输出 supervisor 节点的 token
- `"last_message"`：只保留子 agent 的最终回复

### Q：不用 Gmail 可以吗？

可以。`send_invite_email` 工具里的 Gmail 调用可以替换成任何邮件服务（SendGrid、SMTP 等），或者直接去掉邮件功能。

### Q：DeepSeek 可以换成其他模型吗？

可以。`create_agent` 和 `create_supervisor` 的 `model` 参数支持：
- 字符串：`"openai:gpt-4o"`、`"anthropic:claude-sonnet-4-20250514"`
- 模型实例：`ChatOpenAI(model="gpt-4o")`

### Q：checkpointer 可以换成 PostgreSQL 吗？

可以。生产环境推荐用 `AsyncPostgresSaver`：
```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
checkpointer = AsyncPostgresSaver(conn_string="postgresql://...")
```

### Q：如何调试 supervisor 的路由决策？

```python
# 编译时开启 debug
self.graph = workflow.compile(checkpointer=self.checkpointer, debug=True)
```

或者在 `generate_sse` 里打印 updates 事件，能看到 supervisor 调了哪些子 agent。

### Q：InMemoryStore 重启就丢了，怎么持久化？

开发阶段用 `InMemoryStore` 够了。生产环境换 `AsyncPostgresStore`：

```python
from langgraph.store.postgres import AsyncPostgresStore

store = await AsyncPostgresStore.from_conn_string("postgresql://user:pass@localhost/db")
```

接口完全一致，`put` / `search` / `get` 不用改。

### Q：checkpointer 和 store 有什么区别？

| | checkpointer | store |
|---|---|---|
| **自动 vs 手动** | 自动——graph 每走一步自动保存 | 手动——需要在代码里 `put` / `search` |
| **隔离维度** | `thread_id`（会话） | `namespace`（自定义，通常按 `user_id`） |
| **典型数据** | 消息历史、graph 状态、interrupt 断点 | 用户偏好、历史行程、常用地址 |
| **跨会话** | ❌ 每个 thread 独立 | ✅ 同一 user 的所有会话共享 |

### Q：多会话前端用 localStorage 够吗？

够——session 列表本身很轻量（只有 id + title + timestamp），实际的对话数据存在后端 checkpointer 里。前端只负责维护"哪些会话存在"以及"当前激活哪个"。切换会话时从后端拉历史消息即可。
