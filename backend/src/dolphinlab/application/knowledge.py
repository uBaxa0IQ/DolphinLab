"""Canonical project memory and reproducible context selection, without embeddings."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.api.dependencies import require_project
from dolphinlab.api.presenters import goal_view
from dolphinlab.domain.policy import ProjectPolicy
from dolphinlab.infrastructure.database import (
    ContextManifest,
    GoalRevision,
    InputFile,
    Message,
    ProjectMemory,
    ProjectPolicyRecord,
    ResearchIteration,
    ResearchObject,
    Run,
)

from .base import Service
from .research import latest_goal, latest_iteration

TERMINAL = {"completed", "stopped", "failed"}


def latest_inputs(db, project_id):
    history = db.scalars(
        select(InputFile)
        .where(InputFile.project_id == project_id)
        .order_by(InputFile.created, InputFile.id)
    ).all()
    return list({item.name: item for item in history}.values())


def bind_message(db, project_id):
    goal, iteration = latest_goal(db, project_id), latest_iteration(db, project_id)
    run = db.scalar(
        select(Run)
        .where(Run.project_id == project_id)
        .order_by(Run.created.desc(), Run.id.desc())
        .limit(1)
    )
    active = run if run and run.status not in TERMINAL else None
    current_input_ids = [item.id for item in latest_inputs(db, project_id)]
    run_input_ids = [item["id"] for item in run.inputs if item.get("id")] if run else []
    return {
        "schema_version": 1,
        "goal_revision_id": goal.id if goal else None,
        "goal_revision": goal.revision if goal else 0,
        "iteration_id": active.iteration_id if active else (iteration.id if iteration else None),
        "run_id": active.id if active else None,
        "evidence_run_id": run.id if run else None,
        "last_iteration_id": iteration.id if iteration else None,
        "last_iteration_sequence": iteration.sequence if iteration else 0,
        "input_ids": list(dict.fromkeys([*current_input_ids, *run_input_ids])),
        "current_input_ids": current_input_ids,
        "run_input_ids": run_input_ids,
    }


def memory_view(record, *, current=True, superseded=False):
    return {
        "id": record.id,
        "text": record.text,
        "kind": record.kind,
        "scope": record.scope,
        "status": record.status,
        "goal_revision_id": record.goal_revision_id,
        "message_id": record.message_id,
        "source_quote": record.source_quote,
        "query_id": record.source_action_id,
        "input_ids": record.input_ids,
        "supersedes_id": record.supersedes_id,
        "current": current,
        "superseded": superseded,
        "created": record.created.isoformat(),
    }


def memory_records(db, project_id, goal_id, input_ids, *, include_stale=False, offset=0, limit=100):
    records = list(
        db.scalars(
            select(ProjectMemory)
            .where(ProjectMemory.project_id == project_id)
            .order_by(ProjectMemory.created.desc(), ProjectMemory.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    superseded = set(
        db.scalars(
            select(ProjectMemory.supersedes_id).where(
                ProjectMemory.project_id == project_id,
                ProjectMemory.supersedes_id.in_([item.id for item in records]),
            )
        )
    )
    output = []
    for item in records:
        current = (
            (item.scope == "project" or item.goal_revision_id == goal_id)
            and set(item.input_ids).issubset(input_ids)
            and item.id not in superseded
        )
        if current or include_stale:
            output.append(memory_view(item, current=current, superseded=item.id in superseded))
    return output


def run_context(db, project_id, goal_id, input_ids, parent_iteration_id):
    """Freeze selected memory and earlier-run references at Run creation."""
    from dolphinlab.domain.research import GoalSpec

    from .result_context import selected_results

    goal = db.get(GoalRevision, goal_id)
    chosen = selected_results(
        db, project_id, GoalSpec.model_validate(goal.spec).prior_results, input_ids
    )
    ancestors, previous_runs, ancestor_goals = [], [], set()
    while parent_iteration_id and len(ancestors) < 10:
        item = db.scalar(
            select(ResearchIteration).where(
                ResearchIteration.project_id == project_id,
                ResearchIteration.id == parent_iteration_id,
            )
        )
        if not item or item.id in ancestors:
            break
        ancestors.append(item.id)
        ancestor_goals.add(item.goal_revision_id)
        previous_runs.extend(
            db.scalars(
                select(Run)
                .where(
                    Run.iteration_id == item.id,
                    Run.status.in_(TERMINAL),
                )
                .order_by(Run.created.desc(), Run.id.desc())
            ).all()
        )
        parent_iteration_id = item.parent_iteration_id
    references = [
        {
            "id": run.id,
            "iteration_id": run.iteration_id,
            "status": run.status,
            "goal": run.goal_snapshot,
            "publication": run.publication,
        }
        for run in previous_runs[:5]
    ]
    findings = []
    for record in db.scalars(
        select(ResearchObject)
        .where(
            ResearchObject.run_id.in_([item["id"] for item in references]),
            ResearchObject.kind == "finding",
        )
        .order_by(ResearchObject.seq.desc())
        .limit(15)
    ):
        data = record.data
        findings.append(
            {
                "run_id": record.run_id,
                "id": record.object_id,
                "text": str(data.get("text", ""))[:4000],
                "claim_status": data.get("claim_status", "reported"),
                "references": data.get("references", []),
            }
        )
    historical = [
        record
        for record in memory_records(db, project_id, goal_id, input_ids, include_stale=True)
        if not record["current"]
        and not record["superseded"]
        and record["goal_revision_id"] in ancestor_goals
    ][:10]
    return {
        "schema_version": 1,
        "memory": memory_records(db, project_id, goal_id, input_ids)[:20],
        "historical_memory": historical,
        "previous_runs": references,
        "selected_results": chosen,
        "findings": findings,
        "ancestor_iteration_ids": ancestors,
        "notice": "Historical claims retain their status; check applicability and exact evidence.",
    }


class KnowledgeService(Service):
    def list_memory(self, project_id, *, include_stale=False, offset=0):
        with self.sessions() as db:
            require_project(db, project_id)
            goal = latest_goal(db, project_id)
            return memory_records(
                db,
                project_id,
                goal.id if goal else None,
                {item.id for item in latest_inputs(db, project_id)},
                include_stale=include_stale,
                offset=offset,
            )

    def context(self, message_id, *, history_hash=""):
        from .result_context import result_index

        with self.sessions.begin() as db:
            source = db.get(Message, message_id)
            if not source:
                raise HTTPException(404, "Сообщение не найдено")
            project = require_project(db, source.project_id)
            binding = source.context_binding or {}
            goal = db.scalar(
                select(GoalRevision).where(
                    GoalRevision.id == binding.get("goal_revision_id"),
                    GoalRevision.project_id == project.id,
                )
            )
            current_goal = latest_goal(db, project.id)
            record = db.get(ProjectPolicyRecord, project.id)
            policy = (
                ProjectPolicy(revision=record.revision, **record.policy)
                if record
                else ProjectPolicy()
            )
            run_id = binding.get("run_id") or binding.get("evidence_run_id")
            run = (
                db.scalar(select(Run).where(Run.id == run_id, Run.project_id == project.id))
                if run_id
                else None
            )
            inputs = db.scalars(
                select(InputFile).where(
                    InputFile.project_id == project.id,
                    InputFile.id.in_(binding.get("input_ids", [])),
                )
            ).all()
            memory = memory_records(
                db, project.id, goal.id if goal else None, set(binding.get("input_ids", []))
            )[:20]
            iterations = db.scalars(
                select(ResearchIteration)
                .where(
                    ResearchIteration.project_id == project.id,
                )
                .order_by(ResearchIteration.sequence.desc())
                .limit(5)
            ).all()
            recent = db.scalars(
                select(Message)
                .where(
                    Message.project_id == project.id,
                    Message.role == "user",
                    Message.id != source.id,
                    Message.created <= source.created,
                )
                .order_by(Message.created.desc(), Message.id.desc())
                .limit(6)
            ).all()
            previous_runs = db.scalars(
                select(Run)
                .where(
                    Run.project_id == project.id,
                    Run.created <= source.created,
                )
                .order_by(Run.created.desc(), Run.id.desc())
                .limit(10)
            ).all()
            snapshot = {
                "schema_version": 1,
                "project": {"id": project.id, "name": project.name},
                "source_message": {
                    "id": source.id,
                    "intent": source.intent,
                    "binding": binding,
                    "content": source.content,
                },
                "bound_goal": goal_view(goal) if goal else None,
                "current_goal": goal_view(current_goal) if current_goal else None,
                "policy": policy.model_dump(mode="json"),
                "inputs": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "sha256": item.sha256,
                        "size": item.size,
                        "in_current_uploads": item.id
                        in binding.get("current_input_ids", binding.get("input_ids", [])),
                        "in_run_snapshot": item.id in binding.get("run_input_ids", []),
                    }
                    for item in inputs
                ],
                "memory": [
                    {
                        **item,
                        "text": item["text"][:1200],
                        "source_quote": (item["source_quote"] or "")[:400],
                        "source_quote_clipped": len(item["source_quote"] or "") > 400,
                        "text_clipped": len(item["text"]) > 1200,
                    }
                    for item in memory
                ],
                "memory_selection": {
                    "candidate_limit": 100,
                    "context_limit": 20,
                    "archive_tool": "list_memory with offset/limit",
                },
                "iterations": [
                    {
                        "id": item.id,
                        "sequence": item.sequence,
                        "goal_revision_id": item.goal_revision_id,
                    }
                    for item in iterations
                ],
                "recent_user_messages": [
                    {
                        "id": item.id,
                        "intent": item.intent,
                        "status": item.status,
                        "excerpt": item.content[:2000],
                        "clipped": len(item.content) > 2000,
                    }
                    for item in reversed(recent)
                ],
                "history_runs": [
                    {
                        "id": item.id,
                        "iteration_id": item.iteration_id,
                        "status": item.status,
                        "task_excerpt": item.task_description[:400],
                        "results": result_index(item.id),
                    }
                    for item in previous_runs
                ],
                "run": {
                    "id": run.id,
                    "state": run.status,
                    "iteration_id": run.iteration_id,
                    "task": run.task_description,
                    "policy": run.policy,
                    "snapshot": run.snapshot,
                    "usage": run.usage,
                    "error": run.error,
                }
                if run
                else None,
                "history_hash": history_hash,
            }
            digest = hashlib.sha256(
                json.dumps(snapshot, sort_keys=True, default=str).encode()
            ).hexdigest()
            ident = str(uuid5(NAMESPACE_URL, source.id + ":context:" + digest))
            if not db.get(ContextManifest, ident):
                db.add(
                    ContextManifest(
                        id=ident, project_id=project.id, message_id=source.id, snapshot=snapshot
                    )
                )
            return {**snapshot, "context_manifest_id": ident}
