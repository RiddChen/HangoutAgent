# HangoutAgent - 出行企划助手

基于 LangChain 最新 Multi-Agent 架构的智能出行规划系统。采用 **Subagents + Handoffs 混合模式**，Orchestrator 通过 tool calling 编排 7 个专家子 Agent，结合自定义 State + `@dynamic_prompt` 中间件 + `Command` 状态更新 + `interrupt` 人机确认，实现从天气查询到邮件发送的完整出行规划闭环。

## 系统架构

### 整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Frontend (原生 HTML/JS SPA)                    │
│                      SSE 流式对话 + interrupt 弹窗确认                  │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ POST /api/v1/hangout/send (SSE)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend                                │
│                   lifespan 初始化 MCP + Agent                         │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────────┐
│                                                                      │
│                    Orchestrator Agent (create_agent)                    │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                  @dynamic_prompt 中间件                         │  │
│  │  每轮模型调用前自动注入：                                         │  │
│  │  · 当前日期                                                     │  │
│  │  · 已收集的出行信息（目的地/日期/出发地/天气/偏好...）              │  │
│  │  · 阶段提示（"必须先查天气" / "必须询问用户是否在意"）             │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                  HangoutState (自定义 AgentState)               │  │
│  │                                                                │  │
│  │  destination    date          origin         weather_checked   │  │
│  │  weather_ok     weather_summary              trip_type         │  │
│  │  transport_preference  dining_preference     nearby_preference │  │
│  │  hotel_needed   plan_saved                                     │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────── Tools ─────────────────────────────────────────┐  │
│  │                                                                │  │
│  │  Command 工具 (Handoffs 模式，返回 Command 更新 State)          │  │
│  │  ┌──────────────┐ ┌──────────────────┐ ┌────────────────┐     │  │
│  │  │update_trip_  │ │mark_weather_     │ │mark_trip_type  │     │  │
│  │  │info          │ │result            │ │                │     │  │
│  │  └──────────────┘ └──────────────────┘ └────────────────┘     │  │
│  │  ┌──────────────┐ ┌──────────────────┐ ┌────────────────┐     │  │
│  │  │ask_weather_  │ │save_final_plan   │ │maps_geo /      │     │  │
│  │  │concern       │ │                  │ │maps_distance   │     │  │
│  │  │(interrupt)   │ │                  │ │                │     │  │
│  │  └──────────────┘ └──────────────────┘ └────────────────┘     │  │
│  │                                                                │  │
│  │  子 Agent 工具 (Subagents 模式，子 Agent 包装为 tool)           │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐            │  │
│  │  │weather_ │ │route_   │ │poi_     │ │email_   │            │  │
│  │  │expert   │ │expert   │ │expert   │ │expert   │            │  │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘            │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐                        │  │
│  │  │train_   │ │flight_  │ │hotel_   │                        │  │
│  │  │expert   │ │expert   │ │expert   │                        │  │
│  │  └────┬────┘ └────┬────┘ └────┬────┘                        │  │
│  └───────┼──────────┼──────────┼────────────────────────────────┘  │
└──────────┼──────────┼──────────┼────────────────────────────────────┘
           │          │          │
           ▼          ▼          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        外部工具 (MCP 协议)                            │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ 墨迹天气  │  │ 高德地图  │  │  12306   │  │ 航班查询  │            │
│  │ MCP      │  │ MCP      │  │  MCP     │  │ MCP      │            │
│  │ (天气/   │  │ (地理编码/│  │ (火车票  │  │ (航班    │            │
│  │  预报/   │  │  路线规划/│  │  查询)   │  │  查询)   │            │
│  │  空气)   │  │  POI搜索)│  │          │  │          │            │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │
│                                                                      │
│  ┌──────────┐                                                        │
│  │ Gmail    │                                                        │
│  │ API      │                                                        │
│  │ (OAuth2) │                                                        │
│  └──────────┘                                                        │
└──────────────────────────────────────────────────────────────────────┘
```

### LangGraph 图结构

迁移到 `create_agent` 后，图结构简洁清晰：

```
         ┌───────────┐
         │  __start__ │
         └─────┬─────┘
               ▼
         ┌───────────┐
    ┌───▶│   model   │───┐
    │    │(Orchestrator)│   │
    │    └───────────┘   │
    │          │          │
    │          ▼          ▼
    │    ┌───────────┐  ┌─────────┐
    │    │   tools   │  │ __end__ │
    │    │(子Agent + │  └─────────┘
    │    │ Command)  │
    │    └─────┬─────┘
    │          │
    └──────────┘
