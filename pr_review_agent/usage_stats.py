"""Token and timing stats for PR review runs."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def log_usage_enabled() -> bool:
    raw = os.getenv("REVIEW_LOG_USAGE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class UsageTracker:
    """Stats for one agent_loop phase (one file subagent or integration/legacy)."""

    label: str
    api_calls: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    api_sec: float = 0.0
    retry_sleep_sec: float = 0.0
    wall_sec: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record_call(
        self,
        response,
        *,
        api_sec: float,
        retry_sleep_sec: float = 0.0,
        retries: int = 0,
    ) -> None:
        self.api_calls += 1
        self.retries += retries
        self.api_sec += api_sec
        self.retry_sleep_sec += retry_sleep_sec
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)


@dataclass
class ReviewRunStats:
    route: str = ""
    workers: int | None = None
    phases: list[UsageTracker] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(p.input_tokens for p in self.phases)

    @property
    def total_output_tokens(self) -> int:
        return sum(p.output_tokens for p in self.phases)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_api_calls(self) -> int:
        return sum(p.api_calls for p in self.phases)

    @property
    def total_wall_sec(self) -> float:
        return sum(p.wall_sec for p in self.phases)

    @property
    def subagent_wall_sec(self) -> float:
        return sum(
            p.wall_sec for p in self.phases if p.label not in ("integrate", "legacy")
        )


def print_usage_report(stats: ReviewRunStats) -> None:
    if not log_usage_enabled() or not stats.phases:
        return

    print("\033[90m" + "─" * 56 + "\033[0m")
    print("\033[36m[usage]\033[0m 审查资源统计")
    if stats.route:
        extra = f"  workers={stats.workers}" if stats.workers is not None else ""
        print(f"  路由: {stats.route}{extra}")

    for phase in stats.phases:
        short = phase.label if len(phase.label) <= 44 else "…" + phase.label[-43:]
        print(
            f"  · {short:<44}  "
            f"calls={phase.api_calls}  "
            f"in={phase.input_tokens:,}  out={phase.output_tokens:,}  "
            f"api={phase.api_sec:.1f}s  wall={phase.wall_sec:.1f}s"
            + (f"  retries={phase.retries}" if phase.retries else "")
        )

    print(
        f"  \033[1m合计\033[0m   "
        f"calls={stats.total_api_calls}  "
        f"tokens={stats.total_tokens:,} "
        f"(in {stats.total_input_tokens:,} / out {stats.total_output_tokens:,})  "
        f"wall={stats.total_wall_sec:.1f}s"
    )
    sub_phases = [p for p in stats.phases if p.label not in ("integrate", "legacy")]
    if len(sub_phases) > 1 and stats.subagent_wall_sec > 0:
        print(
            f"         子 Agent 阶段 wall 合计 {stats.subagent_wall_sec:.1f}s "
            f"（并行时 wall 之和 ≥ 实际时钟时间）"
        )
    print("\033[90m" + "─" * 56 + "\033[0m\n")
