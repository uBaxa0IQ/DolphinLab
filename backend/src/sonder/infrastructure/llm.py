"""Provided Chat Completions API: complete replies, coordinated backoff and capacity facts."""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from sonder.common.serialization import dumps, uid

request_actor = ContextVar("request_actor", default="coordinator")
request_purpose = ContextVar("request_purpose", default="research")
request_effort = ContextVar("request_effort", default=None)


@dataclass
class Failure:
    category: str
    retryable: bool
    shared: bool = False
    retry_after: float = 0


class ResponseProblem(Exception):
    def __init__(self, category, detail, raw=None, usage=None):
        super().__init__(detail)
        self.category, self.raw, self.usage = category, raw, usage or {}


class APIUnavailable(Exception):
    pass


def is_context_error(error):
    text = str(error).lower()
    return any(
        s in text
        for s in (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "too many tokens",
            "input is too long",
            "max_model_len",
        )
    )


def failure_kind(error):
    if isinstance(error, ResponseProblem):
        return Failure(error.category, True)
    if is_context_error(error):
        return Failure("context", True)
    if isinstance(error, APIUnavailable):
        return Failure("configuration", False, True)
    status = getattr(error, "status_code", None)
    text = str(error).lower()
    if status == 404 and any(s in text for s in ("no endpoints found", "no available endpoints")):
        return Failure("temporary_routing", True, True)
    if status in (401, 402, 403, 404) or any(
        s in text
        for s in (
            "insufficient_quota",
            "budget exceeded",
            "budget has been exceeded",
            "invalid api key",
            "model_not_found",
        )
    ):
        return Failure("credentials_or_budget", False, True)
    if status == 429:
        delay = 0.0
        response = getattr(error, "response", None)
        header = getattr(response, "headers", {}).get("retry-after")
        if header:
            try:
                delay = max(0.0, float(header))
            except ValueError:
                try:
                    delay = max(
                        0.0,
                        (
                            parsedate_to_datetime(header) - datetime.now(timezone.utc)
                        ).total_seconds(),
                    )
                except (ValueError, TypeError):
                    pass
        return Failure("rate_limit", True, True, delay)
    if status in (408, 409) or (status is not None and status >= 500):
        return Failure("temporary_service", True, status in (502, 503, 504))
    if status in (400, 422):
        return Failure("invalid_request", False)
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return Failure("local_context_or_protocol", False)
    if isinstance(error, (TimeoutError, ConnectionError)) or any(
        s in type(error).__name__.lower() for s in ("timeout", "connection")
    ):
        return Failure("transport", True)
    return Failure("unexpected_response", True)


def backoff(failure, attempt):
    return max(failure.retry_after, min(60.0, 2 ** min(attempt, 6)) * random.uniform(0.8, 1.2))


