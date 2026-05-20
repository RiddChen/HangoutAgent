# 出行企划多智能体系统实施企划

这份文档服务两个场景：

```text
1. 理解当前项目 `/Users/riddler/PycharmProjects/PythonProject` 里 EmailAgent 和前端的复用方式
2. 在新的 PyCharm + uv 项目中，从 0 重建一个出行企划多智能体项目
```

目标是新增一个“同城本地玩乐企划多智能体”系统：用户说“我想这周日去西溪湿地玩”，系统自动解析日期、查询天气、判断是否适合出行、追问出发地、调用高德地图 MCP 做路线规划、允许用户接受或拒绝交通方案并重新规划，最终生成一份本地游玩计划。邮件不是默认最后一步，而是用户后续明确说“帮我约 Adam 一起去”时，再复用现有 `EmailAgent` 生成邀约邮件并等待用户确认发送。这里暂时不使用 Skills，优先朝 MCP 适配方向设计。

## 结论

推荐架构：

```text
Router + Orchestrator + Sequential/Conditional Subagents + MCP Adapter + Optional Handoff to EmailAgent
```

不要用一个大 Agent 做完所有事。同城本地玩乐企划天然包含多个外部能力和多个判断节点：日期、天气、地点、路线、偏好、用户确认、邮件邀约。更好的做法是：

```text
Agent 负责判断、拆解、合并、审核
MCP 负责连接外部服务能力
EmailAgent 只在用户明确要求邀请朋友时负责邮件草稿和发送确认
```

## 从 0 新建项目：严格按阶段来

原则：**每一步只导入已经存在的文件。每写完一个阶段就启动验证一次。不要原样复制旧 `chat.py`，因为旧文件依赖 `personal_chief`，新项目没有这个 Agent。**

推荐新项目路径：

```text
/Users/riddler/PycharmProjects/travel-agent
```

### 阶段 1：只创建 uv 项目

```bash
cd /Users/riddler/PycharmProjects
mkdir travel-agent
cd travel-agent
uv init --package
uv python pin 3.13
```

安装第一批最小依赖：

```bash
uv add fastapi uvicorn python-dotenv pydantic sse-starlette
```

创建目录：

```bash
mkdir -p app/api/v1 app/static docs
touch app/__init__.py app/api/__init__.py app/api/v1/__init__.py
```

### 阶段 2：先写一个完全独立的 main.py

先不要 import `chat`，不要 import `travel`，不要 import `email_agent`。

创建 `app/main.py`：

```python
from fastapi import FastAPI


app = FastAPI(title="TripCrew 出行企划多智能体 API")


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)
```

启动验证：

```bash
uv run python -m app.main
```

浏览器打开：

```text
http://127.0.0.1:8002/health
http://127.0.0.1:8002/docs
```

这个阶段通过后，再进入下一步。

### 阶段 3：新增 travel.py，再回 main.py 挂载

先创建 `app/api/v1/travel.py`：

```python
from fastapi import APIRouter
from pydantic import BaseModel


router = APIRouter()


class TravelChatRequest(BaseModel):
    message: str
    thread_id: str = "default"
    user_id: str | None = None


@router.post("/travel/send")
async def send_travel(request: TravelChatRequest):
    return {
        "type": "message",
        "thread_id": request.thread_id,
        "content": "Travel API skeleton is running.",
    }
```

然后再修改 `app/main.py`，这时才允许 import `travel`：

```python
from fastapi import FastAPI

from app.api.v1 import travel


app = FastAPI(title="TripCrew 出行企划多智能体 API")
app.include_router(travel.router, prefix="/api/v1", tags=["出行企划 Agent"])


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)
```

启动验证：

```bash
uv run python -m app.main
```

测试接口：

```text
POST http://127.0.0.1:8002/api/v1/travel/send
```

请求体：

```json
{
  "message": "明天去哪玩？",
  "thread_id": "demo"
}
```

