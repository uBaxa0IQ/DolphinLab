"""Reusable LangGraph session boundary; deliberately not wired into the preview API.

The worker supplies a durable checkpointer, a context builder, a model adapter and
a tool dispatcher with persistent receipts. No network clients are created here.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from .project_policy import context_message


class AgentState(TypedDict, total=False):
    agent_id: str
    role: str
    history: list[dict[str, Any]]
    context: list[dict[str, Any]]
    response: dict[str, Any]
    summary: str
    waiting: bool
    project_policy: dict[str, Any]
    source_message_id: str


ContextBuilder = Callable[[AgentState], Awaitable[list[dict] | dict[str, Any]]]
ModelCall = Callable[[AgentState], Awaitable[dict]]
ToolDispatcher = Callable[[str, dict], Awaitable[dict]]


def build_agent_graph(
    *,
    checkpointer,
    prepare: ContextBuilder,
    complete: ModelCall,
    dispatch: ToolDispatcher,
    one_turn=False,
):
    if checkpointer is None:
        raise ValueError(
            "An explicit durable checkpointer is required for worker sessions"
        )

    async def context(state: AgentState):
        prepared = await prepare(state)
        updates = (
            dict(prepared) if isinstance(prepared, dict) else {"context": prepared}
        )
        messages = updates["context"]
        if state.get("project_policy"):
            # Worker supplies a frozen policy snapshot when creating a run.
            # The future scheduler must independently enforce these ceilings.
            messages = [*messages, context_message(state["project_policy"])]
        return {**updates, "context": messages}

    async def model(state: AgentState):
        response = await complete(state)
        return {"response": response, "history": [*state.get("history", []), response]}

    async def tools(state: AgentState):
        history = list(state["history"])
        for call in state["response"].get("tool_calls", []):
            # Dispatcher reconciles durable receipts by (agent_id, call_id).
            # Unknown in-flight effects must be reconciled rather than replayed.
            result = await dispatch(state["agent_id"], call)
            import json

            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return {"history": history}

    def next_node(state: AgentState):
        return "tools" if state["response"].get("tool_calls") else END

    def after_tools(state: AgentState):
        return END if one_turn or state.get("waiting") else "context"

    graph = StateGraph(AgentState)
    graph.add_node("context", context)
    graph.add_node("model", model)
    graph.add_node("tools", tools)
    graph.add_edge(START, "context")
    graph.add_edge("context", "model")
    graph.add_conditional_edges("model", next_node, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", after_tools, {"context": "context", END: END})
    return graph.compile(checkpointer=checkpointer)
