"""List tools from the configured train and flight MCP servers."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.hangout.mcp_client import get_flight_mcp_tools, get_train_mcp_tools  # noqa: E402


async def _list_tools(label: str, loader) -> None:
    print(f"\n=== {label} ===")
    try:
        tools = await loader()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return
    print(f"count: {len(tools)}")
    for tool in tools:
        print(f"- {tool.name}")


async def main() -> int:
    load_dotenv(ROOT / ".env")
    await _list_tools("12306 train MCP", get_train_mcp_tools)
    await _list_tools("BigModel aviation MCP", get_flight_mcp_tools)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