### 阶段 4：复用旧前端静态页面

复制旧静态页面：

```bash
cp -R /Users/riddler/PycharmProjects/PythonProject/app/static/* app/static/
```

修改 `app/main.py`，加静态文件挂载：

```python
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import travel


app = FastAPI(title="TripCrew 出行企划多智能体 API")
app.include_router(travel.router, prefix="/api/v1", tags=["出行企划 Agent"])


@app.get("/health")
async def health():
    return {"status": "ok"}


static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend(path: str):
    if path.startswith("api/"):
        return JSONResponse({"error": "Not Found"}, status_code=404)

    file_path = os.path.join(static_dir, path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return {"message": "TripCrew is running", "status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)
```

注意：`main.py` 在 `app/` 目录里，所以静态目录是：

```python
os.path.join(os.path.dirname(__file__), "static")
```

不要写成：

```python
os.path.join(os.path.dirname(__file__), "app/static")
```

否则会变成 `app/app/static`。

### 阶段 5：再接 EmailAgent，不要复制旧 chat.py

安装 EmailAgent 依赖：

```bash
uv add aiosqlite langchain langgraph langgraph-checkpoint-sqlite openai
uv add langchain-google-community[gmail]
```

创建目录：

```bash
mkdir -p app/agents app/common app/integrations app/models app/db
touch app/agents/__init__.py app/common/__init__.py app/integrations/__init__.py app/models/__init__.py
```

复制 EmailAgent 依赖文件：

```bash
cp /Users/riddler/PycharmProjects/PythonProject/app/agents/email_agent.py app/agents/email_agent.py
cp /Users/riddler/PycharmProjects/PythonProject/app/common/logger.py app/common/logger.py
cp /Users/riddler/PycharmProjects/PythonProject/app/integrations/gmail_auth.py app/integrations/gmail_auth.py
cp /Users/riddler/PycharmProjects/PythonProject/app/integrations/gmail_tools.py app/integrations/gmail_tools.py
cp /Users/riddler/PycharmProjects/PythonProject/app/models/schemas.py app/models/schemas.py
```

不要复制：

```text
/Users/riddler/PycharmProjects/PythonProject/app/api/v1/chat.py
```

旧 `chat.py` 里面有：

```python
from app.agents.personal_chief import search_recipes, get_messages, clear_messages
```

新项目没有 `personal_chief`，所以原样复制必然报错。

手写新的 `app/api/v1/chat.py`：

```python
from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from app.agents.email_agent import email_agent
from app.models.schemas import ChatRequest


router = APIRouter()


@router.post("/chat/send", tags=["邮件 Agent"])
async def send_chat(request: ChatRequest):
    return EventSourceResponse(
        email_agent.generate_sse(
            request.thread_id,
            request.message or "",
            request.interrupt_decision,
        )
    )
```

再回到 `app/main.py`，这时才允许 import `chat` 和 `email_agent`。

如果没有 `credentials.json` / `token.json`，先不要让 EmailAgent 启动失败：

```python
from contextlib import asynccontextmanager

from app.agents.email_agent import email_agent
from app.api.v1 import chat, travel


@asynccontextmanager
async def lifespan(app: FastAPI):
    email_agent_started = False
    if os.path.exists("credentials.json") or os.path.exists("token.json"):
        await email_agent.init()
        email_agent_started = True
    else:
        print("EmailAgent skipped: credentials.json/token.json not found.")

    yield

    if email_agent_started:
        await email_agent.close()
```

然后 FastAPI 初始化要加：

```python
app = FastAPI(
    title="TripCrew 出行企划多智能体 API",
    lifespan=lifespan,
)
app.include_router(travel.router, prefix="/api/v1", tags=["出行企划 Agent"])
app.include_router(chat.router, prefix="/api/v1", tags=["邮件 Agent"])
```

这个阶段通过后，才继续做 Router、Orchestrator、MCP。

