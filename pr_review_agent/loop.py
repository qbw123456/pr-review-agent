"""s01: agent loop — LLM ↔ tools until stop (s03: permission gates)."""

from __future__ import annotations

from .config import MODEL, client
from .permissions import check_permission
from .prompts import build_system_prompt
from .tools import TOOL_HANDLERS, TOOLS


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
) -> None:
    system = system or build_system_prompt()
    tools = tools or TOOLS
    handlers = tool_handlers or TOOL_HANDLERS

    for _ in range(max_turns):
        response = client.messages.create(
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
