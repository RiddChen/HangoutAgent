# HangoutAgent - 出行企划助手

基于 LangChain 最新 Multi-Agent 架构（Subagents + Handoffs 混合模式）的智能出行规划系统。Supervisor 通过 tool calling 编排 7 个专家子 Agent，结合 State 驱动 + @dynamic_prompt 中间件实现流程控制，集成 interrupt 人机确认和 MCP 协议对接外部工具，自动完成天气查询、路线规划、周边推荐、火车/航班查询、住宿推荐和邮件发送的完整出行规划流程。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (SPA)                    │
│                  SSE 流式对话界面                      │
└──────────────────────┬──────────────────────────────┘
                       │ POST /api/v1/hangout/send (SSE)
┌──────────────────────▼──────────────────────────────┐
│                  FastAPI Backend                     │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│          Supervisor (create_agent + Middleware)      │
│  ┌─────────────────────────────────────────────┐    │
│  │  HangoutState (自定义 AgentState)            │    │
│  │  destination / date / origin / weather_ok   │    │
│  │  trip_type / preferences / plan_saved ...   │    │
│  └─────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────┐    │
│  │  @dynamic_prompt 中间件                      │    │
│  │  每轮注入状态 + 阶段提示 → 约束 LLM 行为      │    │
│  └─────────────────────────────────────────────┘    │
│  Command 工具: update_trip_info, mark_weather_result│
│  mark_trip_type, ask_weather_concern, save_final_plan│
│  子 Agent 工具: weather_expert, route_expert, ...   │
└──────────────┬──────────────────────────────────────┘
               │ tool calling (Subagents 模式)
   ┌───────────┼───────────┬───────────┬──────────┐
   ▼           ▼           ▼           ▼          ▼
┌─────┐ ┌─────────┐ ┌─────┐ ┌─────┐ ┌──────┐ ┌────┐
│天气  │ │路线/POI │ │火车  │ │航班  │ │住宿  │ │邮件│
│专家  │ │专家     │ │专家  │ │专家  │ │专家  │ │专家│
└──┬──┘ └────┬────┘ └──┬──┘ └──┬──┘ └──┬───┘ └─┬──┘
   ▼         ▼         ▼      ▼       ▼       ▼
┌─────┐  ┌──────┐  ┌──────────────────────┐ ┌─────┐
│墨迹  │  │高德   │  │   BigModel MCP       │ │Gmail│
│天气  │  │地图   │  │ 12306 / 航班 / 住宿   │ │API  │
│MCP  │  │MCP   │  │                      │ │     │
└─────┘  └──────┘  └──────────────────────┘ └─────┘
```

### 核心设计

- **Subagents 模式**：7 个专家子 Agent 通过 `create_agent` 创建，包装为 tool 供 Supervisor 通过 tool calling 调度
- **Handoffs 模式**：`HangoutState` + `Command(update={...})` 实现状态驱动的阶段流转，确保流程顺序（先天气 → 再路线 → 再方案）
- **@dynamic_prompt 中间件**：每轮模型调用前，自动注入当前出行状态和阶段提示到系统 Prompt，用代码约束 LLM 行为
- **Interrupt 机制**：天气确认、邮件发送等需要用户决策的节点通过 `interrupt()` 暂停，前端弹窗确认后恢复
- **MCP 协议集成**：通过 langchain-mcp-adapters 对接高德地图、墨迹天气、12306、航班等外部工具服务

## 技术栈

| 类别 | 技术 |
|------|------|
| LLM | DeepSeek Chat |
| Agent 框架 | LangChain (create_agent + @dynamic_prompt + Command) |
| MCP 工具 | langchain-mcp-adapters (高德地图 / 墨迹天气 / 12306 / 航班) |
| 后端 | FastAPI + SSE (sse-starlette) |
| 状态持久化 | Redis Stack (Checkpointer + Store) / SQLite (fallback) |
| 邮件 | Gmail API (OAuth2, langchain-google-community) |
| 前端 | 原生 HTML/JS SPA |
| 包管理 | uv |

## 项目结构

```
HangoutAgent/
├── app/
│   ├── main.py                  # FastAPI 入口 + lifespan
│   ├── agents/
│   │   └── hangout/
│   │       ├── supervisor.py    # Supervisor 编排 + SSE 流式输出
│   │       ├── agents.py        # 6 个子 Agent 定义
│   │       ├── tools.py         # HangoutState + Command 工具
│   │       ├── prompts.py       # 所有 Agent 的 System Prompt
│   │       └── mcp_client.py    # MCP 连接管理 + 工具分组
│   ├── api/v1/
│   │   └── hangout.py           # REST API 路由
│   ├── common/
│   │   ├── logger.py            # 日志
│   │   └── sse.py               # SSE 事件序列化
│   ├── integrations/
│   │   ├── gmail_auth.py        # Gmail OAuth 认证
│   │   └── gmail_tools.py       # Gmail 发送工具
│   ├── models/
│   │   ├── session.py           # Redis/SQLite 会话管理
│   │   ├── schemas.py           # Pydantic 模型
│   │   └── hangout.py           # 出行数据模型
│   └── static/
│       └── index.html           # 前端 SPA
├── test/                        # 测试
├── scripts/                     # 调试脚本
├── pyproject.toml               # 依赖定义
├── uv.lock                      # 依赖锁文件
└── .env                         # 环境变量 (需自行创建)
```

## 本地部署

### 前置要求

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) (包管理)
- Redis Stack (可选，不装则自动降级为 SQLite)
- Node.js (12306 MCP 需要 npx)

### 1. 克隆 & 安装依赖

```bash
git clone https://github.com/RiddChen/HangoutAgent.git
cd HangoutAgent
uv sync
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
# LLM (必需)
DEEPSEEK_API_KEY=your_deepseek_api_key