### 阶段 6：GitHub 初始化

`.gitignore` 必须包含：

```gitignore
.env
.venv/
__pycache__/
*.pyc
app/db/*.db
app/db/*.db-shm
app/db/*.db-wal
credentials.json
token.json
```

提交：

```bash
git init
git add .
git commit -m "init travel agent project"
```

推送：

```bash
git remote add origin git@github.com:你的用户名/travel-agent.git
git branch -M main
git push -u origin main
```

## 为什么朝 MCP 适配

MCP 很适合这个项目，因为“出行企划”的核心能力大多不是模型本身，而是外部工具能力：

```text
地点搜索       高德地图 MCP / 百度地图 MCP / 其他地图 MCP
周边搜索       高德地图 MCP
路线规划       高德地图 MCP
天气查询       天气 MCP / 地图平台天气接口 / 自定义 MCP
邮件发送       现有 EmailAgent，后面也可以封成邮件 MCP
模型资源搜索   ModelScope MCP
网页检索       搜索 MCP
日历写入       日历 MCP
```

这样后续替换供应商时，不需要重写 Agent，只需要换 MCP Server 或 MCP Adapter。

## 总体流程

```text
用户：
  我想这周日去西溪湿地玩。

Travel Router
  判断任务类型：local_outing_plan

Travel Orchestrator
  管理状态、调度 Agent、决定下一步追问还是调用工具

DateAgent
  把“这周日”解析成具体日期

WeatherAgent
  查询该日期天气；如果天气不适合户外，推荐替代日期并询问用户是否更换

PlaceAgent
  查询西溪湿地地点信息、开放情况、周边餐饮/活动

Orchestrator
  如果缺出发地，追问用户：你从哪里出发？

RouteAgent
  调用高德地图 MCP，生成公交/地铁/驾车/步行等路线方案

PlannerAgent
  汇总成一份可执行的本地游玩计划

ReviewAgent
  检查天气风险、时间是否合理、路线是否太绕、交通方式是否符合偏好

用户：
  可以接受交通方案，也可以说“不想换乘”“太远了”“换个时间”

Orchestrator
  根据用户反馈重新调用对应 Agent，更新 latest_plan

用户后续明确说：
  帮我跟 Adam 说一下这个计划，问他有没有时间一起去。

Router
  判断任务类型：invite_friend_by_email

Optional Handoff to EmailAgent
  使用 latest_plan 生成邀约邮件草稿
  用户确认后才发送
```

## 架构图

```text
app/api/v1/travel.py
        |
        v
app/agents/travel/router_agent.py
        |
        v
app/agents/travel/orchestrator_agent.py
        |
        +--------------------+--------------------+--------------------+
        |                    |                    |                    |
        v                    v                    v                    v
destination_agent.py   weather_agent.py    transport_agent.py    budget_agent.py
        |                    |                    |                    |
        +--------- MCP Adapter / Tool Gateway / Provider Client -------+
                              |
                              v
              AMap MCP / Weather MCP / ModelScope MCP / Custom MCP
                              |
                              v
app/agents/travel/planner_agent.py
                              |
                              v
app/agents/travel/review_agent.py
                              |
                              v
app/agents/email_agent.py
```

## 四种模式的取舍

### Router

Router 放在入口，用来判断用户意图。

示例任务类型：

```text
local_outing_plan          规划本地游玩
outing_modify              修改已有计划
date_check                 只解析/确认日期
weather_check              只查天气
route_plan                 只查路线
transport_replan           用户拒绝交通方式后重新规划
invite_friend_by_email     用户明确要求约朋友
email_only                 只发邮件
```

Router 不负责查地图、不负责发邮件，只负责分流。

### Subagents

Subagents 用来并行完成不同调研任务。

推荐拆成：

