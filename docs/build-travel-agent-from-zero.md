# 从零构建出行企划 Multi-Agent 系统

> 基于 LangChain + LangGraph Supervisor 模式，使用 `create_supervisor` + `create_agent` 构建，不写一行自定义 StateGraph。

## 目录

1. [项目概览](#1-项目概览)
2. [架构设计](#2-架构设计)
3. [环境准备](#3-环境准备)
4. [Step 1：项目骨架](#step-1项目骨架)
5. [Step 2：MCP 工具接入](#step-2mcp-工具接入)
6. [Step 3：创建子 Agent](#step-3创建子-agent)
7. [Step 4：Supervisor 编排](#step-4supervisor-编排)
8. [Step 5：人工介入（HITL）](#step-5人工介入hitl)
9. [Step 6：SSE 流式输出](#step-6sse-流式输出)
10. [Step 7：FastAPI 接入](#step-7fastapi-接入)
11. [Step 8：前端对接](#step-8前端对接)
12. [附录：常见问题](#附录常见问题)

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
│       ├── supervisor.py       # TravelSupervisor 类（核心）
│       ├── agents.py           # 4 个子 agent 定义
│       ├── tools.py            # 自定义 tools（send_invite_email 等）
│       ├── prompts.py          # 所有 prompt
│       └── mcp_client.py       # MCP 工具获取
├── api/v1/
│   └── travel.py               # REST API
├── integrations/
│   ├── gmail_auth.py           # Gmail OAuth
│   └── gmail_tools.py          # Gmail 工具
├── common/
│   └── logger.py
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

## Step 3：创建子 Agent

每个子 agent 用 `create_agent` 创建，只需要指定模型、工具和 prompt。

### app/agents/travel/prompts.py

```python
SUPERVISOR_PROMPT = """你是 CityBuddy 出行企划的总协调者。

## 你的职责
1. 和用户聊天，收集出行信息（目的地、日期）
2. 用 maps_ip_location 自动获取用户当前位置作为出发地（系统会请求用户确认）
3. 信息收集齐后，**同时**派出天气、景点、路线三个专家去调研
4. 三个专家都回来后，派 planner 生成出行方案
5. 如果用户要约朋友，让 planner 处理邮件邀请

## 出发地规则
- 不要主动问用户出发地，先调用 maps_ip_location 自动定位
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

### app/agents/travel/agents.py

```python
"""子 Agent 定义。每个 agent 专注一个领域。"""

from langchain.agents import create_agent
from app.agents.travel.prompts import (
    WEATHER_PROMPT, POI_PROMPT, ROUTE_PROMPT, PLANNER_PROMPT,
)


def create_weather_agent(tools: list):
    """天气调研 Agent。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="weather_expert",
        prompt=WEATHER_PROMPT,
    )


def create_poi_agent(tools: list):
    """景点调研 Agent。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="poi_expert",
        prompt=POI_PROMPT,
    )


def create_route_agent(tools: list):
    """路线规划 Agent。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="route_expert",
        prompt=ROUTE_PROMPT,
    )


def create_planner_agent(tools: list):
    """行程规划 Agent（带邮件邀请工具）。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="planner",
        prompt=PLANNER_PROMPT,
    )
```

> **注意**：这里的 `create_agent` 就是 LangChain 的 `langchain.agents.create_agent`，它返回一个编译好的 Agent 图。`name` 参数很重要——supervisor 用它来生成 handoff 工具名。

---

## Step 4：Supervisor 编排

这是整个项目的核心——用 `create_supervisor` 把 4 个子 agent 编排起来。

### app/agents/travel/supervisor.py

```python
"""TravelSupervisor：用 create_supervisor 编排多个子 Agent。"""

import json
import os

import aiosqlite
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
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
from app.agents.travel.tools import send_invite_email
from app.common.logger import logger


class TravelSupervisor:
    """出行企划 Supervisor。

    用法和 EmailAgent 一样：
    - init()：启动时调用
    - generate_sse()：处理请求，返回 SSE 流
    - close()：关闭资源
    """

    def __init__(self):
        self.graph = None
        self.conn = None
        self.checkpointer = None

    async def init(self):
        """初始化：获取工具 → 创建子 Agent → 构建 Supervisor。"""
        logger.info("TravelSupervisor 初始化中...")

        # 1. 持久化
        await self._init_checkpointer()

        # 2. 获取 MCP 工具
        tools = await get_travel_tools()
        logger.info(
            f"MCP 工具: weather={len(tools['weather'])}, "
            f"poi={len(tools['poi'])}, route={len(tools['route'])}"
        )

        # 3. 创建子 Agent
        weather_agent = create_weather_agent(tools["weather"])
        poi_agent = create_poi_agent(tools["poi"])
        route_agent = create_route_agent(tools["route"])
        planner_agent = create_planner_agent([send_invite_email])

        # 4. 创建 Supervisor（核心！）
        #    - tools: supervisor 自己的工具（IP定位、地理编码）
        #    - agents: 4 个子 agent，supervisor 自动生成 handoff 工具
        #    - parallel_tool_calls: 允许一次调多个子 agent，自动并发
        workflow = create_supervisor(
            agents=[weather_agent, poi_agent, route_agent, planner_agent],
            model="deepseek-chat",
            prompt=SUPERVISOR_PROMPT,
            tools=tools["supervisor"],  # maps_ip_location, maps_geo
            parallel_tool_calls=True,   # 允许并发调多个子 agent
            output_mode="full_history", # 保留完整对话历史
        )

        # 5. 编译（加 checkpointer 实现会话持久化）
        self.graph = workflow.compile(checkpointer=self.checkpointer)
        logger.info("TravelSupervisor 初始化完成 ✓")

    async def _init_checkpointer(self):
        db_path = os.path.join(os.path.dirname(__file__), "../../db/travel.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = await aiosqlite.connect(db_path)
        self.checkpointer = AsyncSqliteSaver(conn=self.conn)
        await self.checkpointer.setup()

    async def close(self):
        if self.conn:
            await self.conn.close()

    # --- SSE 流式输出（见 Step 6）---
    # --- 消息历史（见 Step 7）---


# 单例
travel_supervisor = TravelSupervisor()
```

> **就这么简单。** 对比旧架构：
> - 没有 `StateGraph`、`add_node`、`add_edge`
> - 没有 `Send()` fan-out / fan-in
> - 没有条件边函数
> - 没有 `entry_node`、`route_entry`、`should_research`、`after_planner`
> - `create_supervisor` 一行搞定路由 + 并发 + handoff

---

## Step 5：人工介入（HITL）

本项目有 **两个人工介入点**，都通过 `HumanInTheLoopMiddleware` 实现。

### HITL 1：IP 定位确认（Supervisor 层）

```
用户："我想去西湖玩"
      │
      ▼
Supervisor 准备调用 maps_ip_location 获取用户位置
      │
      ▼
HumanInTheLoopMiddleware 拦截！（隐私敏感操作）
      │
      ▼
interrupt() 暂停 → 前端弹窗："是否允许获取你的位置？"
      │
      ├── ✅ 确认 → 执行 IP 定位 → 自动填入出发地
      └── ❌ 拒绝 → Supervisor 改为直接问用户出发地
```

### HITL 2：邮件发送确认（Planner 层）

```
用户："帮我发邮件给乔治 xxx@gmail.com"
      │
      ▼
Planner 调用 send_invite_email 工具
      │
      ▼
HumanInTheLoopMiddleware 拦截！（不可撤回操作）
      │
      ▼
interrupt() 暂停 → 前端弹窗展示：收件人、主题、正文
      │
      ├── ✅ 确认 → 执行工具 → Gmail API 发送
      ├── ✏️ 修改 → 改参数后执行
      └── ❌ 拒绝 → 取消发送，返回反馈给 Planner
```

### app/agents/travel/tools.py

```python
"""自定义工具：邮件邀请（带 HITL 确认）。"""

from langchain_core.tools import tool


@tool
def send_invite_email(
    to_name: str,
    to_email: str,
    subject: str,
    body: str,
) -> str:
    """发送出行邀请邮件。系统会在发送前要求用户确认。

    参数：
    - to_name: 收件人名字
    - to_email: 收件人邮箱
    - subject: 邮件主题
    - body: 邮件正文
    """
    from app.integrations.gmail_tools import get_gmail_tools

    # 找到 Gmail 发送工具
    gmail_tools = get_gmail_tools()
    send_tool = next((t for t in gmail_tools if t.name == "send_gmail_message"), None)

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
```

### 在两个地方启用 HITL

修改 `agents.py`：

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware


def create_planner_agent(tools: list):
    """行程规划 Agent（send_invite_email 需要人工确认）。"""
    return create_agent(
        "deepseek-chat",
        tools=tools,
        name="planner",
        prompt=PLANNER_PROMPT,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "send_invite_email": True,  # 发邮件前必须确认
                }
            ),
        ],
    )
```

修改 `supervisor.py` 中的 `create_supervisor` 调用：

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware

workflow = create_supervisor(
    agents=[weather_agent, poi_agent, route_agent, planner_agent],
    model="deepseek-chat",
    prompt=SUPERVISOR_PROMPT,
    tools=tools["supervisor"],      # maps_ip_location, maps_geo
    parallel_tool_calls=True,
    output_mode="full_history",
    # Supervisor 自身的 HITL：IP 定位需要确认
    # 注意：这里通过 post_model_hook 实现，
    # 因为 create_supervisor 的 tools 不走子 agent 的 middleware
)
```

> **重要**：Supervisor 自身的工具（`maps_ip_location`）的 HITL 和子 agent 的 HITL 机制不同：
> - **子 agent 的 HITL**：通过 `HumanInTheLoopMiddleware` 在 `create_agent` 上配置
> - **Supervisor 的 HITL**：Supervisor 调用的 tools 由 `create_supervisor` 内部的 ToolNode 执行。需要通过 `interrupt_before=["tools"]` 或封装工具来实现

更简洁的做法——把 `maps_ip_location` 包装成带 interrupt 的工具：

```python
# tools.py 中添加

from langgraph.types import interrupt


@tool
def locate_user_by_ip(ip: str = "") -> str:
    """通过 IP 定位获取用户当前所在城市。
    调用前系统会请求用户确认（涉及位置隐私）。
    """
    # HITL：暂停执行，等用户确认
    decision = interrupt({
        "type": "location_confirm",
        "message": "应用想要获取你的位置信息来确定出发地，是否允许？",
        "tool": "maps_ip_location",
        "ip": ip,
    })

    if isinstance(decision, dict) and decision.get("type") == "approve":
        # 用户同意 → 调用实际的高德 IP 定位
        from app.agents.travel.mcp_client import _ip_location_tool
        return _ip_location_tool.invoke({"ip": ip})
    else:
        return "用户拒绝了位置获取。请直接询问用户的出发地。"
```

这样 Supervisor 的工具列表变成：

```python
workflow = create_supervisor(
    agents=[weather_agent, poi_agent, route_agent, planner_agent],
    model="deepseek-chat",
    prompt=SUPERVISOR_PROMPT,
    tools=[locate_user_by_ip, maps_geo_tool],  # 包装后的定位 + 原始地理编码
    parallel_tool_calls=True,
    output_mode="full_history",
)
```

### 4 种用户决策

| 决策 | 说明 | 适用场景 |
|------|------|---------|
| `approve` | 确认执行 | 允许定位 / 确认发邮件 |
| `reject` | 拒绝并给反馈 | 拒绝定位（改为手动输入）/ 取消邮件 |
| `edit` | 修改参数后执行 | 修改邮件主题或正文 |
| `respond` | 直接回复 | 不常用，用于"ask user"场景 |

### 前端处理两种 interrupt

```javascript
// interrupt 事件处理
if (event === "interrupt") {
  const data = payload.interrupt;

  if (data.type === "location_confirm") {
    // IP 定位确认弹窗
    showLocationConfirmDialog(data.message);
  } else {
    // 邮件发送确认弹窗（从 action_requests 提取邮件详情）
    showEmailConfirmDialog(data);
  }
}
```

---

## Step 6：SSE 流式输出

在 `TravelSupervisor` 类中添加 `generate_sse` 方法：

```python
# supervisor.py 中添加

    async def generate_sse(
        self,
        thread_id: str,
        message: str,
        interrupt_decision: dict | None = None,
    ):
        """处理用户请求，返回 SSE 事件流。"""
        config = {"configurable": {"thread_id": thread_id}}

        # --- 处理 HITL 恢复 ---
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

                # --- 逐 token 流式输出 ---
                if event_type == "messages":
                    token, metadata = data
                    # 只输出 supervisor 的回复（不输出子 agent 内部对话）
                    node = metadata.get("langgraph_node", "")
                    if node == "supervisor" and hasattr(token, "content") and token.content:
                        yield _sse("message", token.content)

                # --- 节点完成 / interrupt 事件 ---
                elif event_type == "updates":
                    if "__interrupt__" in data:
                        # HITL 中断：提取工具调用详情发给前端
                        for item in data["__interrupt__"]:
                            value = item.value if hasattr(item, "value") else item
                            yield _sse_json("interrupt", {
                                "type": "interrupt",
                                "interrupt": _serialize(value),
                            })

            yield _sse("done", "")

        except Exception as exc:
            logger.error(f"SSE 流错误: {exc}", exc_info=True)
            yield _sse("error", str(exc))

    async def get_messages(self, thread_id: str) -> dict:
        """获取会话历史。"""
        if not self.graph:
            return {"messages": []}
        config = {"configurable": {"thread_id": thread_id}}
        state = await self.graph.aget_state(config)
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
        """清除会话历史。"""
        if self.checkpointer:
            await self.checkpointer.adelete_thread(thread_id)


# --- SSE 辅助函数 ---

def _sse(event_type: str, content: str) -> dict:
    return {
        "event": event_type,
        "data": json.dumps(
            {"type": event_type, "content": content},
            ensure_ascii=False,
        ),
    }

def _sse_json(event_type: str, payload: dict) -> dict:
    return {
        "event": event_type,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }

def _serialize(obj):
    """将 LangGraph 对象转为可 JSON 序列化的格式。"""
    if hasattr(obj, "value"):
        return _serialize(obj.value)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _serialize(value) for key, value in obj.items()}
    return obj
```

### 流式输出说明

| SSE 事件 | 说明 | 前端处理 |
|---------|------|---------|
| `message` | supervisor 的文字回复（逐 token） | 追加到聊天气泡 |
| `interrupt` | HITL 中断，包含工具调用详情 | 弹出确认弹窗 |
| `done` | 本轮处理完成 | 结束 loading |
| `error` | 发生错误 | 显示错误提示 |

> **为什么只输出 supervisor 节点的 token？** Supervisor 模式下，子 agent 的输出会被 supervisor 综合后再呈现给用户。直接输出子 agent 的内部对话会很混乱。`output_mode="full_history"` 保证 supervisor 能看到所有子 agent 的回复。

---

## Step 7：FastAPI 接入

### app/api/v1/travel.py

```python
from typing import Any, Dict, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.travel.supervisor import travel_supervisor

router = APIRouter()


class TravelRequest(BaseModel):
    message: str = ""
    thread_id: str = "default"
    interrupt_decision: Optional[Dict[str, Any]] = None


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
    """获取会话历史。"""
    return await travel_supervisor.get_messages(thread_id)


@router.delete("/travel/messages")
async def clear_messages(thread_id: str):
    """清除会话历史。"""
    await travel_supervisor.clear_messages(thread_id)
    return {"status": "ok"}
```

---

## Step 8：前端对接

前端通过 SSE 接收流式响应，核心是处理 4 种事件：

### SSE 解析核心逻辑

```javascript
const response = await fetch("/api/v1/travel/send", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message, thread_id, interrupt_decision }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  // SSE 以双换行分隔
  const parts = buffer.split(/\r?\n\r?\n/);
  buffer = parts.pop() || "";

  for (const part of parts) {
    const { event, data } = parseSseBlock(part);
    const payload = JSON.parse(data);

    switch (event) {
      case "message":
        // 追加到聊天气泡（流式）
        appendToCurrentBubble(payload.content);
        break;

      case "interrupt":
        // 弹出 HITL 确认弹窗
        showEmailConfirmDialog(payload.interrupt);
        break;

      case "done":
        // 结束 loading 状态
        finishLoading();
        break;

      case "error":
        // 显示错误
        showError(payload.content);
        break;
    }
  }
}
```

### HITL 确认弹窗

```javascript
function showEmailConfirmDialog(interruptData) {
  // interruptData 包含 action_requests，其中有工具调用参数
  // 从中提取 to_email, subject, body 展示给用户

  // 用户确认
  confirmButton.onclick = () => {
    sendToAgent({
      interrupt_decision: { type: "approve" }
    });
  };

  // 用户拒绝
  rejectButton.onclick = () => {
    sendToAgent({
      interrupt_decision: {
        type: "reject",
        message: rejectReasonInput.value,
      }
    });
  };
}
```

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