```

- **model 节点**：Orchestrator LLM，负责对话、路由决策、结果汇总
- **tools 节点**：执行所有工具调用——子 Agent（weather_expert 等）和 Command 工具（update_trip_info 等）
- **循环**：model → tools → model，直到 Orchestrator 决定不再调用工具，输出最终回复

### 核心设计

**Subagents 模式（子 Agent 调度）**

每个子 Agent 通过 `create_agent` 独立创建，拥有专属 Prompt 和 MCP 工具集，然后被包装为 Orchestrator 的 tool。Orchestrator 通过 tool calling 决定何时调用哪个子 Agent，子 Agent 独立执行后将结果返回给 Orchestrator 汇总。

```python
# 子 Agent 创建
weather_agent = create_agent(model, tools=[...], system_prompt=WEATHER_PROMPT)

# 包装为 Orchestrator 的 tool
@tool("weather_expert", description="查询目的地天气")
async def call_weather(request: str) -> str:
    result = await weather_agent.ainvoke({"messages": [...]})
    return result["messages"][-1].content
```

**Handoffs 模式（状态驱动流转）**

`HangoutState` 扩展了 `AgentState`，增加 12 个出行相关字段。Command 工具返回 `Command(update={...})` 原子更新 State，驱动流程阶段切换。

```python
@tool
def mark_weather_result(...) -> Command:
    return Command(update={
        "weather_checked": True,
        "weather_ok": True,
        "weather_summary": "晴，26°C，适合出行",
    })
```

**@dynamic_prompt 中间件**

每轮模型调用前，中间件读取 `HangoutState`，将已收集的信息和阶段约束动态注入系统 Prompt。用代码硬约束 LLM 行为（"目的地和日期齐全时必须先查天气"），避免 LLM 跳步。

**interrupt 人机确认**

天气不理想时暂停询问用户是否在意；邮件发送前弹窗让用户确认收件人和内容。前端通过 SSE 接收 interrupt 事件，弹窗后将用户决策回传。

## 对话流程

```
用户提供目的地 + 日期
        │
        ▼
  ┌─────────────┐
  │ update_trip_ │──→ 保存到 HangoutState
  │ info         │
  └──────┬──────┘
         ▼
  ┌─────────────┐     ┌───────────────────┐
  │  weather_   │────▶│ mark_weather_     │
  │  expert     │     │ result            │
  └──────┬──────┘     └────────┬──────────┘
         │                     │
         ▼                     ▼
     天气 OK？─── No ──▶ ask_weather_concern
         │                (interrupt 暂停)
         │ Yes                 │
         ▼               ┌────┴────┐
  获取出发地              │         │
         │           不在意      在意
         ▼           继续       换时间
  ┌─────────────┐
  │ maps_distance│──→ mark_trip_type
  └──────┬──────┘
         │
    ┌────┴────┐
    │         │
  同城      跨城
    │         │
    ▼         ▼
  问偏好    train_expert + flight_expert
    │         │
    ▼         ▼
  route_    问是否住宿 → hotel_expert
  expert      │
    +         │
  poi_expert  │
    │         │
    ▼         ▼
  ┌───────────────────────┐
  │    生成完整出行计划     │
  │  (天气+交通+餐饮+景点)  │
  └───────────┬───────────┘
              ▼
  用户确认满意？─── No ──▶ 修改后再次审核
              │
              │ Yes
              ▼
  ┌─────────────────┐
  │ save_final_plan │
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │  email_expert   │──→ interrupt 弹窗确认
  └────────┬────────┘
           ▼
     邮件发送完成
