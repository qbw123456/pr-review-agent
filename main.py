#!/usr/bin/env python3
"""
PR Review Agent — MVP (s01 agent loop + s02 tools)

Usage:
  pip install -r requirements.txt
  copy .env.example to .env and fill in keys

  # Review current branch vs main
  python main.py review
  python main.py review --base develop --output REVIEW.md

  # Interactive mode
  python main.py chat
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
except ImportError:
    pass

from pr_review_agent.config import WORKDIR, require_env
from pr_review_agent.git_utils import build_review_request
from pr_review_agent.loop import agent_loop, extract_final_text


def cmd_review(base: str, output: Path | None, quiet_tools: bool) -> int:
    print(f"PR Review Agent — reviewing vs `{base}` at {WORKDIR}\n")
    messages = [{"role": "user", "content": build_review_request(base=base)}]
    agent_loop(messages, verbose=not quiet_tools)
    report = extract_final_text(messages)
    print("\n" + "=" * 60 + "\n")
    print(report)

    if output:
        output.write_text(report, encoding="utf-8")
        print(f"\n[Saved to {output}]")
    return 0


def cmd_chat() -> int:
    print("PR Review Agent — interactive (s01+s02 tools)")
    print(f"Workspace: {WORKDIR}")
    print("Commands: review  → quick review vs main")
    print("          q / exit → quit\n")

    history: list = []
    while True:
        try:
            query = input("\033[36mreview >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        stripped = query.strip()
        if stripped.lower() in ("q", "exit", ""):
            break
        if stripped.lower() == "review":
            query = build_review_request(base="main")

        history.append({"role": "user", "content": query})
        agent_loop(history)
        print(extract_final_text(history))
        print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PR Review Agent (s01 + s02)")
    sub = parser.add_subparsers(dest="command")

    review_p = sub.add_parser("review", help="Review current branch vs base")
    review_p.add_argument("--base", default="main", help="Base branch (default: main)")
    review_p.add_argument(
        "--output", "-o", type=Path, default=None, help="Write report to file"
    )
    review_p.add_argument(
        "--quiet-tools", action="store_true", help="Hide tool call previews"
    )

    sub.add_parser("chat", help="Interactive chat with review tools")

    args = parser.parse_args()
    if args.command in ("review", "chat"):
        require_env()
    if args.command == "review":
        return cmd_review(args.base, args.output, args.quiet_tools)
    if args.command == "chat":
        return cmd_chat()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
