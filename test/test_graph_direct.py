"""直接调 graph，观察 LLM 到底在做什么（有没有 tool call）。"""

import asyncio
import json
from dotenv import load_dotenv
load_dotenv()


async def main():
    from app.agents.hangout.orchestrator import hangout_orchestrator

    print("⏳ 初始化...")
    await hangout_orchestrator.init()
    print("✅ 初始化完成\n")

    config = {"configurable": {"thread_id": "debug_001", "user_id": "default"}}
    inp = {"messages": [{"role": "user", "content": "这周六去杭州西湖"}]}

    print("📤 发送: 这周六去杭州西湖\n")
    print("─" * 60)

    async for chunk in hangout_orchestrator.graph.astream(
        inp, config=config, stream_mode="updates"
    ):
        if isinstance(chunk, dict):
            for node, data in chunk.items():
                print(f"\n🔹 节点: {node}")

                if isinstance(data, dict):
                    msgs = data.get("messages", [])
                    for msg in msgs:
                        cls = msg.__class__.__name__
                        content = getattr(msg, "content", "")
                        tool_calls = getattr(msg, "tool_calls", [])

                        if tool_calls:
                            print(f"   [{cls}] 🔧 Tool calls:")
                            for tc in tool_calls:
                                name = tc.get("name", "?")
                                args = tc.get("args", {})
                                print(f"      → {name}({json.dumps(args, ensure_ascii=False)})")
                        elif content:
                            text = content if isinstance(content, str) else str(content)
                            if len(text) > 200:
                                text = text[:200] + "..."
                            print(f"   [{cls}] {text}")

                    # 检查 state 更新
                    for key in ("destination", "date", "origin", "weather_checked", "trip_type", "plan_saved"):
                        if key in data:
                            print(f"   📋 State 更新: {key} = {data[key]}")

    print("\n─" * 60)

    # 检查最终 state
    state = await hangout_orchestrator.graph.aget_state(config)
    if state and state.values:
        print("\n📋 最终 State:")
        for k in ("destination", "date", "origin", "weather_checked", "weather_ok",
                   "weather_summary", "trip_type", "plan_saved"):
            v = state.values.get(k)
            if v not in (None, "", False):
                print(f"   {k} = {v}")

    await hangout_orchestrator.close()


if __name__ == "__main__":
    asyncio.run(main())