```text
DateAgent          日期解析
WeatherAgent       天气评估和替代日期建议
PlaceAgent         地点信息、开放情况、周边推荐
RouteAgent         高德地图 MCP 路线规划
PreferenceAgent    用户偏好和拒绝理由解析
BudgetAgent        预算估算
PlannerAgent       本地游玩计划整合
ReviewAgent        风险审核
```

这些 Agent 之间不要互相乱调。统一由 Orchestrator 调度，结果统一进 Planner。这个项目不是所有 Subagents 都并行：日期、天气、出发地、路线之间有先后依赖，所以采用“条件串行 + 局部并行”的模式。

### Handoffs

Handoff 用在“职责转移”。

本项目的 Handoff 不是默认结尾，而是用户明确要求邀请朋友之后才发生：

```text
RouterAgent -> EmailAgent
```

本地玩乐系统只把 `latest_plan`、朋友姓名、邮箱、邀约意图交给 EmailAgent。EmailAgent 继续负责：

```text
生成邮件草稿
检查收件人
触发 interrupt
等待用户确认
真正发送邮件
```

不要让 TravelAgent 直接调用真实发邮件工具。

### Skills

本项目第一版先不用 Skills。

原因：

```text
当前重点是跑通多 Agent + MCP + EmailAgent 复用
Skills 更适合沉淀稳定模板和操作规范
太早引入 Skills 会让主流程变重
```

后面稳定后，可以再把“行程 JSON 规范”“邮件模板”“MCP 工具调用规范”沉淀成 Skills。

## 推荐目录结构

新增这些文件：

```text
app/
  agents/
    travel/
      __init__.py
      router_agent.py
      orchestrator_agent.py
      destination_agent.py
      weather_agent.py
      transport_agent.py
      budget_agent.py
      planner_agent.py
      review_agent.py
      prompts.py
  api/
    v1/
      travel.py
  integrations/
    mcp/
      __init__.py
      client.py
      registry.py
      schemas.py
      amap_tools.py
      weather_tools.py
      modelscope_tools.py
  models/
    travel.py
```

保留这些现有文件，不要替换：

```text
app/agents/email_agent.py
app/agents/personal_chief.py
app/api/v1/chat.py
app/main.py
```

`app/main.py` 只做 additive 修改：挂载新的 travel router，不要破坏已有 EmailAgent 和 `personal_chief`。

## MCP 适配层设计

不要让每个 Agent 直接知道具体 MCP Server 名称。推荐加一层 `MCPToolGateway`。

```text
Agent
  -> TravelToolGateway
    -> MCPRegistry
      -> amap / weather / modelscope / custom server
```

### MCPRegistry

职责：

```text
读取可用 MCP Server 配置
列出当前可用工具
按能力名找到具体工具
屏蔽供应商差异
```

能力名建议固定成项目内部名字：

```text
poi_search
poi_detail
nearby_search
route_plan
weather_forecast
city_resolve
email_draft
email_send
```

具体 MCP Server 可以变：

```text
poi_search      -> amap.maps_text_search
nearby_search   -> amap.maps_around_search
poi_detail      -> amap.maps_search_detail
route_plan      -> amap route tool
weather_forecast -> weather MCP or custom HTTP wrapper
```

### app/integrations/mcp/registry.py

第一版可以先写成配置映射：

```python
MCP_CAPABILITIES = {
    "poi_search": {
        "server": "amap",
        "tool": "maps_text_search",
    },
    "nearby_search": {
        "server": "amap",
        "tool": "maps_around_search",
    },
    "poi_detail": {
        "server": "amap",
        "tool": "maps_search_detail",
    },
}
```

后面再改成自动 `tools/list`。

## MCP Server 候选

截至 2026-05-19，可以优先考虑这些：

### 高德地图 MCP

候选 1：高德官方 NPM MCP

```json
{
  "mcpServers": {
    "amap-maps": {
      "command": "npx",
      "args": ["-y", "@amap/amap-maps-mcp-server"],
      "env": {
        "AMAP_MAPS_API_KEY": "你的高德 key"
      }
    }
  }
}
```

