#!/usr/bin/env python3
"""Resolve incremental review state from an existing PR comment (for GitHub Actions)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pr_review_agent.incremental_review import (  # noqa: E402
    AGENT_COMMENT_MARKER,
    extract_report_from_comment,
    parse_last_reviewed_sha,
)


def _fetch_pr_comments(repo: str, pr_number: int, token: str) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "pr-review-agent",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_agent_comment(comments: list[dict]) -> dict | None:
    matches = [
        c
        for c in comments
        if AGENT_COMMENT_MARKER in (c.get("body") or "")
    ]
    if not matches:
        return None
    matches.sort(key=lambda c: c.get("updated_at") or c.get("created_at") or "")
    return matches[-1]


def write_github_env(path: Path, key: str, value: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve REVIEW_SINCE_SHA from PR comment")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--github-env",
        default=os.getenv("GITHUB_ENV", ""),
        help="Path to GITHUB_ENV for output",
    )
    parser.add_argument(
        "--previous-report-out",
        default="previous-review.md",
        help="Where to write previous report body",
    )
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if not token:
        print("No GITHUB_TOKEN; skipping incremental resolution (full review).", file=sys.stderr)
        return 0

    try:
        comments = _fetch_pr_comments(args.repo, args.pr_number, token)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Failed to fetch PR comments: {exc}", file=sys.stderr)
        return 0

    comment = find_agent_comment(comments)
    if not comment:
        print("No existing pr-review-agent comment; full review.", file=sys.stderr)
        return 0

    body = comment.get("body") or ""
    since_sha = parse_last_reviewed_sha(body)
    if not since_sha:
        print("Comment found but no last_sha marker; full review.", file=sys.stderr)
        return 0

    if since_sha == args.head_sha:
        print(f"HEAD unchanged ({since_sha[:7]}); incremental may be no-op.", file=sys.stderr)

    previous = extract_report_from_comment(body)
    prev_path = Path(args.previous_report_out)
    if previous:
        prev_path.write_text(previous, encoding="utf-8")

    if args.github_env:
        env_path = Path(args.github_env)
        write_github_env(env_path, "REVIEW_SINCE_SHA", since_sha)
        if previous:
            write_github_env(env_path, "REVIEW_PREVIOUS_REPORT", str(prev_path.resolve()))

    print(f"Incremental since {since_sha[:7]} -> {args.head_sha[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
