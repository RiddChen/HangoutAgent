"""Run an end-to-end SSE dialog against the running hangout backend.

This script does not start the server. It assumes the FastAPI backend is
already running and prints every SSE event so regressions are visible.
"""

import argparse
import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp


@dataclass
class TurnResult:
    text: str = ""
    statuses: list[str] = field(default_factory=list)
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    delta_count: int = 0
    first_delta_seconds: float | None = None


def _parse_sse_block(block: str) -> tuple[str, str]:
    event = "message"
    data_lines: list[str] = []
    for line in block.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return event, "\n".join(data_lines)


def _split_sse_buffer(buffer: str) -> tuple[list[str], str]:
    parts = re.split(r"\r?\n\r?\n", buffer)
    return parts[:-1], parts[-1] if parts else ""


def _json_or_content(data: str) -> dict[str, Any]:
    if not data:
        return {}
    try:
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else {"content": parsed}
    except json.JSONDecodeError:
        return {"content": data}


def _interrupt_type(payload: dict[str, Any]) -> str:
    info = payload.get("interrupt", payload)
    if isinstance(info, dict):
        inner = info.get("interrupt", info)
        if isinstance(inner, list):
            inner = inner[0] if inner else {}
        if isinstance(inner, dict):
            return str(inner.get("type", "interrupt"))
    return "interrupt"


async def send_turn(
    session: aiohttp.ClientSession,
    base_url: str,
    thread_id: str,
    message: str = "",
    interrupt_decision: dict[str, Any] | None = None,
    timeout_seconds: int = 180,
) -> TurnResult:
    url = f"{base_url.rstrip('/')}/api/v1/hangout/send"
    body = {
        "message": message,
        "thread_id": thread_id,
        "interrupt_decision": interrupt_decision,
    }

    label = f"interrupt={interrupt_decision}" if interrupt_decision else message
    print("\n" + "=" * 88)
    print(f"USER/SYSTEM -> {label}")
    print("=" * 88)

    result = TurnResult()
    started = time.monotonic()
    buffer = ""

    client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with session.post(url, json=body, timeout=client_timeout) as response:
        print(f"HTTP {response.status}")
        response.raise_for_status()

        async for chunk in response.content.iter_any():
            buffer += chunk.decode("utf-8", errors="replace")
            parts, buffer = _split_sse_buffer(buffer)

            for part in parts:
                if not part.strip():
                    continue
                event, data = _parse_sse_block(part)
                payload = _json_or_content(data)
                result.events.append(event)

                if event in {"message", "message_delta"}:
                    delta = payload.get("content", "")
                    if isinstance(delta, str) and delta:
                        if result.first_delta_seconds is None:
                            result.first_delta_seconds = time.monotonic() - started
                        result.delta_count += 1
                        result.text += delta
                        print(delta, end="", flush=True)
                elif event == "status":
                    status = str(payload.get("content", ""))
                    if status:
                        result.statuses.append(status)
                        print(f"\n[status] {status}")
                elif event == "interrupt":
                    result.interrupts.append(payload)
                    print(f"\n[interrupt:{_interrupt_type(payload)}] {json.dumps(payload, ensure_ascii=False)}")
                elif event == "error":
                    error = str(payload.get("content") or payload.get("error") or payload)
                    result.errors.append(error)
                    print(f"\n[error] {error}")
                elif event == "done":
                    print("\n[done]")
                else:
                    print(f"\n[{event}] {json.dumps(payload, ensure_ascii=False)}")

    if buffer.strip():
        event, data = _parse_sse_block(buffer)
        print(f"\n[tail:{event}] {data}")
    print()
    return result


async def clear_thread(session: aiohttp.ClientSession, base_url: str, thread_id: str) -> None:
    url = f"{base_url.rstrip('/')}/api/v1/hangout/messages"
    async with session.delete(url, params={"thread_id": thread_id}) as response:
        if response.status not in {200, 404}:
            print(f"clear thread failed: HTTP {response.status} {await response.text()}")


def _contains_any(values: list[str], needles: list[str]) -> bool:
    text = "\n".join(values)
    return any(needle in text for needle in needles)


def _event_index(values: list[str], needles: list[str]) -> int | None:
    for index, value in enumerate(values):
        if any(needle in value for needle in needles):
            return index
    return None


def print_summary(turns: list[TurnResult]) -> int:
    all_statuses = [status for turn in turns for status in turn.statuses]
    all_errors = [error for turn in turns for error in turn.errors]
    full_text = "\n".join(turn.text for turn in turns)

    weather_index = _event_index(all_statuses, ["天气专家"])
    route_index = _event_index(all_statuses, ["路线专家"])
    train_index = _event_index(all_statuses, ["火车票"])
    flight_index = _event_index(all_statuses, ["航班"])

    checks = {
        "有流式 message_delta": sum(turn.delta_count for turn in turns) >= 5,
        "首个 delta 延迟可见": any(
            turn.first_delta_seconds is not None and turn.first_delta_seconds < 15
            for turn in turns
        ),
        "天气专家被调用": weather_index is not None,
        "天气先于路线/火车/航班": weather_index is not None and all(
            idx is None or weather_index <= idx
            for idx in (route_index, train_index, flight_index)
        ),
        "火车 MCP 查询被触发": train_index is not None or "火车" in full_text or "高铁" in full_text,
        "航班 MCP 查询被触发": flight_index is not None or "航班" in full_text or "飞机" in full_text,
        "没有 SSE error": not all_errors,
        "没有暴露 transfer 噪声": "Transferring back to supervisor" not in full_text
        and "transfer_back_to_supervisor" not in full_text,
        "没有暴露原始 MCP JSON": '"code"' not in full_text and '\\"code\\"' not in full_text,
    }

    print("\n" + "=" * 88)
    print("E2E CHECK SUMMARY")
    print("=" * 88)
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'} - {name}")

    if all_statuses:
        print("\nstatus timeline:")
        for status in all_statuses:
            print(f"- {status}")
    if all_errors:
        print("\nerrors:")
        for error in all_errors:
            print(f"- {error}")

    return 0 if all(checks.values()) else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--thread-id", default=f"e2e-{uuid.uuid4().hex[:10]}")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--turn",
        action="append",
        help="Override dialog turns. Can be passed multiple times.",
    )
    args = parser.parse_args()

    turns_to_send = args.turn or [
        "你好",
        "我想去南京玩，5月31日去",
        "我从杭州出发",
        "不需要住宿。请把火车和飞机的候选都查出来，然后给我一个方案。",
    ]

    async with aiohttp.ClientSession() as session:
        await clear_thread(session, args.base_url, args.thread_id)
        results: list[TurnResult] = []
        for message in turns_to_send:
            result = await send_turn(
                session,
                args.base_url,
                args.thread_id,
                message=message,
                timeout_seconds=args.timeout,
            )
            results.append(result)

            for interrupt_payload in result.interrupts:
                kind = _interrupt_type(interrupt_payload)
                if kind == "weather_confirm":
                    approved = await send_turn(
                        session,
                        args.base_url,
                        args.thread_id,
                        interrupt_decision={"type": "approve"},
                        timeout_seconds=args.timeout,
                    )
                    results.append(approved)

        return print_summary(results)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