候选 2：`sugarforever/amap-mcp-server`

```json
{
  "mcpServers": {
    "amap-mcp-server": {
      "command": "uvx",
      "args": ["amap-mcp-server"],
      "env": {
        "AMAP_MAPS_API_KEY": "你的高德 key"
      }
    }
  }
}
```

这个实现文档里列出了 POI 关键词搜索、周边搜索、POI 详情查询，并支持 `stdio`、`sse`、`streamable-http` 三种传输方式。

### ModelScope MCP

ModelScope 官方 MCP 更适合做模型、数据集、应用、论文、MCP Server 发现，不一定直接负责地图。

配置示例：

```json
{
  "mcpServers": {
    "modelscope-mcp-server": {
      "command": "uvx",
      "args": ["modelscope-mcp-server"],
      "env": {
        "MODELSCOPE_API_TOKEN": "你的 ModelScope token"
      }
    }
  }
}
```

它在本项目里的价值：

```text
搜索可用 MCP Server
搜索模型/应用资源
后续扩展图片生成、报告生成、模型调用
```

第一版不依赖它跑主流程。第一版主流程优先接高德 MCP。

## 数据结构

建议所有 Agent 之间都传结构化对象，避免自由文本来回传导致后面不好调试。

### TravelRequest

```python
class TravelRequest(BaseModel):
    user_id: str | None = None
    origin_city: str | None = None
    origin_location: str | None = None
    travel_date: str
    days: int = 1
    people_count: int = 1
    budget_cny: int | None = None
    preferences: list[str] = []
    avoid: list[str] = []
    need_email: bool = False
    invitees: list[Invitee] = []
```

### Invitee

```python
class Invitee(BaseModel):
    name: str
    email: str | None = None
    relationship: str | None = None
```

### CandidatePlace

```python
class CandidatePlace(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    location: str | None = None
    category: str | None = None
    rating: float | None = None
    source: str
    source_id: str | None = None
    reasons: list[str] = []
```

### TravelPlan

```python
class TravelPlan(BaseModel):
    title: str
    date: str
    origin: str
    summary: str
    itinerary: list[ItineraryItem]
    estimated_cost_cny: int | None = None
    weather_summary: str | None = None
    transport_summary: str | None = None
    backup_plan: str | None = None
    email_brief: str | None = None
```

### ItineraryItem

```python
class ItineraryItem(BaseModel):
    start_time: str
    end_time: str | None = None
    activity: str
    place_name: str | None = None
    address: str | None = None
    transport: str | None = None
    cost_cny: int | None = None
    notes: str | None = None
```

### ReviewResult

```python
class ReviewResult(BaseModel):
    passed: bool
    issues: list[str] = []
    missing_fields: list[str] = []
    suggestions: list[str] = []
```

## Agent 职责

### TravelRouterAgent

输入：

```text
用户原始问题
当前会话状态
```

输出：

```json
{
  "intent": "local_outing_plan",
  "confidence": 0.92,
  "required_next_action": "parse_date"
}
```

它只做路由，不查工具。

### TravelOrchestratorAgent

职责：

```text
抽取槽位
判断缺失信息
调度 Subagents
保存中间状态
决定是否进入 EmailAgent
```

缺失信息示例：

```text
不知道出发城市 -> 追问
需要发邮件但没有邮箱 -> 追问
用户说“明天” -> 解析成具体日期
```

### DestinationAgent

职责：

```text
根据城市、日期、偏好查候选地点
调用 poi_search / nearby_search / poi_detail
过滤明显不适合的地点
返回候选地点列表
```

输出：

```json
{
  "places": [],
  "reasoning_summary": "推荐这些地点是因为..."
}
```

### WeatherAgent

职责：

```text
查询天气
判断是否适合户外
给出备用室内方案建议
```

第一版如果没有天气 MCP，可以先用自定义 HTTP wrapper 或手工 mock，接口保持不变。

