"""端到端测试：模拟多轮对话。"""
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.agents.travel.pipeline import travel_pipeline


async def main():
    # 先初始化（正常运行时 main.py 的 lifespan 会做这一步）
    await travel_pipeline.init()

    tid = "e2e-test-001"

    conversations = [
        "周末想去西溪湿地玩",
        "我从城西银泰出发",
        # 等上一轮出方案后可以继续：
        # "选方案A，约朋友一起",
        # "发给小王 xw@gmail.com",
    ]

    for i, msg in enumerate(conversations, 1):
        print(f"\n{'='*60}")
        print(f"第{i}轮 | 用户：{msg}")
        print("=" * 60)

        async for event in travel_pipeline.generate_sse(tid, msg):
            etype = event.get("event", "?")
            data = event.get("data", "")
            print(f"  [{etype}] {data[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
