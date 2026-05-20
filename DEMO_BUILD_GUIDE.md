# CityBuddy Demo 开发文档

这份文档只解决一个目标：**先把 demo 跑通**。

不要先做大而全的 MCP、多轮复杂状态、真实天气、真实高德路线、真实发邮件。那些都放到后面。现在先做一个可以展示的最小项目：

```text
用户输入：我想这周日去西溪湿地玩，从武林广场出发
系统输出：一份本地玩乐计划

用户输入：帮我写一封邮件约 Adam 一起去
系统输出：一封邀约邮件草稿
```

## 现在保留什么

第一版只做 4 个能力：

```text
1. 识别用户想规划本地玩乐
2. 从用户输入里提取：日期、地点、出发地
3. 生成本地游玩计划
4. 根据计划生成邀约邮件草稿
```

## 现在先不做什么

这些先删掉，不进第一版：

```text
不接高德 MCP
不查真实天气
不查真实路线
不做预算
不做 ReviewAgent
不做复杂多轮重规划
不真正发送邮件
不接 Gmail interrupt
不手写 LangGraph StateGraph
不改前端页面
```

原因：这些都很有价值，但不是 demo 第一版必须项。先跑通，再加。

## 第一版项目结构

第一版只需要这些新增/修改文件：

```text
app/models/local_outing.py
app/agents/citybuddy/
  __init__.py
  extractor.py
  planner.py
  invite_writer.py
  orchestrator.py
app/api/v1/citybuddy.py
app/main.py
```

## 第 1 步：创建目录

在项目根目录运行：

```bash
mkdir -p app/agents/citybuddy
touch app/agents/citybuddy/__init__.py
```

## 第 2 步：写数据模型

新建：

```text
app/models/local_outing.py
```

写入：

```python
from pydantic import BaseModel, Field


class OutingRequest(BaseModel):
    raw_text: str
    date_text: str | None = None
    destination: str | None = None
    origin: str | None = None


class OutingPlan(BaseModel):
    title: str
    date_text: str
    destination: str
    origin: str
    route_summary: str
    schedule: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class InviteDraft(BaseModel):
    friend_name: str
    subject: str
    body: str
```

这一步只定义数据形状，不写逻辑。

## 第 3 步：写信息提取器

新建：

```text
app/agents/citybuddy/extractor.py
```

第一版不要追求完美，也不要接 LLM。先用简单规则，让 demo 能跑。

写入：

```python
from app.models.local_outing import OutingRequest


class OutingExtractor:
    def extract(self, message: str) -> OutingRequest:
        date_text = None
        destination = None
        origin = None

        for marker in ["这周日", "周日", "明天", "后天", "下周六", "下周日"]:
            if marker in message:
                date_text = marker
                break

        if "西溪湿地" in message:
            destination = "西溪湿地"

        if "从" in message and "出发" in message:
            origin = message.split("从", 1)[1].split("出发", 1)[0].strip()

        return OutingRequest(
            raw_text=message,
            date_text=date_text,
            destination=destination,
            origin=origin,
        )
```

注意：这里的规则只是 demo 起步，不是最终智能能力。跑通后再换成 `create_agent` 结构化抽取。

## 第 4 步：写计划生成器

新建：

```text
app/agents/citybuddy/planner.py
```

写入：

```python
from app.models.local_outing import OutingPlan, OutingRequest


class OutingPlanner:
    def build_plan(self, request: OutingRequest) -> OutingPlan:
        date_text = request.date_text or "你指定的时间"
        destination = request.destination or "目的地"
        origin = request.origin or "你的出发地"

        return OutingPlan(
            title=f"{destination}本地玩乐计划",
            date_text=date_text,
            destination=destination,
            origin=origin,
            route_summary=f"从{origin}出发前往{destination}。第一版暂时使用模拟路线，后续再接高德 MCP。",
            schedule=[
                f"{date_text} 09:30 从{origin}出发",
                f"{date_text} 10:30 抵达{destination}，开始游玩",
                f"{date_text} 12:30 附近吃午饭",
                f"{date_text} 14:00 继续散步、拍照或轻松游玩",
                f"{date_text} 16:30 准备返程",
            ],
            notes=[
                "第一版不查真实天气。",
                "第一版不查真实路线。",
                "后续可以接高德 MCP 和天气工具。",
            ],
        )
```

## 第 5 步：写邀约邮件草稿生成器

新建：

```text
app/agents/citybuddy/invite_writer.py
```

写入：

```python
from app.models.local_outing import InviteDraft, OutingPlan


class InviteWriter:
    def write(self, friend_name: str, plan: OutingPlan) -> InviteDraft:
        return InviteDraft(
            friend_name=friend_name,
            subject=f"{plan.date_text}一起去{plan.destination}吗？",
            body=(
                f"{friend_name}，\\n\\n"
                f"我计划{plan.date_text}从{plan.origin}出发去{plan.destination}玩。\\n"
                f"大概安排是：\\n"
                + "\\n".join(f"- {item}" for item in plan.schedule)
                + "\\n\\n你有时间的话要不要一起去？"
            ),
        )
```

