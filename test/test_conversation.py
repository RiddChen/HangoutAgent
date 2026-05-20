"""端到端对话测试：模拟用户和 supervisor 多轮对话。

运行方式：
    uv run python -m test.test_conversation

需要：Redis Stack 运行 + .env 配置好 DeepSeek + MCP keys
"""

import asyncio
import json
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()


async def chat(supervisor, thread_id: str, message: str, turn: int):
    """发送一条消息，收集所有 SSE 事件并打印。"""
    print(f"\n{'─'*60}")
    print(f"👤 Turn {turn}: {message}")
    print(f"{'─'*60}")

    full_text = ""
    statuses = []
    interrupts = []

    async for event in supervisor.generate_sse(thread_id, message):
        if not isinstance(event, dict):
            continue

        event_type = event.get("event", "")
        raw_data = event.get("data", "")

        try:
            payload = json.loads(raw_data) if raw_data else {}
        except (json.JSONDecodeError, TypeError):
            payload = {"content": raw_data}

        content = payload.get("content", "")

        if event_type == "message_delta":
            full_text += content
        elif event_type == "status":
            statuses.append(content)
            print(f"   📡 {content}")
        elif event_type == "interrupt":
            interrupts.append(payload)
            print(f"   🛑 INTERRUPT")
        elif event_type == "error":
            print(f"   ❌ ERROR: {content}")

    if full_text:
        display = full_text.strip()
        if len(display) > 600:
            display = display[:600] + "..."
        print(f"\n   🤖 ({len(full_text)} 字):\n   {display}")

    return {"text": full_text, "statuses": statuses, "interrupts": interrupts}


async def get_state(supervisor, thread_id: str) -> dict:
    """读取 graph state 中的自定义字段。"""
    config = {"configurable": {"thread_id": thread_id, "user_id": "default"}}
    state = await supervisor.graph.aget_state(config)
    if not state or not state.values:
        return {}

    fields = [
        "destination", "date", "origin",
        "weather_checked", "weather_ok", "weather_summary",
        "trip_type", "transport_preference", "plan_saved",
    ]
    return {f: state.values.get(f) for f in fields
            if state.values.get(f) not in (None, "", False)}


def show_state(state: dict):
    if state:
        print(f"   📋 State: {state}")
    else:
        print(f"   📋 State: (空)")


async def main():
    from app.agents.travel.supervisor import travel_supervisor

    print("=" * 60)
    print("  端到端对话测试 —— TravelState + Command 工具链")
    print("=" * 60)

    print("\n⏳ 初始化 supervisor...")
    try:
        await travel_supervisor.init()
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        sys.exit(1)
    print("✅ 初始化完成")

    thread_id = f"test_{uuid.uuid4().hex[:8]}"
    print(f"🔗 Thread: {thread_id}\n")

    passed = []
    failed = []

    def check(name: str, condition: bool, detail: str = ""):
        if condition:
            passed.append(name)
            print(f"   ✅ {name}" + (f" ({detail})" if detail else ""))
        else:
            failed.append(name)
            print(f"   ❌ {name}" + (f" ({detail})" if detail else ""))

    try:
        # ── Turn 1: 提供目的地 + 日期 ──
        r1 = await chat(travel_supervisor, thread_id,
                        "这周六去杭州西湖", turn=1)
        s1 = await get_state(travel_supervisor, thread_id)
        show_state(s1)

        # 如果 LLM 没提取信息，再明确说一次
        if not s1.get("destination"):
            r1b = await chat(travel_supervisor, thread_id,
                             "目的地是杭州西湖，时间是这周六", turn="1b")
            s1 = await get_state(travel_supervisor, thread_id)
            show_state(s1)

        check("destination 写入 State",
              bool(s1.get("destination")),
              s1.get("destination", ""))
        check("date 写入 State",
              bool(s1.get("date")),
              s1.get("date", ""))

        # ── Turn 2: 等天气结果 / 或 LLM 可能在问出发地 ──
        # 先看当前状态，如果天气已查就跳过
        if not s1.get("weather_checked"):
            # 看看 LLM 的回复是在问什么
            text1 = r1.get("text", "")
            weather_mentioned = any(s for s in r1["statuses"] if "天气" in s)

            if weather_mentioned:
                # 天气专家在跑，等结果
                print("\n   ⏳ 天气专家应该在工作中...")
            else:
                # LLM 可能在问出发地或做其他事，推进对话
                r2 = await chat(travel_supervisor, thread_id,
                                "我从浙大紫金港出发", turn=2)
                s2 = await get_state(travel_supervisor, thread_id)
                show_state(s2)
        else:
            s2 = s1

        # 再检查一次天气
        s_now = await get_state(travel_supervisor, thread_id)
        show_state(s_now)

        if s_now.get("weather_checked"):
            check("weather_checked 已标记", True,
                  f"ok={s_now.get('weather_ok')}, {s_now.get('weather_summary', '')}")
        else:
            # 再推一轮，有时候 LLM 需要多轮
            r3 = await chat(travel_supervisor, thread_id,
                            "好的，帮我查一下天气吧", turn=3)
            s3 = await get_state(travel_supervisor, thread_id)
            show_state(s3)
            check("weather_checked 已标记",
                  bool(s3.get("weather_checked")),
                  s3.get("weather_summary", ""))

        # ── Turn 3: 提供出发地（如果还没有） ──
        s_now = await get_state(travel_supervisor, thread_id)
        if not s_now.get("origin"):
            r4 = await chat(travel_supervisor, thread_id,
                            "我从浙大紫金港出发", turn=4)
            s4 = await get_state(travel_supervisor, thread_id)
            show_state(s4)
            check("origin 写入 State",
                  bool(s4.get("origin")),
                  s4.get("origin", ""))
        else:
            check("origin 写入 State", True, s_now.get("origin", ""))

        # ── 最终状态检查 ──
        s_final = await get_state(travel_supervisor, thread_id)

        print(f"\n{'='*60}")
        print("  最终 State 快照")
        print(f"{'='*60}")
        for k, v in s_final.items():
            print(f"   {k}: {v}")

        print(f"\n{'='*60}")
        print(f"  结果: {len(passed)} 通过, {len(failed)} 失败")
        for p in passed:
            print(f"   ✅ {p}")
        for f in failed:
            print(f"   ❌ {f}")
        print(f"{'='*60}")

    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await travel_supervisor.close()

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
