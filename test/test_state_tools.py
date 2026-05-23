"""测试 HangoutState + Command 工具链。

运行方式：
    uv run python -m test.test_state_tools

不需要 Redis / MCP，纯本地验证。
"""

import asyncio
import sys
from langgraph.types import Command
from langchain_core.messages import ToolMessage


# ═══════════════════════════════════════
# 1. HangoutState 定义
# ═══════════════════════════════════════

def test_hangout_state():
    from app.agents.hangout.tools import HangoutState

    # 默认值
    annotations = {
        k: v for k, v in HangoutState.__annotations__.items()
        if k not in ("messages", "remaining_steps")
    }
    print(f"[1] HangoutState 字段 ({len(annotations)} 个):")
    for name, typ in annotations.items():
        print(f"    {name}: {typ}")

    assert "destination" in annotations
    assert "weather_checked" in annotations
    assert "plan_saved" in annotations
    print("    ✅ HangoutState 定义正确\n")


# ═══════════════════════════════════════
# 2. update_trip_info → Command
# ═══════════════════════════════════════

def test_update_trip_info():
    from app.agents.hangout.tools import update_trip_info

    # 验证 LLM 看不到 tool_call_id
    assert "tool_call_id" not in update_trip_info.args, \
        "tool_call_id 泄露到 LLM schema!"
    print("[2] update_trip_info:")
    print(f"    LLM 可见参数: {list(update_trip_info.args.keys())}")

    # 模拟调用
    result = update_trip_info.invoke({
        "args": {"destination": "西湖", "date": "周六"},
        "name": "update_trip_info",
        "type": "tool_call",
        "id": "call_001",
    })

    assert isinstance(result, Command), f"期望 Command，得到 {type(result)}"
    assert result.update["destination"] == "西湖"
    assert result.update["date"] == "周六"
    assert "origin" not in result.update, "空字段不应出现在 update 中"

    msg = result.update["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_001"
    assert "西湖" in msg.content
    print(f"    ToolMessage: {msg.content}")
    print("    ✅ 返回 Command + ToolMessage 正确\n")


# ═══════════════════════════════════════
# 3. mark_weather_result → Command
# ═══════════════════════════════════════

def test_mark_weather_result():
    from app.agents.hangout.tools import mark_weather_result

    assert "tool_call_id" not in mark_weather_result.args
    print("[3] mark_weather_result:")

    # 天气好
    result = mark_weather_result.invoke({
        "args": {"weather_summary": "晴，26°C，微风", "weather_ok": True},
        "name": "mark_weather_result",
        "type": "tool_call",
        "id": "call_002",
    })
    assert result.update["weather_checked"] is True
    assert result.update["weather_ok"] is True
    assert result.update["weather_summary"] == "晴，26°C，微风"
    print(f"    天气好: {result.update['messages'][0].content}")

    # 天气差
    result2 = mark_weather_result.invoke({
        "args": {"weather_summary": "中雨，18°C", "weather_ok": False},
        "name": "mark_weather_result",
        "type": "tool_call",
        "id": "call_003",
    })
    assert result2.update["weather_ok"] is False
    print(f"    天气差: {result2.update['messages'][0].content}")
    print("    ✅ 天气标记正确\n")


# ═══════════════════════════════════════
# 4. mark_trip_type → Command
# ═══════════════════════════════════════

def test_mark_trip_type():
    from app.agents.hangout.tools import mark_trip_type

    print("[4] mark_trip_type:")
    for tt in ("same_city", "cross_city"):
        result = mark_trip_type.invoke({
            "args": {"trip_type": tt},
            "name": "mark_trip_type",
            "type": "tool_call",
            "id": f"call_{tt}",
        })
        assert result.update["trip_type"] == tt
        print(f"    {tt}: {result.update['messages'][0].content}")
    print("    ✅ 类型标记正确\n")


# ═══════════════════════════════════════
# 5. save_final_plan → Command + Store
# ═══════════════════════════════════════

def test_save_final_plan():
    from app.agents.hangout.tools import save_final_plan, set_store
    from langgraph.store.memory import InMemoryStore

    print("[5] save_final_plan:")

    store = InMemoryStore()
    set_store(store)

    result = asyncio.run(save_final_plan.ainvoke({
        "args": {"plan": "周六去西湖，晴天，公交 40 分钟"},
        "name": "save_final_plan",
        "type": "tool_call",
        "id": "call_save",
    }, config={"configurable": {"user_id": "test", "thread_id": "t1"}}))

    assert isinstance(result, Command)
    assert result.update["plan_saved"] is True
    print(f"    Command: plan_saved={result.update['plan_saved']}")
    print(f"    ToolMessage: {result.update['messages'][0].content}")

    # 验证 Store 里有数据
    items = asyncio.run(store.asearch(("travel_plan", "test", "t1")))
    assert len(items) > 0
    assert items[0].value["plan"] == "周六去西湖，晴天，公交 40 分钟"
    print(f"    Store 验证: ✅ 方案已持久化")

    # 清理
    set_store(None)
    print("    ✅ save_final_plan 正确\n")


# ═══════════════════════════════════════
# 6. get_final_plan 从 Store 读
# ═══════════════════════════════════════

def test_get_final_plan():
    from app.agents.hangout.tools import get_final_plan, save_final_plan, set_store
    from langgraph.store.memory import InMemoryStore

    print("[6] get_final_plan:")

    store = InMemoryStore()
    set_store(store)
    config = {"configurable": {"user_id": "test", "thread_id": "t2"}}

    # 没有方案时
    result = asyncio.run(get_final_plan.ainvoke({}, config=config))
    assert "暂无" in result
    print(f"    空读取: {result}")

    # 存一个方案
    asyncio.run(save_final_plan.ainvoke({
        "args": {"plan": "测试方案内容"},
        "name": "save_final_plan",
        "type": "tool_call",
        "id": "call_s2",
    }, config=config))

    # 再读
    result2 = asyncio.run(get_final_plan.ainvoke({}, config=config))
    assert result2 == "测试方案内容"
    print(f"    有方案: {result2}")

    set_store(None)
    print("    ✅ get_final_plan 正确\n")


# ═══════════════════════════════════════
# 7. prompt 函数状态注入 + 阶段提示
# ═══════════════════════════════════════

def test_prompt_function():
    from app.agents.hangout.orchestrator import _build_dynamic_prompt

    print("[7] prompt 函数动态注入:")

    # 场景 A：空状态 — 不应有动态注入的信息行（"- 目的地：" 等）
    p = _build_dynamic_prompt({})
    assert "- 目的地：" not in p, "空状态不应注入目的地"
    assert "⚠️ 阶段提示" not in p
    print("    空状态: ✅ 无动态注入内容")

    # 场景 B：有目的地 + 日期，未查天气
    p = _build_dynamic_prompt({"destination": "西湖", "date": "周六"})
    assert "目的地：西湖" in p
    assert "日期：周六" in p
    assert "必须" in p and "weather_expert" in p
    print("    有目的地+日期: ✅ 注入字段 + 阶段提示'必须查天气'")

    # 场景 C：天气已查，适合出行，缺出发地
    p = _build_dynamic_prompt({
        "destination": "西湖", "date": "周六",
        "weather_checked": True, "weather_ok": True,
        "weather_summary": "晴，26°C",
    })
    assert "✅ 适合出行" in p
    assert "缺出发地" in p
    print("    天气通过+缺出发地: ✅ 提示问出发地")

    # 场景 D：天气不好
    p = _build_dynamic_prompt({
        "destination": "西湖", "date": "周六",
        "weather_checked": True, "weather_ok": False,
        "weather_summary": "中雨",
    })
    assert "⚠️ 不太理想" in p
    assert "ask_weather_concern" in p
    print("    天气差: ✅ 提示必须调 ask_weather_concern")

    # 场景 E：全部就绪
    p = _build_dynamic_prompt({
        "destination": "西湖", "date": "周六", "origin": "杭电",
        "weather_checked": True, "weather_ok": True,
        "weather_summary": "晴", "trip_type": "same_city",
        "plan_saved": True,
    })
    assert "同城" in p
    assert "✅ 已保存" in p
    assert "阶段提示" not in p, "全部就绪不应有阶段提示"
    print("    全部就绪: ✅ 无阶段提示")

    print("    ✅ prompt 注入全部通过\n")


# ═══════════════════════════════════════
# 8. orchestrator graph 能正常编译
# ═══════════════════════════════════════

def test_orchestrator_compile():
    """验证 create_agent + HangoutState 能编译成功（不连 MCP）。"""
    from langchain.chat_models import init_chat_model
    from langchain.agents import create_agent
    from langgraph.store.memory import InMemoryStore
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agents.hangout.tools import (
        HangoutState, update_trip_info, mark_weather_result,
        mark_trip_type, ask_weather_concern, save_final_plan,
    )
    from app.agents.hangout.orchestrator import _inject_state_prompt

    print("[8] orchestrator graph 编译测试:")

    model = init_chat_model("deepseek-chat")

    sup_tools = [
        update_trip_info,
        mark_weather_result,
        mark_trip_type,
        ask_weather_concern,
        save_final_plan,
    ]

    try:
        graph = create_agent(
            model=model,
            tools=sup_tools,
            system_prompt="你是测试用主 Agent。",
            state_schema=HangoutState,
            middleware=[_inject_state_prompt],
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
        )
        print(f"    graph 节点: {list(graph.nodes.keys())}")
        print("    ✅ 编译成功\n")
    except Exception as e:
        print(f"    ❌ 编译失败: {e}\n")
        raise


# ═══════════════════════════════════════
# 运行全部测试
# ═══════════════════════════════════════

def main():
    print("=" * 50)
    print("HangoutState + Command 工具链测试")
    print("=" * 50 + "\n")

    tests = [
        test_hangout_state,
        test_update_trip_info,
        test_mark_weather_result,
        test_mark_trip_type,
        test_save_final_plan,
        test_get_final_plan,
        test_prompt_function,
        test_orchestrator_compile,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"    ❌ {test.__name__} 失败: {e}\n")

    print("=" * 50)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 50)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