这一版只生成草稿，不真的发邮件。

## 第 6 步：写 Orchestrator

新建：

```text
app/agents/citybuddy/orchestrator.py
```

写入：

```python
from app.agents.citybuddy.extractor import OutingExtractor
from app.agents.citybuddy.invite_writer import InviteWriter
from app.agents.citybuddy.planner import OutingPlanner
from app.models.local_outing import OutingPlan


class CityBuddyOrchestrator:
    def __init__(self):
        self.extractor = OutingExtractor()
        self.planner = OutingPlanner()
        self.invite_writer = InviteWriter()
        self.latest_plans: dict[str, OutingPlan] = {}

    async def handle(self, thread_id: str, message: str) -> dict:
        if self._is_invite_request(message):
            plan = self.latest_plans.get(thread_id)
            if plan is None:
                return {
                    "type": "message",
                    "content": "我还没有可用于邀约的计划。你先告诉我想什么时候去哪里玩。",
                }

            friend_name = self._extract_friend_name(message)
            draft = self.invite_writer.write(friend_name=friend_name, plan=plan)
            return {
                "type": "invite_draft",
                "draft": draft.model_dump(),
            }

        request = self.extractor.extract(message)

        missing = []
        if not request.date_text:
            missing.append("时间")
        if not request.destination:
            missing.append("目的地")
        if not request.origin:
            missing.append("出发地")

        if missing:
            return {
                "type": "message",
                "content": f"我还缺少：{'、'.join(missing)}。请补充一下，比如：这周日从武林广场出发去西溪湿地玩。",
            }

        plan = self.planner.build_plan(request)
        self.latest_plans[thread_id] = plan

        return {
            "type": "outing_plan",
            "plan": plan.model_dump(),
        }

    def _is_invite_request(self, message: str) -> bool:
        return any(word in message for word in ["邮件", "约", "邀请", "一起去", "问他", "问她"])

    def _extract_friend_name(self, message: str) -> str:
        for name in ["Adam", "adam", "小王", "朋友"]:
            if name in message:
                return name
        return "朋友"


citybuddy_orchestrator = CityBuddyOrchestrator()
```

这是第一版最核心文件：它把提取、规划、邀约串起来。

## 第 7 步：写 API

新建：

```text
app/api/v1/citybuddy.py
```

写入：

```python
from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.citybuddy.orchestrator import citybuddy_orchestrator


router = APIRouter()


class CityBuddyRequest(BaseModel):
    message: str
    thread_id: str = "default"


@router.post("/citybuddy/send")
async def send_citybuddy(request: CityBuddyRequest):
    return await citybuddy_orchestrator.handle(
        thread_id=request.thread_id,
        message=request.message,
    )
```

## 第 8 步：挂载 API

修改：

```text
app/main.py
```

找到：

```python
from app.api.v1 import chat, travel
```

改成：

```python
from app.api.v1 import chat, citybuddy, travel
```

找到 router 挂载位置，加一行：

```python
app.include_router(citybuddy.router, prefix="/api/v1", tags=["CityBuddy Demo"])
```

也就是最终有：

```python
app.include_router(chat.router, prefix="/api/v1", tags=["邮件 Agent"])
app.include_router(travel.router, prefix="/api/v1", tags=["出行企划 Agent"])
app.include_router(citybuddy.router, prefix="/api/v1", tags=["CityBuddy Demo"])
```

## 第 9 步：启动

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8003 --reload
```

如果 8003 被占用，换 8004。

## 第 10 步：测试 demo

第一轮：生成计划。

```bash
curl -X POST http://127.0.0.1:8003/api/v1/citybuddy/send \
  -H "Content-Type: application/json" \
  -d '{"message":"我想这周日从武林广场出发去西溪湿地玩","thread_id":"demo"}'
```

应该返回：

```json
{
  "type": "outing_plan",
  "plan": {
    "title": "西溪湿地本地玩乐计划"
  }
}
```

第二轮：生成邀约邮件草稿。

```bash
curl -X POST http://127.0.0.1:8003/api/v1/citybuddy/send \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我写封邮件约 Adam 一起去","thread_id":"demo"}'
```

应该返回：

```json
{
  "type": "invite_draft",
  "draft": {
    "friend_name": "Adam",
    "subject": "这周日一起去西溪湿地吗？"
  }
}
```

到这里，demo 就跑通了。

## 第 11 步：demo 跑通后再升级

只在 demo 跑通后再加这些：

```text
1. OutingExtractor 换成 LangChain create_agent 结构化抽取
2. OutingPlanner 换成 LangChain create_agent 生成更自然计划
3. RoutePlanner 接高德 MCP
4. WeatherAgent 接天气工具
5. InviteWriter 改成调用真实 EmailAgent
6. latest_plans 从内存换成 SQLite
```

不要倒过来。先跑通 demo，再加智能。
