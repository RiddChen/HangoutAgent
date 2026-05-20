# HangoutAgent - 出行企划助手

基于 LangGraph Multi-Agent 架构的智能出行规划系统。通过 Supervisor 编排多个专家 Agent，自动完成天气查询、路线规划、周边推荐、火车/航班查询、住宿推荐和邮件发送的完整出行规划流程。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (SPA)                    │
│                  SSE 流式对话界面                      │
└──────────────────────┬──────────────────────────────┘
                       │ POST /api/v1/travel/send (SSE)
┌──────────────────────▼──────────────────────────────┐
│                  FastAPI Backend                     │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               TravelSupervisor                      │
│  ┌─────────────────────────────────────────────┐    │
│  │  TravelState (LangGraph State)              │    │
│  │  destination / date / origin / weather_ok   │    │
│  │  trip_type / preferences / plan_saved ...   │    │
│  └─────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────┐    │
│  │  Dynamic Prompt (状态注入 + 阶段提示)         │    │
│  │  代码驱动流程控制 + Prompt 驱动灵活对话        │    │
│  └─────────────────────────────────────────────┘    │
│  Tools: update_trip_info, mark_weather_result,      │
│         mark_trip_type, ask_weather_concern,         │
│         save_final_plan, maps_geo, maps_distance     │
└──┬───────┬────────┬────────┬───────┬───────┬────────┘
   │       │        │        │       │       │
   ▼       ▼        ▼        ▼       ▼       ▼
┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐
│天气  │ │路线  │ │POI  │ │火车  │ │航班  │ │住宿  │
│专家  │ │专家  │ │专家  │ │专家  │ │专家  │ │专家  │
└──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘
   │       │       │       │       │       │
   ▼       ▼       ▼       ▼       ▼       ▼
┌─────┐ ┌──────┐ ┌─────┐ ┌──────────────────────┐
│墨迹  │ │高德   │ │高德  │ │   BigModel MCP       │
│天气  │ │地图   │ │地图  │ │ 12306 / 航班 / 住宿   │
│MCP  │ │MCP   │ │MCP  │ │                      │
└─────┘ └──────┘ └─────┘ └──────────────────────┘
```

### 核心设计

- **Supervisor 模式**：一个 Supervisor 编排 6 个专家子 Agent，负责对话管理、状态更新和结果汇总
- **State + Prompt 双驱动**：结构化字段（目的地/日期/天气状态等）存在 `TravelState` 中，每轮自动注入 System Prompt；阶段提示（"必须先查天气"）由代码根据 State 生成，约束 LLM 行为
- **Command 工具**：`update_trip_info`、`mark_weather_result` 等工具返回 `Command(update={...})`，原子性更新 State
- **Interrupt 机制**：天气确认、邮件发送等需要用户决策的节点通过 `interrupt()` 暂停，前端弹窗确认后恢复

## 技术栈

| 类别 | 技术 |
|------|------|
| LLM | DeepSeek Chat |
| Agent 框架 | LangChain + LangGraph + langgraph-supervisor |
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
