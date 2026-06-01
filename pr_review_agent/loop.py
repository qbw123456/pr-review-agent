"""s01: agent loop — LLM ↔ tools until stop (s03: permission gates)."""

from __future__ import annotations

import os
import time

from anthropic import APIStatusError, RateLimitError

from .config import MODEL, client
from .permissions import check_permission
from .prompts import build_system_prompt
from .tools import TOOL_HANDLERS, TOOLS
from .usage_stats import UsageTracker

DEFAULT_API_MAX_RETRIES = 5
DEFAULT_API_RETRY_BASE_SEC = 10.0
DEFAULT_API_RETRY_MAX_SEC = 120.0

_RATE_LIMIT_HINTS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "限流",
    "请求过于频繁",
    "429",
)


def _api_retry_settings() -> tuple[int, float, float]:
    """Return (max_retries_after_failure, base_delay_sec, max_delay_sec)."""
    raw_retries = os.getenv("REVIEW_API_MAX_RETRIES", "").strip()
    raw_base = os.getenv("REVIEW_API_RETRY_BASE_SEC", "").strip()
    raw_max = os.getenv("REVIEW_API_RETRY_MAX_SEC", "").strip()
    try:
        max_retries = int(raw_retries) if raw_retries else DEFAULT_API_MAX_RETRIES
    except ValueError:
        max_retries = DEFAULT_API_MAX_RETRIES
    try:
        base_delay = float(raw_base) if raw_base else DEFAULT_API_RETRY_BASE_SEC
    except ValueError:
        base_delay = DEFAULT_API_RETRY_BASE_SEC
    try:
        max_delay = float(raw_max) if raw_max else DEFAULT_API_RETRY_MAX_SEC
    except ValueError:
        max_delay = DEFAULT_API_RETRY_MAX_SEC
    return max(0, max_retries), max(1.0, base_delay), max(base_delay, max_delay)


def _is_retryable_api_error(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in (429, 529):
        return True
    status = getattr(exc, "status_code", None)
    if status in (429, 529):
        return True
    lower = str(exc).lower()
    return any(hint in lower for hint in _RATE_LIMIT_HINTS)


def create_message_with_retry(
    *,
    verbose: bool = True,
    usage: UsageTracker | None = None,
    **kwargs,
):
    """Call messages.create with exponential backoff on rate limits."""
    max_retries, base_delay, max_delay = _api_retry_settings()
    last_exc: BaseException | None = None
    retries_done = 0
    retry_sleep = 0.0
    for attempt in range(max_retries + 1):
        call_start = time.perf_counter()
        try:
            response = client.messages.create(**kwargs)
            if usage is not None:
                usage.record_call(
                    response,
                    api_sec=time.perf_counter() - call_start,
                    retry_sleep_sec=retry_sleep,
                    retries=retries_done,
                )
            return response
        except Exception as exc:
            last_exc = exc
            if not _is_retryable_api_error(exc) or attempt >= max_retries:
                raise
            retries_done += 1
            delay = min(max_delay, base_delay * (2**attempt))
            if verbose:
                print(
                    f"\033[33m⚠ API 限流/过载，{delay:.0f}s 后重试 "
                    f"({attempt + 1}/{max_retries})…\033[0m"
                )
            time.sleep(delay)
            retry_sleep += delay
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("create_message_with_retry: unreachable")


def agent_loop(
    messages: list,
    *,
    system: str | None = None,
    tools: list | None = None,
    tool_handlers: dict | None = None,
    verbose: bool = True,
    interactive: bool = False,
    review_mode: bool = False,
    max_turns: int = 50,
    usage: UsageTracker | None = None,
) -> None:
    if system is None:
        system = build_system_prompt()
    if tools is None:
        tools = TOOLS
    if tool_handlers is None:
        handlers = TOOL_HANDLERS
    else:
        handlers = tool_handlers

    for _ in range(max_turns):
        response = create_message_with_retry(
            verbose=verbose,
            usage=usage,
            model=MODEL,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if verbose:
                print(f"\033[33m> {block.name}\033[0m")

            if not check_permission(
                block, interactive=interactive, review_mode=review_mode
            ):
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Permission denied.",
                    }
                )
                continue

            handler = handlers.get(block.name)
            output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
            if verbose:
                print(str(output)[:200])
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )
        messages.append({"role": "user", "content": results})

    if verbose:
        print(f"\033[33m⚠ 达到最大轮次 ({max_turns})，停止工具循环\033[0m")


def extract_final_text(messages: list) -> str:
    content = messages[-1].get("content")
    if not isinstance(content, list):
        return str(content or "")
    parts = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)
