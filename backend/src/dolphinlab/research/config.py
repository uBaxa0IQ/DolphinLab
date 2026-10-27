"""Resource, context, model, and timing configuration for a research session."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    task_path: Path
    work_dir: Path
    seconds: float = 10800
    reserve: float = 0
    cpus: int = 8
    ram_mb: int = 28000
    llm_concurrency: int = 6
    max_agents: int = 25
    reasoning_effort: str = "high"
    compaction_reasoning_effort: str = "low"
    role_reasoning_efforts: dict = field(
        default_factory=lambda: {
            "tech_lead": "medium",
            "team_lead": "medium",
            "data_engineer": "medium",
        }
    )
    temperature: float | None = 0.3
    request_timeout: float = 300
    shutdown_seconds: float = 15
    request_extra: dict = field(default_factory=dict)
    context_history_bytes: int = 128000
    context_compact_turns: int = 16
    context_recent_exchanges: int = 6
    context_payload_bytes: int = 16000
    context_window: int | None = field(
        default_factory=lambda: int(os.environ["MODEL_CONTEXT_TOKENS"])
        if os.getenv("MODEL_CONTEXT_TOKENS")
        else None
    )
    generation_reserve: int | None = field(
        default_factory=lambda: int(os.environ["MODEL_GENERATION_RESERVE"])
        if os.getenv("MODEL_GENERATION_RESERVE")
        else None
    )
    log_bytes: int = 2_000_000
    resume: bool = False
    reference_roots: tuple[Path, ...] = ()
    deadline_wall: float | None = None
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", ""))
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        self.task_path = self.task_path.resolve()
        self.work_dir = self.work_dir.resolve()
        if self.seconds <= 0 or self.cpus < 1 or self.ram_mb < 128:
            raise ValueError("Invalid time or resource budget")
        if self.llm_concurrency < 1 or self.max_agents < 5 or self.request_timeout <= 0:
            raise ValueError(
                "Need positive concurrency/timeout and capacity for tech lead plus a four-role team"
            )
        if (
            min(
                self.context_history_bytes,
                self.context_compact_turns,
                self.context_recent_exchanges,
                self.context_payload_bytes,
            )
            < 1
        ):
            raise ValueError("Context working-memory settings must be positive")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("Context window must be a confirmed positive model capacity")
        if self.generation_reserve is not None and self.generation_reserve <= 0:
            raise ValueError("Generation reserve must be positive")
        if (
            self.context_window
            and self.generation_reserve
            and self.generation_reserve >= self.context_window
        ):
            raise ValueError("Generation reserve must leave room for input")
        self.reserve = min(max(0, self.reserve), self.seconds)
        self.shutdown_seconds = min(max(0.0, self.shutdown_seconds), self.seconds)

    @property
    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if self.deadline_wall is not None:
            remaining = min(remaining, self.deadline_wall - time.time())
        return max(0.0, remaining)

    @property
    def finalizing(self) -> bool:
        return self.remaining <= self.reserve

    def public(self) -> dict:
        return {
            "remaining_seconds": round(self.remaining, 1),
            "work_remaining_seconds": round(max(0.0, self.remaining - self.shutdown_seconds), 1),
            "shutdown_reserve_seconds": self.shutdown_seconds,
            "context_policy": {
                "history_bytes_before_proactive_summary": self.context_history_bytes,
                "minimum_turns_between_summaries": self.context_compact_turns,
                "recent_payload_exchanges_preserved": self.context_recent_exchanges,
                "physical_capacity_guard_has_priority": True,
            },
            "phase": "finalization" if self.finalizing else "research",
            "finalization_reserve_seconds": self.reserve,
            "cpu_total": self.cpus,
            "ram_budget_mb": self.ram_mb,
        }