### TransportAgent

职责：

```text
规划从出发地到地点之间的路线
估算通勤时长
判断一天内是否太赶
```

### BudgetAgent

职责：

```text
估算交通、门票、餐饮、其他成本
判断是否超过用户预算
```

第一版预算可以是规则估算，不强依赖 MCP。

### PlannerAgent

职责：

```text
整合所有 Subagents 结果
生成最终一日游行程
生成 email_brief
```

Planner 不直接发邮件。

### ReviewAgent

职责：

```text
检查计划是否可执行
检查有没有缺字段
检查是否需要用户确认
```

如果 `need_email=true` 且邮箱缺失，不能进入 EmailAgent。

### EmailAgent

复用现有：

```text
app/agents/email_agent.py
```

Travel 系统给它的输入应该是：

```json
{
  "to": "friend@example.com",
  "subject": "明天一起去玩？",
  "body_brief": "根据以下行程写一封自然的邀约邮件...",
  "travel_plan": {}
}
```

EmailAgent 继续负责发送前确认。不要绕开它的 interrupt。

## API 设计

新增：

```text
POST /api/v1/travel/send
```

请求：

```json
{
  "message": "明天去哪玩？帮我约小王一起。",
  "thread_id": "travel-001",
  "user_id": "1"
}
```

普通响应：

```json
{
  "type": "message",
  "content": "你从哪个城市出发？预算大概多少？"
}
```

计划响应：

```json
{
  "type": "travel_plan",
  "plan": {
    "title": "杭州明日一日游",
    "date": "2026-05-20",
    "summary": "上午西湖，下午法喜寺，晚上湖滨散步。",
    "itinerary": []
  }
}
```

邮件确认响应：

```json
{
  "type": "email_approval_required",
  "draft": {
    "to": "friend@example.com",
    "subject": "明天一起去杭州玩？",
    "body": "..."
  }
}
```

## 状态管理

每个 `thread_id` 保存：

```text
原始用户需求
已收集槽位
解析后的具体日期
天气评估结果
用户出发地
用户偏好和拒绝理由
路线候选方案
用户接受的路线方案
Subagents 中间结果
最终 TravelPlan
ReviewResult
latest_plan
EmailAgent handoff 状态，仅在用户要求邀请朋友后出现
```

第一版可以直接复用 LangGraph checkpoint / SQLite 思路。不要只存在内存里，否则服务重启后会话丢失。

## 第一版 MVP 路线

### 第 1 步：先做无 MCP 的骨架

目标：

```text
Router 能识别出行规划
Orchestrator 能追问缺失信息
Planner 能生成 mock 行程
EmailAgent handoff 能跑通
```

暂时 mock：

```text
POI
天气
路线
预算
```

### 第 2 步：接高德 MCP 的 POI 搜索

目标：

```text
DestinationAgent 调用高德 MCP 搜索景点/餐厅
返回真实地点名称、地址、经纬度、POI ID
```

### 第 3 步：接路线规划

目标：

```text
TransportAgent 根据地点顺序估算通勤时间
Planner 根据路线结果调整行程顺序
```

### 第 4 步：接天气

目标：

```text
WeatherAgent 判断是否适合户外
雨天自动给室内备用方案
```

### 第 5 步：完善邮件闭环

目标：

```text
Planner 生成 email_brief
EmailAgent 生成邮件草稿
用户 approve 后才发送
发送结果写回 travel thread
```

## 安全与确认

外部副作用必须确认：

```text
发送邮件
写入日历
创建订单
订票
付款
```

本项目第一版只有邮件发送，所以规则是：

```text
TravelAgent 不允许直接发送邮件
EmailAgent 发送前必须 interrupt
用户没有 approve 时不能真正发送
```

MCP 工具调用也要分级：

```text
只读工具：POI 搜索、天气查询、路线查询，可以自动调用
写入工具：邮件发送、日历写入，必须确认
交易工具：订票、付款，第一版不做
```

