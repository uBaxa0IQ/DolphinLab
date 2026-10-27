"""Working context with durable originals, proactive capacity guards and semantic compaction."""

from __future__ import annotations

import json

from sonder.common.serialization import dumps, uid
from sonder.infrastructure.llm import is_context_error, request_purpose
from sonder.research.roles import prompt


def blocks(history):
    """Group whole call/result exchanges; never manufacture orphaned tool messages."""
    result, current, pending = [], [], set()
    for message in history:
        if not pending and message["role"] == "user" and current:
            result.append(current)
            current = []
        current.append(message)
        if message.get("tool_calls"):
            pending.update(c["id"] for c in message["tool_calls"])
        elif message["role"] == "tool":
            pending.discard(message["tool_call_id"])
        if not pending and message["role"] in ("assistant", "tool"):
            result.append(current)
            current = []
    if pending:
        raise ValueError("Cannot compact an unfinished tool exchange")
    if current:
        result.append(current)
    return result


def archive_context(agent, store, reason):
    folder = store.root / "transcripts" / agent["id"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uid("context") + ".json")
    path.write_text(
        dumps(
            {
                "agent": agent["id"],
                "epoch": agent.get("epoch", 0),
                "reason": reason,
                "summary": agent.get("summary", ""),
                "note": agent.get("note", ""),
                "history": agent["history"],
            }
        ),
        encoding="utf-8",
    )
    store.event("context_archive", {"agent": agent["id"], "path": str(path), "reason": reason})
    return str(path)


def summary_view(parts, archive):
    """Generated source remains in files; summarize its role and evidence, not every code token."""
    view = json.loads(dumps(parts))
    for group in view:
        for message in group:
            for call in message.get("tool_calls", []):
                try:
                    args = json.loads(call["function"]["arguments"])
                    if call["function"]["name"] in ("python_job", "run_python") and "code" in args:
                        args["code"] = {"source_archived_at": archive, "tool_call_id": call["id"]}
                    if call["function"]["name"] in ("write_file", "append_file") or (
                        call["function"]["name"] == "files"
                        and args.get("action") in ("write", "append")
                    ):
                        args["content"] = {"file": args.get("path"), "original_call_at": archive}
                    call["function"]["arguments"] = dumps(args)
                except (ValueError, KeyError, TypeError):
                    pass
    return view


def archive_old_payloads(agent, store, keep_recent=6, payload_bytes=16000):
    """Externalize consumed code/file payloads, not task instructions or scientific conclusions.

    Keep recent complete exchanges verbatim so newly read material can be used. Older
    exchanges retain their actual results and artifact locations; exact bytes remain archived.
    This is working-set management, not a model input/output token ceiling.
    """
    parts = blocks(agent["history"])
    if len(parts) <= keep_recent:
        return
    candidates = []
    payload_tools = {
        "run_python",
        "run_script",
        "write_file",
        "append_file",
        "read_file",
        "job_status",
        "python_job",
        "files",
        "inspect_team",
        "read_memory",
        "recall",
        "inspect_candidate",
        "list_candidates",
        "inspect_protocol",
    }
    for index, part in enumerate(parts[:-keep_recent]):
        calls = [c for m in part for c in m.get("tool_calls", [])]
        if (
            calls
            and any(c["function"]["name"] in payload_tools for c in calls)
            and len(dumps(part).encode("utf-8")) > payload_bytes
        ):
            candidates.append(index)
    if not candidates:
        return
    archive = archive_context(agent, store, "consumed code/data payloads")
    replaced = 0
    for index in candidates:
        part = parts[index]
        calls = {c["id"]: c for m in part for c in m.get("tool_calls", [])}
        records = []
        for message in part:
            if message["role"] == "user":
                records.append({"original_instruction": message["content"]})
            elif message["role"] == "assistant" and message.get("content"):
                records.append({"assistant_statement_unverified": message["content"]})
            if message["role"] == "tool":
                call = calls[message["tool_call_id"]]
                name = call["function"]["name"]
                arguments = json.loads(call["function"]["arguments"])
                for key in ("code", "content"):
                    if key in arguments:
                        arguments[key] = {"exact_original": archive, "call_id": call["id"]}
                try:
                    result = json.loads(message["content"])
                except ValueError:
                    result = {"original_at": archive}
                if isinstance(result, dict):
                    for key in ("text", "stdout_tail", "stderr_tail"):
                        if key in result and len(str(result[key]).encode("utf-8")) > payload_bytes:
                            result[key] = {"exact_original": archive, "call_id": call["id"]}
                if len(dumps(result).encode("utf-8")) > payload_bytes:
                    # Registry/board snapshots were a major manager-history leak. Their exact
                    # contents are archival observations; current state is supplied afresh.
                    result = {
                        "exact_original": archive,
                        "call_id": call["id"],
                        "fields": list(result) if isinstance(result, dict) else None,
                        "record_count": len(result) if isinstance(result, list) else None,
                        "lookup": "Use current board/registry or reread the archived tool result for historical detail",
                    }
                records.append(
                    {"completed_tool": name, "arguments": arguments, "observed_result": result}
                )
        content = (
            "Previously completed tool exchange. Exact original: " + archive + "\n" + dumps(records)
        )
        if len(content) < len(dumps(part)):
            parts[index] = [{"role": "user", "content": content}]
            replaced += 1
    if replaced:
        agent["history"] = [message for part in parts for message in part]
        agent["last_archive"] = archive
        store.put("agent", agent["id"], agent)
        store.event(
            "payloads_archived", {"agent": agent["id"], "archive": archive, "exchanges": replaced}
        )


