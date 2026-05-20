"""Test whether the configured Gaode MCP can resolve city by IP location."""

import asyncio
import argparse
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.hangout.mcp_client import get_amap_tools  # noqa: E402
from app.common.sse import serialize  # noqa: E402


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def _mask_ip(value: str) -> str:
    parts = value.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return _mask(value)


def _print_json(title: str, value) -> None:
    print(f"\n=== {title} ===")
    if isinstance(value, str):
        print(value)
        return
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _tool_schema(tool) -> dict:
    schema = getattr(tool, "args_schema", None)
    if not schema:
        return {}
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    if hasattr(schema, "schema"):
        return schema.schema()
    return {}


def _extract_city(value) -> str:
    if isinstance(value, dict):
        for key in ("city", "cityName", "adcode_city", "province"):
            city = value.get(key)
            if isinstance(city, str) and city and city != "[]":
                return city
        for item in value.values():
            city = _extract_city(item)
            if city:
                return city
    elif isinstance(value, list):
        for item in value:
            city = _extract_city(item)
            if city:
                return city
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
            city = _extract_city(parsed)
            if city:
                return city
        except Exception:
            pass
        match = re.search(r'"city"\s*:\s*"([^"]+)"', text)
        if match:
            return match.group(1)
        match = re.search(r'([\u4e00-\u9fa5]{2,12}市)', text)
        if match:
            return match.group(1)
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test Gaode MCP maps_ip_location.")
    parser.add_argument("--ip", default="", help="Optional public IP to pass to maps_ip_location.")
    parser.add_argument(
        "--no-headers",
        action="store_true",
        help="Use only the Authorization query string, matching BigModel's sample config.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    gaode_key = (
        os.getenv("GAODE_MCP_API_KEY")
        or os.getenv("WEATHER_MCP_API_KEY")
        or os.getenv("BIGMODEL_API_KEY")
        or ""
    )
    gaode_url = os.getenv("GAODE_MCP_URL") or (
        "https://open.bigmodel.cn/api/mcp-broker/proxy/"
        f"gaode-map/mcp?Authorization={gaode_key}"
    )

    print(f"project: {ROOT}")
    print(f"transport: {os.getenv('GAODE_MCP_TRANSPORT', 'http')}")
    print(f"api key: {_mask(gaode_key)}")
    print(f"url: {gaode_url.replace(gaode_key, _mask(gaode_key))}")

    if args.no_headers:
        client = MultiServerMCPClient({
            "gaode-map": {
                "transport": os.getenv("GAODE_MCP_TRANSPORT", "http"),
                "url": gaode_url,
                "timeout": timedelta(seconds=30),
                "sse_read_timeout": timedelta(seconds=300),
            }
        })
        tools = await client.get_tools()
    else:
        tools = await get_amap_tools()
    names = sorted(t.name for t in tools)
    _print_json("loaded tools", names)

    ip_tool = next((t for t in tools if t.name == "maps_ip_location"), None)
    if not ip_tool:
        print("\nERROR: maps_ip_location not found in Gaode MCP tools.")
        return 2

    _print_json("maps_ip_location schema", _tool_schema(ip_tool))

    calls = [{"ip": args.ip}] if args.ip else [{"ip": ""}, {}]
    for call_args in calls:
        display_args = dict(call_args)
        if display_args.get("ip"):
            display_args["ip"] = _mask_ip(display_args["ip"])
        _print_json("calling maps_ip_location with args", display_args)
        try:
            result = await ip_tool.ainvoke(call_args)
        except Exception as exc:
            print(f"ERROR: call failed: {type(exc).__name__}: {exc}")
            continue

        raw = serialize(result)
        _print_json("raw result", raw)
        print(f"extracted city: {_extract_city(raw) or '(empty)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