## Prompt 设计原则

每个 Agent 的 prompt 要短而明确。

### Router prompt

```text
你是出行企划系统的入口路由器。
只判断用户意图，不要生成行程，不要调用地图，不要发送邮件。
输出固定 JSON：intent, confidence, required_next_action。
```

### Destination prompt

```text
你是目的地推荐 Agent。
你只能根据工具返回的真实地点信息推荐候选地点。
不要编造地址、评分、营业时间。
输出 CandidatePlace 列表。
```

### Planner prompt

```text
你是行程规划 Agent。
你会收到目的地、天气、交通、预算结果。
请生成一份现实可执行的一日游行程。
不要发送邮件，只生成 email_brief。
输出 TravelPlan JSON。
```

### Review prompt

```text
你是行程审核 Agent。
检查时间、预算、天气、路线、缺失字段。
如果计划不可执行，指出问题和修改建议。
输出 ReviewResult JSON。
```

## 实现注意点

1. 先把结构化 schema 写好，再写 Agent。
2. MCP Adapter 返回值要保留 `source` 和 `raw`，方便排查工具结果。
3. Agent 之间只传项目内部 schema，不传 MCP 原始大 JSON。
4. Planner 不允许编造地点信息，只能使用 DestinationAgent 给出的候选。
5. EmailAgent handoff 前必须有完整收件人和邮件草稿。
6. `personal_chief` 不参与这个系统，避免两个项目逻辑混在一起。

## 推荐开发顺序

```text
1. app/models/travel.py
2. app/agents/travel/prompts.py
3. app/agents/travel/router_agent.py
4. app/agents/travel/orchestrator_agent.py
5. app/agents/travel/planner_agent.py
6. app/agents/travel/review_agent.py
7. app/api/v1/travel.py
8. app/main.py 挂载 travel router
9. app/integrations/mcp/registry.py
10. app/integrations/mcp/amap_tools.py
11. DestinationAgent 接入真实 MCP
12. TransportAgent / WeatherAgent 逐步替换 mock
13. Handoff 到 EmailAgent
```

## 验收标准

第一版跑通后，下面这组对话应该成立：

```text
用户：明天去哪玩？帮我约小王一起。
系统：你从哪个城市出发？小王的邮箱是多少？

用户：杭州，xiaowang@example.com，预算 300，想轻松一点。
系统：
  1. 查杭州明天天气
  2. 查轻松型景点和餐饮
  3. 生成一日游计划
  4. 给出邮件草稿
  5. 等待用户确认是否发送

用户：确认发送。
系统：邮件已发送。
```

最小验收：

```text
能追问缺失城市和邮箱
能生成结构化 TravelPlan
能进入 EmailAgent 草稿流程
发送前有人工确认
不会改坏 personal_chief
```

## 外部资料

- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents)：适合 code-first agent 应用，应用自己控制编排、工具执行、状态和审批。
- [OpenAI Agent Builder](https://developers.openai.com/api/docs/guides/agent-builder)：适合可视化工作流、节点和 typed edge，后续如果想做可视化编排可以参考。
- [OpenAI Agent Safety](https://developers.openai.com/api/docs/guides/agent-builder-safety)：发送邮件这类外部副作用要保留确认，Agent 间传递数据尽量用结构化输出。
- [ModelScope MCP Server](https://github.com/modelscope/modelscope-mcp-server)：可用于 ModelScope 资源发现、模型/数据集/应用/MCP Server 搜索。
- [高德官方 Amap Maps MCP](https://mcp.so/zh/server/amap-maps/amap?tab=content)：可用 `@amap/amap-maps-mcp-server` 接入高德地图能力。
- [sugarforever/amap-mcp-server](https://github.com/sugarforever/amap-mcp-server)：可用于 POI 搜索、周边搜索、POI 详情，并支持 `stdio`、`sse`、`streamable-http`。