# 高德地图 MCP (必需)
AMAP_MAPS_API_KEY=your_amap_api_key

# 墨迹天气 MCP (推荐，不配则用高德天气)
WEATHER_MCP_TRANSPORT=http
WEATHER_MCP_API_KEY=your_bigmodel_api_key
WEATHER_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/moji-weather/mcp?Authorization=your_bigmodel_api_key

# 12306 火车票 MCP (可选)
TRAIN_MCP_TRANSPORT=stdio
TRAIN_MCP_COMMAND=npx
TRAIN_MCP_ARGS=-y 12306-mcp

# 航班 MCP (可选)
FLIGHT_MCP_TRANSPORT=http
FLIGHT_MCP_API_KEY=your_bigmodel_api_key
FLIGHT_MCP_URL=https://open.bigmodel.cn/api/mcp-broker/proxy/aviation/mcp?Authorization=your_bigmodel_api_key

# LangSmith 追踪 (可选)
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=hangout-agent
```

**API Key 获取方式：**

| Key | 获取地址 |
|-----|---------|
| DeepSeek | https://platform.deepseek.com |
| 高德地图 | https://console.amap.com |
| BigModel (天气/航班) | https://open.bigmodel.cn |

### 3. 启动 Redis (可选)

```bash
# macOS
brew install redis-stack
redis-stack-server

# 或 Docker
docker run -d -p 6379:6379 redis/redis-stack-server:latest
```

不启动 Redis 也能运行，系统会自动降级到 SQLite，但会话持久化能力有限。

### 4. 启动服务

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

打开浏览器访问 http://127.0.0.1:8002

### 5. Gmail 邮件功能 (可选)

如果需要邮件发送功能：

1. 在 [Google Cloud Console](https://console.cloud.google.com) 创建 OAuth 2.0 凭据
2. 将下载的凭据保存为项目根目录的 `credentials.json`
3. 首次发送邮件时会自动引导 OAuth 授权，生成 `token.json`

## 对话流程

```
用户提供目的地+日期
       │
       ▼
  查询天气 ──→ 天气不好 ──→ 询问用户是否在意
       │                         │
       ▼                    在意：换时间
  天气通过                  不在意：继续
       │
       ▼
  获取出发地 → 判断同城/跨城
       │                │
    同城               跨城
       │                │
       ▼                ▼
  问偏好           查火车+航班
  (交通/餐饮/周边)      │
       │                ▼
       ▼           问是否住宿
  路线+POI             │
       │                ▼
       ▼            查住宿
  生成完整计划
       │
       ▼
  用户确认 → 保存方案 → 发送邮件 (interrupt 确认)
```

## License

MIT