class ModelClient:
    def __init__(self, config):
        if not all((config.api_key, config.base_url, config.model)):
            raise ValueError("Set OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL as in baseline")
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.request_timeout,
            max_retries=0,
        )
        self.config = config
        self.slots = asyncio.Semaphore(config.llm_concurrency)
        self.blocked_until = 0.0
        self.disabled_reason = None
        self.failures = 0
        self.ratios = deque(maxlen=16)
        self.window_source = "explicit configuration" if config.context_window else "not confirmed"

    def trace(self, ident, stage, payload=None):
        logger = getattr(self, "log_event", None)
        folder = getattr(self, "log_dir", None)
        if not logger or not folder:
            return
        record = {
            "request_id": ident,
            "agent": request_actor.get(),
            "purpose": request_purpose.get(),
        }
        if payload is not None:
            path = Path(folder) / request_actor.get() / (ident + "-" + stage + ".json")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                content = dumps(payload)
                if self.config.api_key:
                    content = content.replace(self.config.api_key, "[redacted]")
                path.write_text(content, encoding="utf-8")
                record["path"] = str(path)
            except OSError as error:
                record.update(trace_error=f"{type(error).__name__}: {error}", payload=payload)
        logger("model_" + stage, record)

    @staticmethod
    def payload_bytes(messages, tools=None):
        return len(
            json.dumps({"messages": messages, "tools": tools or []}, ensure_ascii=False).encode(
                "utf-8"
            )
        )

    def context_status(self, messages, tools=None):
        size = self.payload_bytes(messages, tools)
        # Calibrated against actual prompt usage, including schemas. This is an estimate, not
        # an invented tokenizer or a claim of knowing Qwen's context capacity.
        ratio = max(self.ratios) * 1.2 if self.ratios else 1.0
        estimate = math.ceil(size * ratio) + 256
        window = self.config.context_window
        reserve = self.config.generation_reserve or (math.ceil(window * 0.25) if window else None)
        return {
            "window_tokens": window,
            "capacity_source": self.window_source,
            "estimated_input_tokens": estimate,
            "generation_reserve_tokens": reserve,
            "estimate_calibrated": bool(self.ratios),
            "needs_compaction": bool(
                window and self.ratios and estimate > (window - reserve) * 0.9
            ),
        }

    def learn_capacity(self, error):
        text = str(error)
        patterns = (
            r"maximum context length(?:\s+is|\s*[:=])\s*([\d,]+)",
            r"max_model_len[\s\"':=]+([\d,]+)",
            r"context window(?:\s+of|\s*[:=])\s*([\d,]+)",
        )
        for pattern in patterns:
            found = re.search(pattern, text, re.I)
            if found:
                value = int(found[1].replace(",", ""))
                if value > 0:
                    current = self.config.context_window
                    self.config.context_window = min(current, value) if current else value
                    self.window_source = "provider context-limit error"
                    return

    async def complete(self, messages, tools=None):
        ident = uid("request")
        effort = (
            self.config.compaction_reasoning_effort
            if request_purpose.get() == "compaction"
            else (request_effort.get() or self.config.reasoning_effort)
        )
        extra = dict(self.config.request_extra)
        if "reasoning" in extra:
            extra["reasoning"] = {**extra["reasoning"], "effort": effort}
        self.trace(
            ident,
            "queued",
            {
                "model": self.config.model,
                "messages": messages,
                "tools": tools or [],
                "reasoning_effort": effort,
            },
        )
        while True:
            if self.disabled_reason:
                raise APIUnavailable(self.disabled_reason)
            if self.config.remaining <= 0.2:
                raise TimeoutError("Task deadline reached before model request")
            delay = self.blocked_until - time.monotonic()
            if delay > 0:
                await asyncio.sleep(min(delay, self.config.remaining))
                continue
            await asyncio.wait_for(self.slots.acquire(), timeout=self.config.remaining)
            if self.blocked_until > time.monotonic():
                self.slots.release()
                continue
            break
        try:
            if self.disabled_reason:
                raise APIUnavailable(self.disabled_reason)
            self.trace(ident, "request_started")
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    **(
                        {"temperature": self.config.temperature}
                        if self.config.temperature is not None
                        else {}
                    ),
                    **({"tools": tools, "parallel_tool_calls": False} if tools else {}),
                    **({"extra_body": extra} if extra else {}),
                    reasoning_effort=effort,
                ),
                timeout=min(self.config.request_timeout, self.config.remaining),
            )
            if getattr(self, "log_event", None):
                self.trace(ident, "response", response.model_dump(exclude_none=True))
            usage = response.usage.model_dump() if response.usage else {}
            if usage.get("prompt_tokens"):
                self.ratios.append(
                    usage["prompt_tokens"] / max(1, self.payload_bytes(messages, tools))
                )
            if not response.choices:
                raise ResponseProblem("malformed", "Provider returned no choices", usage=usage)
            choice = response.choices[0]
            raw = choice.message.model_dump(exclude_none=True)
            if choice.finish_reason == "length":
                raise ResponseProblem(
                    "incomplete",
                    "Provider truncated generation. No tool calls executed. "
                    "Keep reasoning focused and write long code in several file operations.",
                    raw,
                    usage,
                )
            if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
                raise ResponseProblem(
                    "refusal", "Provider did not produce an actionable response", raw, usage
                )
            message = choice.message
            data = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                calls, seen = [], set()
                for call in message.tool_calls:
                    if (
                        not call.id
                        or call.id in seen
                        or not call.function.name
                        or not isinstance(call.function.arguments, str)
                    ):
                        raise ResponseProblem(
                            "malformed",
                            "Invalid/duplicate tool call identity or payload",
                            raw,
                            usage,
                        )
                    seen.add(call.id)
                    # Reject the whole generation before executing any side effect if one call is cut off.
                    try:
                        normalize_arguments(call.function.arguments)
                    except (ValueError, TypeError) as error:
                        raise ResponseProblem(
                            "malformed", f"Tool arguments are not valid JSON: {error}", raw, usage
                        ) from error
                    calls.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                    )
                data["tool_calls"] = calls
            if not data.get("tool_calls") and not data["content"].strip():
                raise ResponseProblem(
                    "empty",
                    "Empty response. Choose an action, send a finding or explicitly wait.",
                    raw,
                    usage,
                )
            self.failures = 0
            return data, usage
        except asyncio.CancelledError:
            self.trace(ident, "cancelled")
            raise
        except Exception as error:
            kind = failure_kind(error)
            self.learn_capacity(error)
            if kind.category not in ("context", "incomplete", "malformed", "empty", "refusal"):
                self.failures += 1
            if not kind.retryable and kind.shared:
                self.disabled_reason = "Model API unavailable: " + kind.category
            elif kind.shared:
                self.blocked_until = max(
                    self.blocked_until, time.monotonic() + backoff(kind, self.failures)
                )
            detail = (
                str(error).replace(self.config.api_key, "[redacted]")
                if self.config.api_key
                else str(error)
            )
            self.trace(ident, "request_failed", {"category": kind.category, "detail": detail})
            raise
        finally:
            self.slots.release()

    async def close(self):
        await self.client.close()


def normalize_arguments(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return value
