# 出行企划多智能体系统 — 从零搭建教程

> **前置知识**：本教程假设你已完成以下课程学习：
> - 第1节 Runtime（State / Store / Context）
> - 第2节 Middleware（PII / ModelFallback / HITL）
> - 第3节 EmailAgent 案例（动态提示词、动态工具、SSE）
> - 第4节 MCP（stdio / http / 自定义 MCP Server）
> - 第5节 多Agent（Subagents / Handoffs / Skills / Router）
>
> 教程中会标注 **「回顾」** 来关联你学过的知识点。

---

## 目录

- [第一章 架构设计：为什么混合使用多种模式](#第一章-架构设计为什么混合使用多种模式)
- [第二章 数据结构先行：Agent 之间的契约](#第二章-数据结构先行agent-之间的契约)
- [第三章 自定义 State：记录出行规划全流程状态](#第三章-自定义-state记录出行规划全流程状态)
- [第四章 Router Agent：入口意图识别](#第四章-router-agent入口意图识别)
- [第五章 Orchestrator Agent：槽位补全与调度](#第五章-orchestrator-agent槽位补全与调度)
- [第六章 Subagents：并行调研四大维度](#第六章-subagents并行调研四大维度)
- [第七章 Planner Agent：汇总生成行程](#第七章-planner-agent汇总生成行程)
- [第八章 Review Agent：审核行程可执行性](#第八章-review-agent审核行程可执行性)
- [第九章 Handoff to EmailAgent：邮件邀约闭环](#第九章-handoff-to-emailagent邮件邀约闭环)
- [第十章 接入真实 MCP：高德地图](#第十章-接入真实-mcp高德地图)
- [第十一章 FastAPI 接口与 SSE 流式输出](#第十一章-fastapi-接口与-sse-流式输出)
- [第十二章 完整测试与验收](#第十二章-完整测试与验收)
- [附录 A 完整目录结构](#附录-a-完整目录结构)
- [附录 B 常见问题](#附录-b-常见问题)

---

## 第一章 架构设计：为什么混合使用多种模式

### 1.1 回顾：四种多 Agent 模式

在第5节课程中，你学到了四种模式：

| 模式 | 核心思路 | 并行 | 多步 | 子Agent直接对话 |
|------|---------|------|------|----------------|
| **Subagents** | 主 Agent 把子 Agent 当 Tool 用 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐ |
| **Handoffs** | 改变 state 触发 Agent 切换 | - | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Skills** | 单 Agent 按需加载技能 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Router** | 路由 Agent 分类后分发 | ⭐⭐⭐ | - | ⭐⭐⭐ |

### 1.2 出行企划为什么不能只用一种

你在第5节课做的婚礼策划案例用的是 **纯 Subagents**，因为那个案例的流程相对线性：

```
用户说出需求 → coordinator 更新 state → 并行调用 3 个 subagent → 汇总
```

但出行企划比婚礼策划多了几个复杂度：

1. **用户意图不明确**：用户可能说"明天去哪玩"（需要规划）、也可能说"帮我查个天气"（只查天气）、也可能说"帮我发个邮件约朋友"（只发邮件）。需要 **Router** 先判断意图。

2. **信息不完整**：用户说"明天去玩"，但没说城市、没说预算、没说邮箱。需要 **Orchestrator** 追问。这在婚礼案例中是 coordinator 做的，但我们把它独立出来，因为槽位逻辑更复杂。

3. **并行调研**：目的地、天气、交通、预算四个维度可以并行。这是 **Subagents** 的强项。

4. **职责转移**：行程生成后，需要把控制权交给 EmailAgent 来发邮件。EmailAgent 有自己的认证、HITL 中间件，是一个独立的 Agent。这是 **Handoff** 的典型场景。

### 1.3 最终架构

```
用户输入
  │
  ▼
┌─────────────────┐
│  Router Agent    │  ← 只判断意图，输出 JSON
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Orchestrator    │  ← 补齐槽位（城市/日期/预算/邮箱）
│  Agent           │     缺什么追问什么
└────────┬────────┘
         │ 槽位齐了
         ▼
┌────────────────────────────────────────┐
│         Subagents (并行)                │
│  ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │Destination│ │ Weather  │ │Transport│ │
│  │  Agent    │ │  Agent   │ │  Agent  │ │
│  └──────────┘ └──────────┘ └────────┘ │
│                ┌────────┐              │
│                │ Budget │              │
│                │  Agent │              │
│                └────────┘              │
└────────────────────┬───────────────────┘
                     │
                     ▼
           ┌─────────────────┐
           │  Planner Agent   │  ← 汇总生成 TravelPlan
           └────────┬────────┘
                    │
                    ▼
           ┌─────────────────┐
           │  Review Agent    │  ← 检查时间/预算/天气/缺失字段
           └────────┬────────┘
                    │
                    ▼ need_email=True?
           ┌─────────────────┐
           │  EmailAgent      │  ← Handoff：生成草稿 → HITL 确认 → 发送
           │  (已有，复用)     │
           └─────────────────┘
```

### 1.4 和婚礼案例的对比

| 对比项 | 婚礼策划 (课程案例) | 出行企划 (本项目) |
|--------|---------------------|-------------------|
| 模式 | 纯 Subagents | Router + Orchestrator + Subagents + Handoff |
| 入口 | coordinator 直接对话 | Router 先判断意图 |
| 信息收集 | coordinator 用 `update_state` tool | Orchestrator 专门负责追问 |
| 子 Agent | travel/venue/playlist | destination/weather/transport/budget |
| 邮件 | 无 | Handoff 到已有 EmailAgent |
| MCP | Kiwi(航班) + Time | 高德地图(POI/路线) + 天气 MCP |
| 状态持久化 | 无 (InMemorySaver) | SQLite (AsyncSqliteSaver) |

---

## 第二章 数据结构先行：Agent 之间的契约

> **原则**：所有 Agent 之间传递的数据都用 Pydantic Model，不传自由文本。这样后面调试时能看清楚每个 Agent 的输入输出。

### 2.1 为什么先写数据结构

在课程的 EmailAgent 案例中，数据结构很简单——只有 `messages` 和 `authenticated`。

但出行企划的 Agent 多了，中间数据复杂了。如果不先定义好"契约"，后面每个 Agent 各自发挥，最后 Planner 汇总时就会发现数据对不上。

### 2.2 创建 `app/models/travel.py`

```python
# app/models/travel.py

from pydantic import BaseModel


class Invitee(BaseModel):
    """受邀人信息"""
    name: str
    email: str | None = None
    relationship: str | None = None


class TravelRequest(BaseModel):
    """用户出行需求，由 Orchestrator 填充"""
    origin_city: str | None = None
    travel_date: str | None = None
    days: int = 1
    people_count: int = 1
    budget_cny: int | None = None
    preferences: list[str] = []
    need_email: bool = False
    invitees: list[Invitee] = []


class CandidatePlace(BaseModel):
    """目的地候选，由 DestinationAgent 返回"""
    name: str
    address: str | None = None
    city: str | None = None
    category: str | None = None
    rating: float | None = None
    source: str = "mock"


class WeatherInfo(BaseModel):
    """天气信息，由 WeatherAgent 返回"""
    city: str
    date: str
    weather: str
    temperature_high: int | None = None
    temperature_low: int | None = None
    suitable_outdoor: bool = True
    suggestion: str = ""


class TransportInfo(BaseModel):
    """交通信息，由 TransportAgent 返回"""
    from_place: str
    to_place: str
    method: str
    duration_minutes: int | None = None
    cost_cny: int | None = None


class ItineraryItem(BaseModel):
    """行程中的一个环节"""
    start_time: str
    end_time: str | None = None
    activity: str
    place_name: str | None = None
    address: str | None = None
    transport: str | None = None
    cost_cny: int | None = None


class TravelPlan(BaseModel):
    """最终行程方案，由 PlannerAgent 生成"""
    title: str
    date: str
    origin: str
    summary: str
    itinerary: list[ItineraryItem] = []
    estimated_cost_cny: int | None = None
    weather_summary: str | None = None
    transport_summary: str | None = None
    email_brief: str | None = None


class ReviewResult(BaseModel):
    """审核结果，由 ReviewAgent 生成"""
    passed: bool
    issues: list[str] = []
    suggestions: list[str] = []
```

### 2.3 验证

打开终端，在项目根目录运行：

```bash
uv run python -c "from app.models.travel import TravelPlan, ReviewResult; print('数据结构 OK')"
```

如果输出 `数据结构 OK`，这一步就完成了。

> **要点**：这些 Model 不一定是最终版，后面开发过程中会按需调整。但先定义出来，让你开发每个 Agent 时知道"我应该输出什么"。

---

## 第三章 自定义 State：记录出行规划全流程状态

> **「回顾」** 第1节 Runtime 中你学到：`AgentState` 默认只有 `messages`。通过继承 `AgentState`，可以添加自定义属性来记录任务状态。

### 3.1 为什么需要自定义 State

出行企划的主 Agent（Orchestrator）需要记录：
- 用户需求的各个槽位（城市、日期、预算…）
- 当前处于哪个阶段（收集信息 / 调研中 / 生成行程 / 邮件确认）
- 各个 Subagent 的返回结果

就像你在 EmailAgent 中用 `AuthenticatedState` 记录认证状态一样，这里我们用 `TravelState` 记录整个流程状态。

### 3.2 创建 `app/agents/travel/state.py`

```python
# app/agents/travel/state.py

from typing import NotRequired

from langchain.agents import AgentState


class TravelState(AgentState):
    """出行企划主 Agent 的状态

    继承自 AgentState，自动拥有 messages 属性（历史消息列表）。
    以下属性用于记录整个规划流程的中间数据。
    """

    # ---- 阶段标记 ----
    # 可选值: collecting_slots / researching / planning / reviewing / emailing / done
    phase: NotRequired[str]

    # ---- 用户需求槽位 ----
    origin_city: NotRequired[str]
    travel_date: NotRequired[str]
    days: NotRequired[int]
    people_count: NotRequired[int]
    budget_cny: NotRequired[int]
    preferences: NotRequired[list[str]]
    need_email: NotRequired[bool]
    invitee_name: NotRequired[str]
    invitee_email: NotRequired[str]

    # ---- Subagent 结果 ----
    destination_result: NotRequired[str]
    weather_result: NotRequired[str]
    transport_result: NotRequired[str]
    budget_result: NotRequired[str]

    # ---- 最终输出 ----
    travel_plan: NotRequired[str]
    review_result: NotRequired[str]
    email_brief: NotRequired[str]
```

### 3.3 知识点：为什么用 `NotRequired`

> **「回顾」** 第1节中你看到的写法：
> ```python
> class CustomState(AgentState):
>     model_call_count: NotRequired[int]
> ```

`NotRequired` 表示这个字段在 State 创建时不是必须提供的。这很重要，因为：
- 用户第一次发消息时，我们只有 `messages`，其他字段都还不知道
- 随着对话推进，Orchestrator 通过 `Command(update={...})` 逐步填充这些字段

如果不用 `NotRequired`，创建 Agent 时 LangGraph 会要求你在第一次调用时就提供所有字段的值。

### 3.4 验证

```bash
uv run python -c "from app.agents.travel.state import TravelState; print('State OK')"
```

> 记得先创建 `app/agents/travel/__init__.py`（空文件即可），否则 Python 无法识别这个包。

---

## 第四章 Router Agent：入口意图识别

### 4.1 Router 的职责

Router 是整个系统的入口。它只做一件事：**判断用户想干什么**。

它不查地图、不查天气、不发邮件，只输出一个 JSON：

```json
{
  "intent": "travel_plan_with_email",
  "confidence": 0.92
}
```

可选的意图类型：

| intent | 含义 |
|--------|------|
| `travel_plan` | 只规划行程 |
| `travel_plan_with_email` | 规划行程 + 约朋友 |
| `weather_only` | 只查天气 |
| `email_only` | 只发邮件（直接转给 EmailAgent） |
| `chitchat` | 闲聊，不需要规划 |

### 4.2 为什么 Router 不用 Tool

> **「回顾」** 第5节中你看到 Router 的定义：路由模式中，一个 Agent 对用户请求分类后导向专门的 Agent。

Router 的工作是"理解语言"，不是"调用工具"。给它 Tool 反而会干扰它的判断。所以 Router 是一个 **没有 Tool 的纯文本 Agent**，靠 system prompt 来约束它的输出格式。

### 4.3 创建 `app/agents/travel/prompts.py`

我们把所有 Agent 的 system prompt 集中管理：

```python
# app/agents/travel/prompts.py

ROUTER_PROMPT = """你是出行企划系统的入口路由器。

你的唯一任务是判断用户的意图，然后输出 JSON。

可选的意图类型：
- travel_plan：用户想规划出行，但不需要发邮件约人
- travel_plan_with_email：用户想规划出行，并且提到了要约朋友/发邮件
- weather_only：用户只是想查某个地方的天气
- email_only：用户只是想发邮件，和出行规划无关
- chitchat：闲聊，不涉及出行规划

规则：
1. 只输出 JSON，不要输出其他任何内容
2. 不要调用任何工具
3. 不要生成行程
4. 不要追问用户

输出格式：
{"intent": "travel_plan_with_email", "confidence": 0.92}
"""


ORCHESTRATOR_PROMPT = """你是出行企划系统的协调者。

你的任务是：
1. 从用户的消息中提取出行需求信息
2. 如果信息不完整，追问用户
3. 信息齐全后，调用 start_research 工具启动调研

你需要收集的信息（槽位）：
- origin_city：出发城市（必须）
- travel_date：出行日期（必须，如果用户说"明天"，请转换成具体日期）
- days：出行天数（默认 1 天）
- people_count：出行人数（默认 1 人）
- budget_cny：预算，单位元（可选，但建议询问）
- preferences：偏好，如"轻松""户外""美食"等（可选）
- need_email：是否需要发邮件约人（如果用户提到了朋友/约人，设为 true）
- invitee_name：受邀人姓名（如果 need_email 为 true，必须）
- invitee_email：受邀人邮箱（如果 need_email 为 true，必须）

追问规则：
- 一次只追问 1-2 个最关键的缺失信息，不要一次问太多
- 用自然的中文对话
- 如果用户说"随便""都行"，可以给出默认建议然后继续

当所有必填槽位都齐了，调用 start_research 工具。
"""


DESTINATION_PROMPT = """你是目的地推荐 Agent。

你会收到一个城市名和用户偏好，你的任务是推荐 3-5 个适合的地点。

规则：
1. 调用工具搜索真实地点信息
2. 不要编造地址、评分、营业时间
3. 根据用户偏好（轻松/户外/美食/文化等）筛选
4. 输出结构化的 JSON 列表

输出格式：
[
  {"name": "西湖", "address": "杭州市西湖区", "category": "景点", "rating": 4.8},
  ...
]
"""


WEATHER_PROMPT = """你是天气评估 Agent。

你会收到城市名和日期，你的任务是评估天气是否适合出行。

输出格式（JSON）：
{
  "city": "杭州",
  "date": "2026-05-20",
  "weather": "多云",
  "temperature_high": 28,
  "temperature_low": 19,
  "suitable_outdoor": true,
  "suggestion": "天气适宜出行，建议携带防晒用品"
}

如果天气不适合户外，在 suggestion 中给出室内备选建议。
"""


TRANSPORT_PROMPT = """你是交通规划 Agent。

你会收到出发地和一组目的地，你的任务是规划地点之间的交通路线。

规则：
1. 估算每段路程的时间和费用
2. 判断一天内是否来得及走完
3. 如果太赶，建议删减地点

输出格式（JSON 列表）：
[
  {"from_place": "酒店", "to_place": "西湖", "method": "地铁", "duration_minutes": 25, "cost_cny": 5},
  ...
]
"""


BUDGET_PROMPT = """你是预算估算 Agent。

你会收到行程地点列表和人数，你的任务是估算总花费。

估算项目：
- 门票费用（根据景点类型估算）
- 餐饮费用（按人均估算）
- 交通费用（根据交通规划结果）
- 其他费用（如停车费、小费等）

输出格式（JSON）：
{
  "items": [
    {"category": "门票", "detail": "西湖免费", "cost_cny": 0},
    {"category": "餐饮", "detail": "午餐人均80x2人", "cost_cny": 160},
    ...
  ],
  "total_cny": 500,
  "within_budget": true,
  "suggestion": ""
}
"""


PLANNER_PROMPT = """你是行程规划 Agent。

你会收到以下信息：
- 目的地候选列表（来自 DestinationAgent）
- 天气评估（来自 WeatherAgent）
- 交通路线（来自 TransportAgent）
- 预算估算（来自 BudgetAgent）
- 用户的原始需求（城市、日期、偏好等）

你的任务：
1. 综合以上信息，生成一份可执行的一日游行程
2. 时间安排要合理（考虑交通时间、用餐时间、休息时间）
3. 如果天气不适合户外，优先安排室内活动
4. 如果超预算，调整方案

规则：
- 只使用 DestinationAgent 给出的真实地点，不要自己编造地点
- 生成 email_brief（一句话总结行程，用于邮件邀约）

输出格式（JSON）：
{
  "title": "杭州明日轻松一日游",
  "date": "2026-05-20",
  "origin": "杭州",
  "summary": "上午西湖漫步...",
  "itinerary": [
    {"start_time": "09:00", "end_time": "11:30", "activity": "西湖漫步", "place_name": "西湖", "transport": "步行", "cost_cny": 0},
    ...
  ],
  "estimated_cost_cny": 500,
  "weather_summary": "多云，适合户外",
  "transport_summary": "全程公共交通，约1.5小时",
  "email_brief": "明天一起去杭州西湖走走，然后去灵隐寺，晚上河坊街吃小吃"
}
"""


REVIEW_PROMPT = """你是行程审核 Agent。

你会收到一份 TravelPlan JSON，你的任务是检查这份行程是否可执行。

检查项：
1. 时间安排是否合理（不能凌晨出发、不能太晚结束、中间要有午餐时间）
2. 天气是否适合安排的活动（下雨天别安排户外徒步）
3. 预算是否超限
4. 如果需要发邮件(need_email=true)，是否有收件人邮箱
5. 行程是否过于紧凑（总交通时间不应超过总行程的40%）

输出格式（JSON）：
{
  "passed": true,
  "issues": [],
  "suggestions": ["建议多预留15分钟午餐时间"]
}

如果 passed=false，必须给出 issues 说明原因。
"""
```

### 4.4 创建 `app/agents/travel/router_agent.py`

```python
# app/agents/travel/router_agent.py

import json

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.travel.prompts import ROUTER_PROMPT


async def classify_intent(user_message: str) -> dict:
    """用 Router Agent 判断用户意图

    Args:
        user_message: 用户原始输入

    Returns:
        {"intent": "travel_plan_with_email", "confidence": 0.92}
    """
    router = create_agent(
        "deepseek-chat",
        tools=[],
        system_prompt=ROUTER_PROMPT,
    )

    response = await router.ainvoke({
        "messages": [HumanMessage(content=user_message)]
    })

    ai_message = response["messages"][-1].content

    # 尝试解析 JSON
    try:
        # 处理可能被 markdown 包裹的 JSON
        text = ai_message.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return result
    except (json.JSONDecodeError, IndexError):
        return {"intent": "chitchat", "confidence": 0.5}
```

### 4.5 测试 Router

在项目根目录创建一个测试脚本 `tests/test_router.py`，或者直接在终端运行：

```bash
uv run python -c "
import asyncio
from app.agents.travel.router_agent import classify_intent

async def main():
    # 测试 1：出行 + 约人
    r1 = await classify_intent('明天去哪玩？帮我约小王一起。')
    print('测试1:', r1)

    # 测试 2：只查天气
    r2 = await classify_intent('杭州明天天气怎么样？')
    print('测试2:', r2)

    # 测试 3：闲聊
    r3 = await classify_intent('你好')
    print('测试3:', r3)

asyncio.run(main())
"
```

预期输出类似：
```
测试1: {'intent': 'travel_plan_with_email', 'confidence': 0.95}
测试2: {'intent': 'weather_only', 'confidence': 0.9}
测试3: {'intent': 'chitchat', 'confidence': 0.85}
```

> **注意**：LLM 输出有随机性，confidence 数值可能不同，关键是 intent 判断正确。

---

## 第五章 Orchestrator Agent：槽位补全与调度

### 5.1 Orchestrator 的职责

Orchestrator 是"项目经理"。它做两件事：
1. **追问**：和用户对话，补齐缺失的槽位信息
2. **调度**：槽位齐全后，调用 tool 启动 Subagents 并行调研

> **「回顾」** 这和婚礼案例中 coordinator 用 `update_state` tool 收集信息是一样的思路。区别是我们把"收集信息"和"调度子 Agent"分成了两个明确的阶段。

### 5.2 定义 Tools

Orchestrator 需要两个 Tool：

1. `update_slots` — 把从用户对话中提取到的信息更新到 State
2. `start_research` — 槽位齐全后，启动并行调研

> **「回顾」** 第1节中你学到：在 Tool 中通过 `runtime: ToolRuntime` 访问 state，通过 `Command(update={...})` 修改 state。

```python
# app/agents/travel/orchestrator_agent.py

import asyncio
import json
from datetime import datetime, timedelta

from langchain.agents import create_agent
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.travel.prompts import ORCHESTRATOR_PROMPT
from app.agents.travel.state import TravelState


@tool
def update_slots(
    runtime: ToolRuntime,
    origin_city: str = "",
    travel_date: str = "",
    days: int = 0,
    people_count: int = 0,
    budget_cny: int = 0,
    preferences: str = "",
    need_email: bool = False,
    invitee_name: str = "",
    invitee_email: str = "",
) -> Command:
    """当从用户对话中获取到出行需求信息时，调用此工具更新状态。
    只传入你确定获取到的字段，其他字段留空。

    :arg origin_city: 出发城市
    :arg travel_date: 出行日期，格式 YYYY-MM-DD
    :arg days: 出行天数
    :arg people_count: 出行人数
    :arg budget_cny: 预算（元）
    :arg preferences: 偏好，逗号分隔，如 "轻松,美食"
    :arg need_email: 是否需要发邮件约人
    :arg invitee_name: 受邀人姓名
    :arg invitee_email: 受邀人邮箱
    """
    updates = {}

    if origin_city:
        updates["origin_city"] = origin_city
    if travel_date:
        updates["travel_date"] = travel_date
    if days > 0:
        updates["days"] = days
    if people_count > 0:
        updates["people_count"] = people_count
    if budget_cny > 0:
        updates["budget_cny"] = budget_cny
    if preferences:
        updates["preferences"] = [p.strip() for p in preferences.split(",")]
    if need_email:
        updates["need_email"] = True
    if invitee_name:
        updates["invitee_name"] = invitee_name
    if invitee_email:
        updates["invitee_email"] = invitee_email

    updates["phase"] = "collecting_slots"
    updates["messages"] = [
        ToolMessage(
            f"已更新以下信息：{json.dumps(updates, ensure_ascii=False)}",
            tool_call_id=runtime.tool_call_id,
        )
    ]

    return Command(update=updates)


@tool
def start_research(runtime: ToolRuntime) -> Command:
    """当所有必要的出行信息都已收集完毕时，调用此工具启动调研。
    调用前请确认至少有 origin_city 和 travel_date。
    """
    state = runtime.state
    origin = state.get("origin_city")
    date = state.get("travel_date")

    if not origin or not date:
        return Command(update={
            "messages": [ToolMessage(
                "还缺少必要信息（出发城市或日期），请继续和用户确认。",
                tool_call_id=runtime.tool_call_id,
            )]
        })

    return Command(update={
        "phase": "researching",
        "messages": [ToolMessage(
            "信息收集完毕，开始启动调研！",
            tool_call_id=runtime.tool_call_id,
        )],
    })


def create_orchestrator(checkpointer=None):
    """创建 Orchestrator Agent 实例"""
    return create_agent(
        "deepseek-chat",
        tools=[update_slots, start_research],
        state_schema=TravelState,
        checkpointer=checkpointer or InMemorySaver(),
        system_prompt=ORCHESTRATOR_PROMPT,
    )
```

### 5.3 测试多轮对话

```bash
uv run python -c "
import asyncio
from langchain_core.messages import HumanMessage
from app.agents.travel.orchestrator_agent import create_orchestrator

async def main():
    agent = create_orchestrator()
    config = {'configurable': {'thread_id': 'test-1'}}

    # 第1轮：用户提出模糊需求
    print('=== 第1轮 ===')
    r1 = await agent.ainvoke(
        {'messages': [HumanMessage('明天去哪玩？帮我约小王一起')]},
        config=config,
    )
    print(r1['messages'][-1].content)

    # 第2轮：用户补充信息
    print('\n=== 第2轮 ===')
    r2 = await agent.ainvoke(
        {'messages': [HumanMessage('从杭州出发，预算300，想轻松一点。小王邮箱是 xw@test.com')]},
        config=config,
    )
    print(r2['messages'][-1].content)

    # 检查 state
    state = await agent.aget_state(config)
    print('\n=== State ===')
    for key in ['origin_city', 'travel_date', 'budget_cny', 'need_email', 'invitee_email', 'phase']:
        print(f'  {key}: {state.values.get(key)}')

asyncio.run(main())
"
```

预期效果：
- 第1轮：AI 会追问"从哪个城市出发？预算大概多少？小王的邮箱是？"
- 第2轮：AI 提取信息后调用 `update_slots`，然后调用 `start_research`
- State 中能看到各个槽位已填充，phase 变为 `researching`

### 5.4 知识点：多轮对话和 checkpointer

> **「回顾」** 第3节 EmailAgent 案例中，你用 `InMemorySaver` 实现了多轮对话。同一个 `thread_id` 的多次 `invoke` 会共享同一个 state。

这里的 Orchestrator 也是一样。用户第一轮没说完的信息，第二轮补充后，state 里的数据是累积的。

后面正式上线时，我们会换成 `AsyncSqliteSaver`（和 EmailAgent 一样），这样服务重启后对话不会丢。

---

## 第六章 Subagents：并行调研四大维度

### 6.1 回顾：Subagent 模式的核心思路

> **「回顾」** 第5节婚礼案例中的做法：
> ```python
> @tool
> async def search_flights(runtime: ToolRuntime) -> str:
>     response = await travel_agent.ainvoke({...})
>     return response['messages'][-1].content
> ```
> 把子 Agent 包装成 Tool，主 Agent 调用这些 Tool 就等于调用了子 Agent。

我们也采用同样的模式。但先用 **mock 数据** 跑通流程，后面再接真实 MCP。

### 6.2 为什么先 mock

> **这是本教程最重要的开发原则之一。**

课程中婚礼案例能直接用 Kiwi MCP 和 Tavily，是因为它们是公开免费服务。

但我们的出行企划要用高德地图 MCP，你需要：
1. 申请高德 API Key
2. 安装配置 MCP Server
3. 确保网络通畅

如果一开始就依赖真实 MCP，任何一步出问题都会卡住整个开发。所以：

```
先用 mock → 跑通整个多 Agent 流程 → 再逐个替换为真实 MCP
```

### 6.3 创建 `app/agents/travel/subagents.py`

```python
# app/agents/travel/subagents.py

"""
四个调研 Subagent 的 mock 实现。

每个 Agent 目前返回 mock 数据。当接入真实 MCP 后，
只需要修改这个文件中的 tool 实现，不影响其他 Agent。
"""

import json

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import HumanMessage

from app.agents.travel.prompts import (
    BUDGET_PROMPT,
    DESTINATION_PROMPT,
    TRANSPORT_PROMPT,
    WEATHER_PROMPT,
)


# ============================================================
# 1. DestinationAgent — 目的地推荐
# ============================================================

@tool
def search_poi(city: str, preferences: str = "") -> str:
    """在指定城市搜索推荐的景点和餐厅。

    :arg city: 城市名称
    :arg preferences: 用户偏好，如 "轻松,美食"
    """
    # --- mock 数据，后续替换为高德 MCP 调用 ---
    mock_data = {
        "杭州": [
            {"name": "西湖", "address": "杭州市西湖区", "category": "景点", "rating": 4.8},
            {"name": "灵隐寺", "address": "杭州市西湖区灵隐路法云弄1号", "category": "景点", "rating": 4.7},
            {"name": "河坊街", "address": "杭州市上城区河坊街", "category": "美食街", "rating": 4.5},
            {"name": "太子湾公园", "address": "杭州市西湖区南山路", "category": "公园", "rating": 4.6},
            {"name": "知味观·味庄", "address": "杭州市西湖区杨公堤", "category": "餐饮", "rating": 4.4},
        ],
        "北京": [
            {"name": "故宫博物院", "address": "北京市东城区景山前街4号", "category": "景点", "rating": 4.9},
            {"name": "南锣鼓巷", "address": "北京市东城区南锣鼓巷", "category": "文化街区", "rating": 4.3},
            {"name": "颐和园", "address": "北京市海淀区新建宫门路19号", "category": "景点", "rating": 4.8},
            {"name": "簋街", "address": "北京市东城区东直门内大街", "category": "美食街", "rating": 4.2},
        ],
    }

    places = mock_data.get(city, [
        {"name": f"{city}中心广场", "address": f"{city}市中心", "category": "景点", "rating": 4.0},
        {"name": f"{city}美食街", "address": f"{city}老城区", "category": "美食街", "rating": 4.0},
    ])

    return json.dumps(places, ensure_ascii=False)


destination_agent = create_agent(
    "deepseek-chat",
    tools=[search_poi],
    system_prompt=DESTINATION_PROMPT,
)


# ============================================================
# 2. WeatherAgent — 天气评估
# ============================================================

@tool
def query_weather(city: str, date: str) -> str:
    """查询指定城市和日期的天气预报。

    :arg city: 城市名称
    :arg date: 日期，格式 YYYY-MM-DD
    """
    # --- mock 数据，后续替换为天气 MCP 调用 ---
    return json.dumps({
        "city": city,
        "date": date,
        "weather": "多云",
        "temperature_high": 28,
        "temperature_low": 19,
        "suitable_outdoor": True,
        "suggestion": "天气适宜出行，建议携带防晒用品和一把雨伞",
    }, ensure_ascii=False)


weather_agent = create_agent(
    "deepseek-chat",
    tools=[query_weather],
    system_prompt=WEATHER_PROMPT,
)


# ============================================================
# 3. TransportAgent — 交通规划
# ============================================================

@tool
def plan_route(origin: str, destinations: str) -> str:
    """规划从出发地到各个目的地之间的交通路线。

    :arg origin: 出发地点
    :arg destinations: 目的地列表，逗号分隔，如 "西湖,灵隐寺,河坊街"
    """
    # --- mock 数据，后续替换为高德 MCP 路线规划 ---
    places = [p.strip() for p in destinations.split(",")]
    routes = []
    prev = origin

    for place in places:
        routes.append({
            "from_place": prev,
            "to_place": place,
            "method": "公交/地铁",
            "duration_minutes": 25,
            "cost_cny": 5,
        })
        prev = place

    return json.dumps(routes, ensure_ascii=False)


transport_agent = create_agent(
    "deepseek-chat",
    tools=[plan_route],
    system_prompt=TRANSPORT_PROMPT,
)


# ============================================================
# 4. BudgetAgent — 预算估算
# ============================================================

@tool
def estimate_budget(places: str, people_count: int = 1) -> str:
    """根据行程地点和人数估算总预算。

    :arg places: 地点列表，逗号分隔
    :arg people_count: 出行人数
    """
    # --- mock 数据，后续可接入真实价格 API ---
    place_list = [p.strip() for p in places.split(",")]
    items = []
    total = 0

    for place in place_list:
        ticket = 0 if "湖" in place or "公园" in place or "街" in place else 50
        items.append({
            "category": "门票",
            "detail": f"{place} {'免费' if ticket == 0 else f'{ticket}元/人'}",
            "cost_cny": ticket * people_count,
        })
        total += ticket * people_count

    meal_cost = 80 * people_count * 2
    items.append({"category": "餐饮", "detail": f"午餐+晚餐 人均80x{people_count}人x2餐", "cost_cny": meal_cost})
    total += meal_cost

    transport_cost = 30 * people_count
    items.append({"category": "交通", "detail": f"全天公交/地铁 约{transport_cost}元", "cost_cny": transport_cost})
    total += transport_cost

    return json.dumps({
        "items": items,
        "total_cny": total,
        "within_budget": True,
        "suggestion": "",
    }, ensure_ascii=False)


budget_agent = create_agent(
    "deepseek-chat",
    tools=[estimate_budget],
    system_prompt=BUDGET_PROMPT,
)


# ============================================================
# 并行调用所有 Subagent
# ============================================================

async def run_all_subagents(
    city: str,
    date: str,
    preferences: str,
    people_count: int,
) -> dict:
    """并行调用四个 Subagent，返回各自的结果。

    这是 Subagents 模式的核心：四个独立任务同时执行，互不依赖。
    """
    pref_text = ", ".join(preferences) if isinstance(preferences, list) else preferences

    # 构造每个 subagent 的输入
    dest_input = {"messages": [HumanMessage(f"推荐{city}的景点和餐厅，偏好：{pref_text}")]}
    weather_input = {"messages": [HumanMessage(f"查询{city}在{date}的天气")]}
    transport_input = {"messages": [HumanMessage(f"规划{city}市内一日游的交通路线，出发地：{city}市中心")]}
    budget_input = {"messages": [HumanMessage(f"估算{city}一日游的预算，{people_count}人出行")]}

    # 并行执行！
    import asyncio
    dest_task = destination_agent.ainvoke(dest_input)
    weather_task = weather_agent.ainvoke(weather_input)
    transport_task = transport_agent.ainvoke(transport_input)
    budget_task = budget_agent.ainvoke(budget_input)

    results = await asyncio.gather(
        dest_task, weather_task, transport_task, budget_task
    )

    return {
        "destination": results[0]["messages"][-1].content,
        "weather": results[1]["messages"][-1].content,
        "transport": results[2]["messages"][-1].content,
        "budget": results[3]["messages"][-1].content,
    }
```

### 6.4 测试并行 Subagents

```bash
uv run python -c "
import asyncio
from app.agents.travel.subagents import run_all_subagents

async def main():
    results = await run_all_subagents(
        city='杭州',
        date='2026-05-20',
        preferences=['轻松', '美食'],
        people_count=2,
    )
    for name, result in results.items():
        print(f'\n=== {name} ===')
        print(result[:200] + '...' if len(result) > 200 else result)

asyncio.run(main())
"
```

> **知识点：`asyncio.gather`**
>
> 在婚礼案例中，三个 Subagent 是由 coordinator 的 LLM 自己决定同时调用三个 tool 来实现并行的（你在课程输出中可以看到 3 个 Tool Calls 同时出现）。
>
> 这里我们用 `asyncio.gather` 在代码层面显式并行，确保四个 Agent 一定同时启动，而不是依赖 LLM 自己"聪明地"并行调用。这样更可控。

---

## 第七章 Planner Agent：汇总生成行程

### 7.1 Planner 的输入

Planner 会收到四个 Subagent 的结果（作为上下文），然后生成最终行程。

它不需要 Tool，因为它的工作是"综合思考"，不是"调用外部服务"。但我们给它一个 `output_plan` tool 来确保输出格式正确。

### 7.2 创建 `app/agents/travel/planner_agent.py`

```python
# app/agents/travel/planner_agent.py

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.travel.prompts import PLANNER_PROMPT


planner_agent = create_agent(
    "deepseek-chat",
    tools=[],
    system_prompt=PLANNER_PROMPT,
)


async def generate_plan(
    origin: str,
    date: str,
    preferences: list[str],
    people_count: int,
    budget_cny: int | None,
    need_email: bool,
    invitee_name: str | None,
    destination_result: str,
    weather_result: str,
    transport_result: str,
    budget_result: str,
) -> str:
    """调用 Planner Agent 汇总所有 Subagent 结果，生成最终行程"""

    prompt = f"""请根据以下调研结果，生成一份完整的一日游行程方案。

## 用户需求
- 出发城市：{origin}
- 出行日期：{date}
- 偏好：{', '.join(preferences) if preferences else '无特别偏好'}
- 人数：{people_count}
- 预算：{f'{budget_cny}元' if budget_cny else '未指定'}
- 需要邮件邀约：{'是，邀请' + (invitee_name or '朋友') if need_email else '否'}

## 目的地调研结果
{destination_result}

## 天气调研结果
{weather_result}

## 交通调研结果
{transport_result}

## 预算调研结果
{budget_result}

请输出 JSON 格式的 TravelPlan。"""

    response = await planner_agent.ainvoke({
        "messages": [HumanMessage(content=prompt)]
    })

    return response["messages"][-1].content
```

### 7.3 测试

这一步依赖 Subagents 的结果，可以先用硬编码的 mock 结果测试：

```bash
uv run python -c "
import asyncio
from app.agents.travel.planner_agent import generate_plan

async def main():
    result = await generate_plan(
        origin='杭州',
        date='2026-05-20',
        preferences=['轻松', '美食'],
        people_count=2,
        budget_cny=300,
        need_email=True,
        invitee_name='小王',
        destination_result='[{\"name\":\"西湖\",\"category\":\"景点\"},{\"name\":\"河坊街\",\"category\":\"美食街\"}]',
        weather_result='{\"weather\":\"多云\",\"suitable_outdoor\":true}',
        transport_result='[{\"from\":\"市中心\",\"to\":\"西湖\",\"duration_minutes\":20}]',
        budget_result='{\"total_cny\":250,\"within_budget\":true}',
    )
    print(result)

asyncio.run(main())
"
```

---

## 第八章 Review Agent：审核行程可执行性

### 8.1 为什么需要 Review

LLM 生成的行程可能有逻辑问题：
- 下雨天安排了户外徒步
- 总交通时间超过 4 小时（太赶了）
- 需要发邮件但没有邮箱信息
- 凌晨 5 点出发（不合理）

Review Agent 就是"质检员"，在行程到达用户/邮件系统之前做最后一次检查。

### 8.2 创建 `app/agents/travel/review_agent.py`

```python
# app/agents/travel/review_agent.py

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agents.travel.prompts import REVIEW_PROMPT


review_agent = create_agent(
    "deepseek-chat",
    tools=[],
    system_prompt=REVIEW_PROMPT,
)


async def review_plan(
    travel_plan: str,
    need_email: bool = False,
    invitee_email: str | None = None,
) -> str:
    """调用 Review Agent 审核行程方案"""

    prompt = f"""请审核以下行程方案：

## 行程方案
{travel_plan}

## 额外信息
- 需要发邮件邀约：{'是' if need_email else '否'}
- 收件人邮箱：{invitee_email or '未提供'}

请输出 JSON 格式的审核结果。"""

    response = await review_agent.ainvoke({
        "messages": [HumanMessage(content=prompt)]
    })

    return response["messages"][-1].content
```

---

## 第九章 Handoff to EmailAgent：邮件邀约闭环

### 9.1 什么是 Handoff

> **「回顾」** 第5节：Handoffs 模式是"随着任务执行改变 state 中的任务状态，从而切换到其它 Agent"。

在本项目中，Handoff 发生在：

```
Review Agent 审核通过
    ↓ need_email = True
EmailAgent 接管
    ↓ 生成邮件草稿
    ↓ HITL 中断，等待用户确认
    ↓ 用户确认后发送
```

### 9.2 Handoff 的关键：结构化数据传递

Handoff 不是简单地"转发消息"。Travel 系统需要给 EmailAgent 传递结构化的邮件信息：

```python
# 要传给 EmailAgent 的内容
email_input = {
    "to": "xiaowang@test.com",
    "subject": "明天一起去杭州玩？",
    "body_brief": "根据以下行程写一封自然的邀约邮件：上午西湖，下午河坊街...",
}
```

### 9.3 如何复用现有 EmailAgent

看你项目中的 `email_agent.py`，EmailAgent 的调用入口是：

```python
email_agent.generate_sse(thread_id, message, interrupt_decision)
```

所以 Handoff 就是：构造一段自然语言消息，让 EmailAgent 根据这段消息来发邮件。

```python
# app/agents/travel/handoff.py

from app.agents.email.email_agent import email_agent


async def handoff_to_email(
        thread_id: str,
        to_email: str,
        to_name: str,
        email_brief: str,
        travel_plan_summary: str,
):
    """将行程方案 handoff 给 EmailAgent，生成邀约邮件。

    注意：这个函数返回的是 SSE 事件流（async generator），
    需要在 API 层用 EventSourceResponse 消费。
    """
    message = f"""请帮我给 {to_name}（{to_email}）发一封邮件，邀请 ta 明天一起出行。

邮件主题：明天一起{email_brief}

行程概要：
{travel_plan_summary}

请写一封自然、友好的中文邀约邮件，不要太正式。"""

    # 复用 EmailAgent 的 SSE 流
    async for event in email_agent.generate_sse(
            thread_id=f"travel-email-{thread_id}",
            message=message,
            interrupt_decision=None,
    ):
        yield event
```

### 9.4 理解 HITL 在 Handoff 中的作用

> **「回顾」** 第2节中你学到 `HumanInTheLoopMiddleware`：在执行 `send_gmail_message` 之前会触发 interrupt，等待用户确认。

EmailAgent 已经内置了 HITL。所以 Handoff 之后：

1. EmailAgent 生成邮件草稿 → 返回给前端显示
2. 前端弹出确认框："确认发送这封邮件？"
3. 用户点击确认 → 前端发送 `interrupt_decision: {"action": "approve"}`
4. EmailAgent 继续执行 `send_gmail_message`

**Travel 系统不需要管这些，全部由 EmailAgent 内部处理。** 这就是 Handoff 的好处：职责清晰。

### 9.5 测试（不发真实邮件）

由于 EmailAgent 依赖 Gmail OAuth（`credentials.json`），如果你还没配置，可以先跳过此步。

如果已配置，可以这样测试：

```bash
uv run python -c "
import asyncio
from app.agents.travel.handoff import handoff_to_email

async def main():
    async for event in handoff_to_email(
        thread_id='test-001',
        to_email='test@example.com',
        to_name='小王',
        email_brief='去杭州西湖走走',
        travel_plan_summary='上午9点西湖漫步，中午知味观午餐，下午河坊街逛逛',
    ):
        print(event)

asyncio.run(main())
"
```

---

## 第十章 接入真实 MCP：高德地图

> **「回顾」** 第4节中你学了两种 MCP 连接方式：
> - **stdio**：本地脚本，如 Time MCP（`uvx mcp-server-time`）
> - **http**：远程服务，如 Kiwi MCP（`https://mcp.kiwi.com`）

高德 MCP 使用的是 **stdio** 方式。

### 10.1 前置准备

1. **申请高德 API Key**：
   - 访问 https://console.amap.com/
   - 注册/登录 → 创建应用 → 获取 Web服务 API Key

2. **配置环境变量**：
   在项目根目录的 `.env` 文件中添加：
   ```
   AMAP_MAPS_API_KEY=你的高德key
   ```

3. **确保 npx 可用**：
   ```bash
   npx --version
   ```
   如果没有 npx，安装 Node.js：`brew install node`

### 10.2 连接高德 MCP

> **「回顾」** 第4节中连接外部 MCP 的方式：
> ```python
> client = MultiServerMCPClient({
>     "time": {
>         "transport": "stdio",
>         "command": "uvx",
>         "args": ["mcp-server-time"]
>     }
> })
> tools = await client.get_tools()
> ```

高德 MCP 的连接方式：

```python
# app/integrations/mcp/amap_tools.py

import os

from langchain_mcp_adapters.client import MultiServerMCPClient


async def get_amap_client():
    """创建高德地图 MCP 客户端"""
    api_key = os.getenv("AMAP_MAPS_API_KEY")
    if not api_key:
        raise ValueError("请在 .env 中设置 AMAP_MAPS_API_KEY")

    client = MultiServerMCPClient({
        "amap": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "env": {
                "AMAP_MAPS_API_KEY": api_key,
            },
        }
    })

    return client


async def get_amap_tools():
    """获取高德 MCP 提供的工具列表"""
    client = await get_amap_client()
    tools = await client.get_tools()
    return tools
```

### 10.3 查看高德 MCP 提供的工具

先在 Jupyter 或脚本中测试：

```python
import asyncio
from app.integrations.mcp.amap_tools import get_amap_tools

async def main():
    tools = await get_amap_tools()
    for tool in tools:
        print(f"工具名: {tool.name}")
        print(f"描述: {tool.description}")
        print("---")

asyncio.run(main())
```

高德 MCP 通常提供这些工具：
- `maps_text_search` — POI 关键词搜索
- `maps_around_search` — 周边搜索
- `maps_search_detail` — POI 详情查询
- `maps_direction` — 路线规划（可能有）

### 10.4 替换 DestinationAgent 的 mock 工具

确认拿到工具后，修改 `subagents.py` 中 DestinationAgent 的工具。

**替换前（mock）**：
```python
destination_agent = create_agent(
    "deepseek-chat",
    tools=[search_poi],  # ← mock 工具
    system_prompt=DESTINATION_PROMPT,
)
```

**替换后（真实 MCP）**：
```python
# 需要在异步上下文中创建
async def create_destination_agent_with_mcp():
    from app.integrations.mcp.amap_tools import get_amap_tools
    amap_tools = await get_amap_tools()
    return create_agent(
        "deepseek-chat",
        tools=amap_tools,
        system_prompt=DESTINATION_PROMPT,
    )
```

> **注意**：MCP 工具是异步获取的，所以创建 Agent 也要在异步上下文中完成。这和课程中直接 `tools = await client.get_tools()` 是一样的道理。

### 10.5 同理替换 TransportAgent

如果高德 MCP 提供了路线规划工具（`maps_direction`），也可以替换 TransportAgent 的 mock 工具。

### 10.6 关于天气 MCP

截至目前，天气数据有几种获取方式：
- 高德地图的天气接口（如果 MCP 提供了天气工具）
- 自定义 MCP Server（用 FastMCP 自己写一个，参考第4节第3部分）
- 暂时保持 mock

**建议**：第一版先保持天气和预算的 mock，优先把高德 POI 搜索接进去。

---

## 第十一章 FastAPI 接口与 SSE 流式输出

### 11.1 回顾：EmailAgent 的 API 模式

> **「回顾」** 第3节 EmailAgent 的 API 是这样的：
> ```python
> @router.post("/chat/send")
> async def send_chat(request: ChatRequest):
>     return EventSourceResponse(email_agent.generate_sse(...))
> ```

出行企划的 API 也采用同样的模式，但流程更复杂：Router → Orchestrator → Subagents → Planner → Review → (可选) EmailAgent。

### 11.2 完整流程编排

创建 `app/agents/travel/pipeline.py`，把所有 Agent 串起来：

```python
# app/agents/travel/pipeline.py

"""
出行企划的完整流程编排。

这个文件是整个多 Agent 系统的"总控"。它按照以下流程编排各个 Agent：
Router → Orchestrator（多轮对话） → Subagents（并行） → Planner → Review → (Handoff) EmailAgent

关键设计：pipeline 需要处理三种请求：
1. 普通对话（收集信息 / 闲聊）
2. 信息齐全后触发完整规划流程
3. 用户确认邮件发送（interrupt_decision）
"""

import json

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.travel.handoff import handoff_to_email
from app.agents.travel.orchestrator_agent import create_orchestrator
from app.agents.travel.planner_agent import generate_plan
from app.agents.travel.review_agent import review_plan
from app.agents.travel.router_agent import classify_intent
from app.agents.travel.subagents import run_all_subagents

# Orchestrator 需要 checkpointer 来支持多轮对话
_checkpointer = InMemorySaver()
_orchestrator = create_orchestrator(checkpointer=_checkpointer)


async def travel_pipeline_sse(
        thread_id: str,
        message: str,
        user_id: str = "",
        interrupt_decision: dict | None = None,
):
    """出行企划 SSE 流，供 FastAPI 端点调用。

    Args:
        thread_id: 会话 ID
        message: 用户消息
        user_id: 用户 ID
        interrupt_decision: 邮件 HITL 确认决定（来自前端）

    Yields:
        dict: SSE 事件 {"event": "...", "data": "..."}
    """
    config = {"configurable": {"thread_id": thread_id}}

    # ---- 分支 A: 用户确认/拒绝邮件发送（HITL 回调） ----
    # 当前端传入 interrupt_decision 时，说明用户在响应 EmailAgent 的 HITL 中断。
    # 此时不走 Router/Orchestrator，直接把决定转发给 EmailAgent。
    if interrupt_decision:
        yield _sse("status", "正在处理邮件确认...")
        async for event in handoff_to_email_resume(
                thread_id=thread_id,
                interrupt_decision=interrupt_decision,
        ):
            yield event
        yield _sse("done", "处理完成")
        return

    # ---- 分支 B: 正常对话流程 ----

    # ---- Step 1: Router 判断意图 ----
    intent_result = await classify_intent(message)
    intent = intent_result.get("intent", "chitchat")

    yield _sse("status", f"意图识别：{intent}")

    if intent == "chitchat":
        yield _sse("message", "你好！我是出行企划助手。告诉我你想去哪玩，我来帮你规划行程！")
        yield _sse("done", "处理完成")
        return

    if intent == "email_only":
        yield _sse("message", "看起来你想发邮件。请使用邮件助手功能。")
        yield _sse("done", "处理完成")
        return

    # ---- Step 2: Orchestrator 收集/补全信息 ----
    response = await _orchestrator.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )

    ai_reply = response["messages"][-1].content
    state = (await _orchestrator.aget_state(config)).values
    phase = state.get("phase", "collecting_slots")

    # 如果还在收集信息阶段，返回 AI 的追问
    if phase != "researching":
        yield _sse("message", ai_reply)
        yield _sse("done", "处理完成")
        return

    # ---- Step 3: 信息齐全，启动并行调研 ----
    yield _sse("status", "正在为你调研目的地、天气、交通和预算...")

    city = state.get("origin_city", "")
    date = state.get("travel_date", "")
    preferences = state.get("preferences", [])
    people_count = state.get("people_count", 1)

    research_results = await run_all_subagents(
        city=city,
        date=date,
        preferences=preferences,
        people_count=people_count,
    )

    yield _sse("status", "调研完成，正在生成行程方案...")

    # ---- Step 4: Planner 生成行程 ----
    plan = await generate_plan(
        origin=city,
        date=date,
        preferences=preferences,
        people_count=people_count,
        budget_cny=state.get("budget_cny"),
        need_email=state.get("need_email", False),
        invitee_name=state.get("invitee_name"),
        destination_result=research_results["destination"],
        weather_result=research_results["weather"],
        transport_result=research_results["transport"],
        budget_result=research_results["budget"],
    )

    # ---- Step 5: Review 审核 ----
    yield _sse("status", "正在审核行程...")
    review = await review_plan(
        travel_plan=plan,
        need_email=state.get("need_email", False),
        invitee_email=state.get("invitee_email"),
    )

    # 返回行程方案和审核结果
    yield _sse("plan", plan)
    yield _sse("review", review)

    # ---- Step 6: 如果需要邮件，Handoff 到 EmailAgent ----
    if state.get("need_email") and state.get("invitee_email"):
        yield _sse("status", "正在生成邮件草稿...")

        # 真正调用 EmailAgent！不只是发个信号
        async for event in handoff_to_email(
                thread_id=thread_id,
                to_email=state.get("invitee_email", ""),
                to_name=state.get("invitee_name", "朋友"),
                email_brief=plan[:200],
                travel_plan_summary=plan[:500],
        ):
            yield event
            # 注意：如果 EmailAgent 返回了 interrupt 事件（HITL），
            # SSE 流会把它传给前端。前端收到 interrupt 后应弹出确认框，
            # 用户确认后再次调用 /travel/send 并带上 interrupt_decision。
    else:
        yield _sse("done", "处理完成")


async def handoff_to_email_resume(
        thread_id: str,
        interrupt_decision: dict,
):
    """处理邮件 HITL 确认：将用户的确认/拒绝决定转发给 EmailAgent。

    「回顾」这和 EmailAgent 的 chat.py 中处理 interrupt_decision 的逻辑一样：
    email_agent.generate_sse(thread_id, message="", interrupt_decision=decision)
    """
    from app.agents.email.email_agent import email_agent

    async for event in email_agent.generate_sse(
            thread_id=f"travel-email-{thread_id}",
            message="",
            interrupt_decision=interrupt_decision,
    ):
        yield event


def _sse(event_type: str, content: str) -> dict:
    """构造 SSE 事件"""
    return {
        "event": event_type,
        "data": json.dumps(
            {"type": event_type, "content": content},
            ensure_ascii=False,
        ),
    }
```

> **关键改动说明**：
>
> 对比之前的断裂版本，这里修复了三个问题：
>
> 1. **Step 6 真正调用了 `handoff_to_email`**，而不是只发一个 `email_ready` 信号。EmailAgent 的 SSE 事件（包括 message、interrupt、done）会透传给前端。
>
> 2. **新增了 `interrupt_decision` 参数和分支 A**。当用户确认/拒绝邮件后，前端带着 `interrupt_decision` 再次调用 `/travel/send`，pipeline 识别到这个参数后直接走 `handoff_to_email_resume`，跳过 Router/Orchestrator，把决定转发给 EmailAgent。
>
> 3. **`handoff_to_email_resume` 复用了 EmailAgent 的 `generate_sse`**，传入 `interrupt_decision` 让 EmailAgent 从中断处继续执行（发送邮件或取消）。这和你在第3节课程中学的 `chat.py` 里处理 `interrupt_decision` 的方式完全一致。

### 11.3 修改 `app/api/v1/travel.py`

```python
# app/api/v1/travel.py

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.agents.travel.pipeline import travel_pipeline_sse


router = APIRouter()


class TravelChatRequest(BaseModel):
    message: str = ""
    thread_id: str = "default"
    user_id: str | None = None
    interrupt_decision: Optional[Dict[str, Any]] = None


@router.post("/travel/send", tags=["出行企划 Agent"])
async def send_travel(request: TravelChatRequest):
    """出行企划 SSE 端点。

    支持两种调用场景：
    1. 普通对话：传 message，进入 Router → Orchestrator → ... 流程
    2. 邮件确认：传 interrupt_decision，跳过规划流程，直接回调 EmailAgent

    「回顾」和 chat.py 中 EmailAgent 的 send_chat 端点对比：
    chat.py 也接受 interrupt_decision，用于 HITL 邮件确认。
    这里的区别是 travel.py 会先判断 interrupt_decision 是否存在，
    如果存在就走 Handoff 回调路径，否则走完整的出行规划流程。
    """
    return EventSourceResponse(
        travel_pipeline_sse(
            thread_id=request.thread_id,
            message=request.message or "",
            user_id=request.user_id or "",
            interrupt_decision=request.interrupt_decision,
        )
    )
```

> **对比 `chat.py`**：
>
> ```python
> # chat.py（已有的 EmailAgent 端点）
> class ChatRequest(BaseModel):
>     message: Optional[str] = None
>     thread_id: str
>     interrupt_decision: Optional[Dict[str, Any]] = None  # ← 同样的字段
> ```
>
> `travel.py` 的 `TravelChatRequest` 也加了 `interrupt_decision`，结构完全一致。
> 前端在收到 `interrupt` 事件后，用同样的方式发送确认请求：
>
> ```json
> POST /api/v1/travel/send
> {
>   "thread_id": "demo-001",
>   "interrupt_decision": {"action": "approve"}
> }
> ```

### 11.4 测试 API

启动服务：
```bash
uv run python -m app.main
```

用 curl 测试完整的三步闭环（或使用 Postman / Swagger UI）：

```bash
# 第1轮：模糊需求 → 系统追问
curl -X POST http://127.0.0.1:8002/api/v1/travel/send \
  -H "Content-Type: application/json" \
  -d '{"message": "明天去哪玩？帮我约小王一起", "thread_id": "demo-001"}'
# 预期：收到 message 事件，AI 追问城市/预算/邮箱

# 第2轮：补充信息 → 触发完整规划 + EmailAgent 生成草稿
curl -X POST http://127.0.0.1:8002/api/v1/travel/send \
  -H "Content-Type: application/json" \
  -d '{"message": "杭州出发，预算300，轻松点。小王邮箱 xw@test.com", "thread_id": "demo-001"}'
# 预期：依次收到 status → plan → review → EmailAgent 的 message/interrupt 事件
# 当收到 interrupt 事件时，说明 EmailAgent 在等待你确认邮件发送

# 第3轮：确认发送邮件 → EmailAgent 执行发送
curl -X POST http://127.0.0.1:8002/api/v1/travel/send \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "demo-001", "interrupt_decision": {"action": "approve"}}'
# 预期：收到 EmailAgent 的 message 事件（邮件已发送）+ done 事件
```

> **完整闭环说明**：
>
> ```
> 第1轮 → Router 识别意图 → Orchestrator 追问 → 返回追问消息
> 第2轮 → Orchestrator 补齐槽位 → Subagents 并行调研 → Planner 生成行程
>       → Review 审核 → Handoff 到 EmailAgent → EmailAgent 生成草稿
>       → HITL 中断（interrupt 事件发给前端）
> 第3轮 → 前端带 interrupt_decision 回调 → pipeline 识别到 interrupt_decision
>       → 跳过 Router/Orchestrator → 直接调用 handoff_to_email_resume
>       → EmailAgent 从中断处继续 → 真正发送邮件 → done
> ```
>
> 这就是 **Router + Orchestrator + Subagents + Handoff** 四种模式协同工作的完整闭环。

---

## 第十二章 完整测试与验收

### 12.1 验收对话脚本

以下对话应该能跑通：

```
===== 第 1 轮请求 =====
POST /api/v1/travel/send
{"message": "明天去哪玩？帮我约小王一起", "thread_id": "demo-001"}

→ event: status    → 意图识别：travel_plan_with_email
→ event: message   → 好的！请问你从哪个城市出发？预算大概多少？小王的邮箱是？
→ event: done

===== 第 2 轮请求 =====
POST /api/v1/travel/send
{"message": "杭州，预算300，轻松一点。小王邮箱 xw@test.com", "thread_id": "demo-001"}

→ event: status    → 正在为你调研目的地、天气、交通和预算...
→ event: status    → 调研完成，正在生成行程方案...
→ event: plan      → {"title":"杭州明日轻松一日游", "itinerary": [...]}
→ event: review    → {"passed": true}
→ event: status    → 正在生成邮件草稿...
→ event: message   → 邮件草稿：嗨小王，明天想约你一起去杭州玩...
→ event: interrupt → {"reason": "需要人工确认", "details": [...]}   ← HITL！

===== 第 3 轮请求（确认邮件）=====
POST /api/v1/travel/send
{"thread_id": "demo-001", "interrupt_decision": {"action": "approve"}}

→ event: status    → 正在处理邮件确认...
→ event: message   → 邮件已发送至 xw@test.com
→ event: done
```

> **第 3 轮是关键**：前端收到 `interrupt` 事件后弹出确认框，用户点确认后，
> 前端带着 `interrupt_decision` 再次调用同一个 `/travel/send` 端点。
> Pipeline 看到 `interrupt_decision` 不为空，直接走 HITL 回调路径（分支 A），
> 不再经过 Router/Orchestrator，直接恢复 EmailAgent 的执行。

### 12.2 最小验收清单

| 验收项 | 如何验证 |
|--------|---------|
| Router 能正确识别意图 | 分别输入"明天去玩""查天气""你好"，检查 intent |
| Orchestrator 能追问缺失信息 | 只说"去玩"不说城市，检查是否追问 |
| Orchestrator 能提取多轮信息 | 分两轮提供城市和预算，检查 state 是否累积 |
| Subagents 能并行返回结果 | 查看日志或输出，4 个结果应同时返回 |
| Planner 能生成结构化行程 | 输出应包含时间、地点、费用 |
| Review 能检查问题 | 故意漏掉邮箱，检查是否报 issue |
| **Handoff 真正调用 EmailAgent** | **need_email=true 时收到 EmailAgent 的 message + interrupt 事件** |
| **HITL 邮件确认闭环** | **第 3 轮带 interrupt_decision 请求后收到"邮件已发送"** |
| 不影响已有 EmailAgent | 直接访问 `/api/v1/chat/send` 应正常工作 |

### 12.3 分步排查

如果某一步出问题，可以单独测试那个 Agent。每个 Agent 都有独立的测试方法（见各章节末尾的"测试"部分）。

**调试技巧**：在 `pipeline.py` 中加日志：
```python
from app.common.logger import logger

logger.info(f"Router 结果: {intent_result}")
logger.info(f"Orchestrator state: {state}")
logger.info(f"Subagent 结果: {research_results.keys()}")
```

---

## 附录 A 完整目录结构

```
app/
├── __init__.py
├── main.py                          # FastAPI 入口，挂载所有 router
├── agents/
│   ├── __init__.py
│   ├── email_agent.py               # [已有] EmailAgent
│   └── travel/
│       ├── __init__.py
│       ├── state.py                 # 第三章：自定义 TravelState
│       ├── prompts.py               # 第四章：所有 Agent 的 system prompt
│       ├── router_agent.py          # 第四章：意图路由
│       ├── orchestrator_agent.py    # 第五章：槽位收集 + 调度
│       ├── subagents.py             # 第六章：4 个调研 Subagent (mock)
│       ├── planner_agent.py         # 第七章：行程规划
│       ├── review_agent.py          # 第八章：行程审核
│       ├── handoff.py               # 第九章：Handoff 到 EmailAgent
│       └── pipeline.py              # 第十一章：完整流程编排
├── api/
│   └── v1/
│       ├── __init__.py
│       ├── chat.py                  # [已有] EmailAgent SSE 接口
│       └── travel.py                # 第十一章：出行企划 SSE 接口
├── common/
│   └── logger.py                    # [已有] 日志
├── integrations/
│   ├── __init__.py
│   ├── gmail_auth.py                # [已有]
│   ├── gmail_tools.py               # [已有]
│   └── mcp/
│       ├── __init__.py
│       └── amap_tools.py            # 第十章：高德 MCP 连接
├── models/
│   ├── __init__.py
│   ├── schemas.py                   # [已有] ChatRequest
│   └── travel.py                    # 第二章：出行数据结构
├── db/                              # SQLite 数据库文件
└── static/                          # [已有] 前端静态文件
```

## 附录 B 常见问题

### Q1: `create_agent` 报错找不到模型

确保 `.env` 中配置了模型 API Key。如果用 DeepSeek：
```
DEEPSEEK_API_KEY=your_key
```

或者在 `pyproject.toml` 确认依赖 `langchain-deepseek` 已安装。

### Q2: MCP 工具获取超时

stdio 模式下，MCP Client 会下载并启动一个子进程。首次运行需要下载包，网络不好会超时。

解决：
- 确保 `npx` 或 `uvx` 命令可用
- 预先安装：`npx -y @amap/amap-maps-mcp-server` 手动下载一次
- 检查网络代理设置

### Q3: Subagent 并行时偶尔报错

`asyncio.gather` 默认会在任一任务报错时取消所有任务。可以加 `return_exceptions=True`：

```python
results = await asyncio.gather(
    dest_task, weather_task, transport_task, budget_task,
    return_exceptions=True,
)
```

然后在结果中检查是否有 Exception，对报错的 Agent 使用 mock 数据兜底。

### Q4: Orchestrator 一直追问不停

可能是 system prompt 写得不够明确。检查两点：
1. `start_research` 工具的 docstring 是否清晰描述了调用条件
2. prompt 中是否明确写了"当 origin_city 和 travel_date 都有了，就调用 start_research"

### Q5: EmailAgent 初始化失败

确认有 `credentials.json` 或 `token.json`。如果没有 Gmail OAuth，`main.py` 中的 lifespan 会跳过 EmailAgent 初始化。

这种情况下 Handoff 到 EmailAgent 会报错。解决：
1. 先配置 Gmail OAuth（参考第3节课程的邮箱认证部分）
2. 或者先跳过邮件功能，只测试行程规划部分

### Q6: 数据结构定义了但 Agent 不用

Agent 的 system prompt 中要明确要求"输出 JSON 格式"，并给出示例。LLM 不会自动读取你的 Pydantic Model 定义，它只看 prompt。

后续如果想强制 JSON 格式，可以在 prompt 中加：
```
严格按照以下 JSON schema 输出，不要包含任何其他内容：
{"title": "string", "date": "string", ...}
```

---

## 推荐开发顺序

按照本教程的章节顺序，一步一步来：

```
 第二章  → 写 app/models/travel.py              → 验证 import
 第三章  → 写 app/agents/travel/state.py         → 验证 import
 第四章  → 写 prompts.py + router_agent.py       → 测试意图分类
 第五章  → 写 orchestrator_agent.py              → 测试多轮对话
 第六章  → 写 subagents.py (mock)                → 测试并行调研
 第七章  → 写 planner_agent.py                   → 测试行程生成
 第八章  → 写 review_agent.py                    → 测试审核
 第九章  → 写 handoff.py                         → (如有 Gmail OAuth) 测试邮件
 第十章  → 写 amap_tools.py                      → 替换 mock，测试真实 POI
 第十一章 → 写 pipeline.py + 修改 travel.py       → 端到端测试
```

**每写完一个文件，先单独测试，通过后再写下一个。** 不要一次写完所有文件再测试——这是 Codex 文档里也强调的原则，也是最容易被忽视的原则。
