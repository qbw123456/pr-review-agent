"""s01: agent loop — LLM ↔ tools until stop."""

from .config import MODEL, client
from .prompts import build_system_prompt
from .tools import TOOL_HANDLERS, TOOLS


def agent_loop(messages: list, *, verbose: bool = True) -> None:
    system = build_system_prompt()
    while True:
        response = client.messages.create(
            model=MODEL,
            system=system,
            messages=messages,
            tools=TOOLS,
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
            handler = TOOL_HANDLERS.get(block.name)
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


def extract_final_text(messages: list) -> str:
    content = messages[-1].get("content")
    if not isinstance(content, list):
        return str(content or "")
    parts = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)