```

## 技术栈

| 类别 | 技术 |
|------|------|
| LLM | DeepSeek Chat |
| Agent 框架 | LangChain `create_agent` + `@dynamic_prompt` 中间件 + `Command` 状态更新 |
| Multi-Agent 模式 | Subagents（子 Agent 包装为 tool） + Handoffs（State 驱动阶段流转） |
| 外部工具协议 | MCP (Model Context Protocol)，通过 langchain-mcp-adapters 接入 |
| MCP 服务 | 高德地图（地理编码/路线/POI）、墨迹天气、12306 火车票、航班查询 |
| 人机交互 | `interrupt()` 暂停 + 前端弹窗确认 |
| 后端 | FastAPI + SSE 流式输出 (sse-starlette) |
| 状态持久化 | Redis Stack (Checkpointer + Store) / SQLite (fallback) |
| 邮件 | Gmail API (OAuth2, langchain-google-community) |
| 前端 | 原生 HTML/JS SPA |
| 包管理 | uv |

## 项目结构

```
HangoutAgent/
├── app/
│   ├── main.py                    # FastAPI 入口 + lifespan（初始化 MCP + Agent）
│   ├── agents/
│   │   └── hangout/
│   │       ├── orchestrator.py      # Orchestrator Agent 编排 + SSE 流式输出
│   │       │                      #   · create_agent + @dynamic_prompt 中间件
│   │       │                      #   · 子 Agent 包装为 tool（Subagents 模式）
│   │       │                      #   · 流式事件处理 + interrupt 转发
│   │       ├── agents.py          # 7 个子 Agent 定义（create_agent + 专属 Prompt）
│   │       ├── tools.py           # HangoutState 定义 + Command 工具
│   │       │                      #   · update_trip_info / mark_weather_result
│   │       │                      #   · ask_weather_concern (interrupt)
│   │       │                      #   · save_final_plan / send_final_plan_email
│   │       ├── prompts.py         # 所有 Agent 的 System Prompt
│   │       └── mcp_client.py      # MCP 连接管理 + 工具分组
│   │                              #   · 高德/墨迹/12306/航班/住宿 MCP
│   ├── api/v1/
│   │   └── hangout.py             # REST API 路由（SSE 流式 + 消息查询）
│   ├── common/
│   │   ├── logger.py              # 日志配置
│   │   └── sse.py                 # SSE 事件序列化
│   ├── integrations/
│   │   ├── gmail_auth.py          # Gmail OAuth2 认证流程
│   │   └── gmail_tools.py         # Gmail 发送工具封装
│   ├── models/
│   │   ├── session.py             # Redis / SQLite 会话管理
│   │   ├── schemas.py             # Pydantic 请求/响应模型
│   │   └── hangout.py             # 出行数据模型
│   └── static/
│       └── index.html             # 前端 SPA（SSE 对话 + interrupt 弹窗）
├── pyproject.toml                 # 依赖定义（uv + hatchling）
├── uv.lock                        # 依赖锁文件
└── .env                           # 环境变量（需自行创建）
```

## 本地部署

### 前置要求

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)（包管理）
- Redis Stack（可选，不装则自动降级为 SQLite）
- Node.js（12306 MCP 需要 npx）

### 1. 克隆 & 安装依赖

```bash
git clone https://github.com/RiddChen/HangoutAgent.git
cd HangoutAgent
uv sync
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
# LLM（必需）
DEEPSEEK_API_KEY=your_deepseek_api_key

# 高德地图 MCP（必需）
AMAP_MAPS_API_KEY=your_amap_api_key

# 墨迹天气 MCP（推荐，不配则用高德天气）
WEATHER_MCP_TRANSPORT=http
WEATHER_MCP_API_KEY=your_bigmodel_api_key
WEATHER_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/moji-weather/mcp?Authorization=your_bigmodel_api_key

# 12306 火车票 MCP（可选）
TRAIN_MCP_TRANSPORT=stdio
TRAIN_MCP_COMMAND=npx
TRAIN_MCP_ARGS=-y 12306-mcp

# 航班 MCP（可选）
FLIGHT_MCP_TRANSPORT=http
FLIGHT_MCP_API_KEY=your_bigmodel_api_key
FLIGHT_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/aviation/mcp?Authorization=your_bigmodel_api_key

# LangSmith 追踪（可选）
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=hangout-agent
```

**API Key 获取方式：**

| Key | 获取地址 |
|-----|---------|
| DeepSeek | https://platform.deepseek.com |
| 高德地图 | https://console.amap.com |
| BigModel（天气/航班）| https://open.bigmodel.cn |

### 3. 启动 Redis（可选）

```bash
# macOS
brew install redis-stack
redis-stack-server

# 或 Docker
docker run -d -p 6379:6379 redis/redis-stack-server:latest
```

不启动 Redis 也能运行，系统会自动降级到 SQLite + InMemoryStore。

### 4. 启动服务

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

打开浏览器访问 http://127.0.0.1:8002

### 5. Gmail 邮件功能（可选）

1. 在 [Google Cloud Console](https://console.cloud.google.com) 创建 OAuth 2.0 凭据
2. 将下载的凭据保存为项目根目录的 `credentials.json`
3. 首次发送邮件时会自动引导 OAuth 授权，生成 `token.json`

## License

MIT