SUMMARY_INSTRUCTIONS = """Produce working memory for a continuing autonomous ML researcher.
Preserve the research objective, decisions and their reasons, measured observations with evidence
paths, failed hypotheses, unresolved questions, latest applicable assignments and active job IDs.
Distinguish measurements, colleague reports and conjecture. Preserve row/split/metric conventions
if the researchers established them. Do not invent facts or silently resolve disagreements.
Source code itself lives in files: retain its purpose and paths instead of reproducing it.
Original task, current team board, notes, job registry and candidate records remain separately
available. Older summaries are fallible navigation aids. Cite original archives for detail.
Return a concise useful note, with pending actions clearly visible. Prefer unresolved questions,
decisions, evidence IDs/paths and reasons over chronology. Omit duplicate/current board listings,
full messages, repeated scores and copied code. Current facts are supplied separately next turn.
Reduce the previous summary too; do not just append another episode. No tools in this request.
"""


async def summarize(client, text, depth=0):
    messages = [
        {"role": "system", "content": SUMMARY_INSTRUCTIONS},
        {"role": "user", "content": text},
    ]
    status = getattr(client, "context_status", lambda *_: {"needs_compaction": False})(messages)
    split = status["needs_compaction"]
    if not split:
        try:
            purpose = request_purpose.set("compaction")
            try:
                message, usage = await client.complete(messages)
            finally:
                request_purpose.reset(purpose)
            note = message.get("content", "").strip()
            if not note:
                raise ValueError("Empty summary; original context retained")
            return note, [usage]
        except Exception as error:
            if not is_context_error(error):
                raise
            split = True
    if depth >= 10 or len(text) < 2:
        raise ValueError("Compaction could not fit provider capacity; originals retained")
    # Split near a line boundary. A single large payload can still be handled as text fragments;
    # the exact original remains in the archive rather than being cut out of durable history.
    middle = len(text) // 2
    newline = text.rfind("\n", middle // 2, middle)
    middle = newline + 1 if newline >= 0 else middle
    first, usage1 = await summarize(client, text[:middle], depth + 1)
    second, usage2 = await summarize(client, text[middle:], depth + 1)
    combined = "Earlier fragment:\n" + first + "\nLater fragment:\n" + second
    if len(combined) >= len(text):
        raise ValueError("Summary did not reduce an overflowing source; originals retained")
    note, usage3 = await summarize(client, combined, depth + 1)
    return note, usage1 + usage2 + usage3


async def compact(agent, client, store, facts=None, aggressive=False):
    parts = blocks(agent["history"])
    if not parts:
        raise ValueError("No conversation phase to compact")
    retained = min(3, len(parts) - 1) if not aggressive else 0
    older = parts[:-retained] if retained else parts
    tail = [m for part in parts[-retained:] for m in part] if retained else []
    archive = archive_context(
        agent, store, "capacity recovery" if aggressive else "working memory compaction"
    )
    source = dumps(
        {
            "original_archive": archive,
            "previous_note": agent.get("summary", ""),
            "personal_note": agent.get("note", ""),
            "current_facts": facts or {},
            "completed_conversation": summary_view(older, archive),
        }
    )
    note, usages = await summarize(client, source)
    original_size = len(dumps(older).encode("utf-8")) + len(
        agent.get("summary", "").encode("utf-8")
    )
    for usage in usages:
        store.event("model_usage", {"agent": agent["id"], "purpose": "compaction", "usage": usage})
    if len(note.encode("utf-8")) >= original_size:
        store.event(
            "compaction_ineffective",
            {
                "agent": agent["id"],
                "archive": archive,
                "before_bytes": original_size,
                "summary_bytes": len(note.encode("utf-8")),
            },
        )
        agent["last_compaction_turn"] = agent.get("turn_count", 0)
        raise ValueError("Summary did not reduce working memory; original history retained")
    # Commit only after a complete valid summary. Any failure leaves the original active history.
    agent["summary"] = note
    agent["history"] = tail
    agent["last_archive"] = archive
    agent["last_compaction_turn"] = agent.get("turn_count", 0)
    agent["phase_pending"] = False
    agent["last_compaction"] = {
        "before_memory_bytes": original_size,
        "summary_bytes": len(note.encode("utf-8")),
        "retained_tail_bytes": len(dumps(tail).encode("utf-8")),
        "archive": archive,
    }
    store.put("agent", agent["id"], agent)
    store.event(
        "compaction", {"agent": agent["id"], "aggressive": aggressive, **agent["last_compaction"]}
    )


def context_status(client, messages, tools):
    method = getattr(client, "context_status", None)
    return method(messages, tools) if method else {"needs_compaction": False, "window_tokens": None}


def refresh_context_status(agent, state, client, tools):
    """Current next-request estimate, distinct from actual tokens in the previous API call."""
    messages = build_context(agent, state)
    status = context_status(client, messages, tools)
    status.update(
        system_bytes=len(messages[0]["content"].encode("utf-8")),
        state_bytes=len(messages[-1]["content"].encode("utf-8")),
        history_bytes=len(dumps(agent["history"]).encode("utf-8")),
        summary_bytes=len(agent.get("summary", "").encode("utf-8")),
        tool_schema_bytes=len(dumps(tools).encode("utf-8")),
        last_actual_prompt_tokens=agent.get("last_actual_prompt_tokens"),
    )
    agent["context_status"] = status
    return messages


def should_compact(agent, state, config):
    """A working-set policy, not an input/output token ceiling. Applies to every role."""
    since = agent.get("turn_count", 0) - agent.get("last_compaction_turn", 0)
    history_bytes = len(dumps(agent["history"]).encode("utf-8"))
    pinned_bytes = len(
        dumps(build_context({**agent, "history": [], "summary": ""}, state)).encode("utf-8")
    )
    threshold = max(config.context_history_bytes, pinned_bytes // 2)
    return history_bytes > threshold and (
        since >= config.context_compact_turns
        or (agent.get("phase_pending") and since >= max(8, config.context_compact_turns * 3 // 4))
    )


async def prepare_context(agent, state, client, config, store, tools):
    archive_old_payloads(
        agent, store, config.context_recent_exchanges, config.context_payload_bytes
    )
    messages = build_context(agent, state)
    if config.remaining > 30 and should_compact(agent, state, config):
        try:
            await compact(
                agent,
                client,
                store,
                facts={
                    k: state[k]
                    for k in ("current_focus", "team", "published", "portfolio_plan")
                    if k in state
                },
            )
            messages = build_context(agent, state)
        except Exception as error:
            store.event(
                "phase_compaction_failed",
                {"agent": agent["id"], "error_type": type(error).__name__},
            )
    status = context_status(client, messages, tools)
    agent["context_status"] = status
    if status["needs_compaction"]:
        facts = {
            k: state[k]
            for k in ("team_board", "personal_note", "global_note", "published")
            if k in state
        }
        await compact(agent, client, store, facts=facts)
        messages = build_context(agent, state)
        if context_status(client, messages, tools)["needs_compaction"] and agent["history"]:
            await compact(agent, client, store, facts=facts, aggressive=True)
            messages = build_context(agent, state)
        if context_status(client, messages, tools)["needs_compaction"]:
            # A large shared notebook must not make an otherwise fresh session unusable. Keep
            # complete notes on disk and expose their locations plus factual indexes. The original
            # task and stack remain verbatim; the semantic working note remains in context.
            folder = store.root / "transcripts" / agent["id"]
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (uid("state") + ".json")
            path.write_text(dumps(state), encoding="utf-8")
            state = dict(state)
            for field in ("personal_note", "global_note"):
                state[field] = {"full_note": str(path), "field": field}
            if state.get("team_board"):
                board = state["team_board"]
                state["team_board"] = {
                    "charter": {
                        k: board["charter"].get(k)
                        for k in ("id", "revision", "goal", "scope", "out_of_scope", "state")
                    },
                    "members": [
                        {k: m.get(k) for k in ("id", "role", "state", "focus_revision")}
                        for m in board["members"]
                    ],
                    "finding_ids": [f["id"] for f in board["findings"]],
                    "full_board": str(path),
                }
            state["team"] = (
                {k: state["team"].get(k) for k in ("id", "revision", "goal", "state")}
                if state.get("team")
                else None
            )
            state["full_state_archive"] = str(path)
            messages = build_context(agent, state)
            if context_status(client, messages, tools)["needs_compaction"]:
                raise ValueError(
                    "Pinned task/state exceed confirmed capacity even after archiving notes; originals retained"
                )
    return refresh_context_status(agent, state, client, tools)


def build_context(agent, state):
    stable = "\nORIGINAL TASK (verbatim):\n" + state.get("original_task", "")
    stable += "\nALLOWED STACK:\n" + state.get("allowed_stack", "")
    dynamic = {k: v for k, v in state.items() if k not in ("original_task", "allowed_stack")}
    # Keep changing boards/clocks out of the conversation prefix. A provider that caches
    # prefixes can reuse the stable instructions/history; no cache behavior is assumed.
    system = prompt(agent["role"]) + stable
    messages = [{"role": "system", "content": system}]
    if agent.get("summary"):
        messages.append(
            {
                "role": "user",
                "content": "Working memory; verify facts against evidence:\n" + agent["summary"],
            }
        )
    messages.extend(agent["history"])
    messages.append(
        {
            "role": "user",
            "content": "CURRENT RUNTIME STATE (observations, notes and colleague claims retain their stated evidence/status):\n"
            + dumps(dynamic),
        }
    )
    return messages
