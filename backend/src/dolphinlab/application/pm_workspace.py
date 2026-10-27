"""PM's project-scoped, transactional tools. No tool starts a research Run."""

import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select

from dolphinlab.domain.conversation import (
    DataQuestion,
    FollowupSpec,
    MemoryNote,
    MessageRoute,
    ResearchInstruction,
)
from dolphinlab.domain.research import GoalSpec
from dolphinlab.infrastructure.database import (
    DATA_ROOT,
    Directive,
    Event,
    InputFile,
    Message,
    PMAction,
    Project,
    ProjectMemory,
    Run,
)
from dolphinlab.infrastructure.tabular_questions import calculate

from .base import Service
from .knowledge import TERMINAL, memory_records
from .research import append_goal, append_iteration, latest_goal, latest_iteration


class PMWorkspace(Service):
    def dispatch(self, message_id, name, arguments):
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        key = name if name in ("prepare_followup", "send_to_tech_lead") else name + canonical
        ident = str(uuid5(NAMESPACE_URL, message_id + ":pm-action:" + key))
        with self.sessions.begin() as db:
            source = db.get(Message, message_id)
            if not source or source.role != "user":
                raise ValueError("Unknown source message")
            project = db.scalar(
                select(Project).where(Project.id == source.project_id).with_for_update()
            )
            if project.archived:
                raise ValueError("Project is archived")
            db.refresh(source)
            existing = db.get(PMAction, ident)
            if existing:
                if existing.arguments != arguments:
                    raise ValueError("This message already has a different action of this kind")
                return {**existing.result, "action_id": ident}
            try:
                with db.begin_nested():
                    result = self._perform(db, source, project, name, arguments, ident)
                    if len(json.dumps(result, ensure_ascii=False).encode()) > 64000:
                        raise ValueError("Результат слишком большой: запроси меньшую страницу")
            except (ValueError, HTTPException) as error:
                result = {"error": str(error.detail if isinstance(error, HTTPException) else error)}
            # Effects and receipts commit together. Every operation is DB-only or a bounded read.
            db.add(
                PMAction(
                    id=ident,
                    project_id=source.project_id,
                    message_id=source.id,
                    kind=name,
                    arguments=arguments,
                    result=result,
                )
            )
            db.add(
                Event(
                    project_id=project.id,
                    kind="pm_action",
                    payload={"message_id": source.id, "action_id": ident, "name": name},
                )
            )
            return {**result, "action_id": ident}

    def _perform(self, db, source, project, name, arguments, action_id):
        binding = source.context_binding or {}
        if name == "route_message":
            route = MessageRoute.model_validate(arguments)
            if binding.get("requested_intent", source.intent) != "auto" or source.intent != "auto":
                raise ValueError("Режим уже определён или задан пользователем")
            if route.intent == "instruction":
                run = db.scalar(
                    select(Run).where(Run.id == binding.get("run_id"), Run.project_id == project.id)
                )
                if not run or run.status in TERMINAL:
                    raise ValueError(
                        "Нет активного адресата: уточни намерение или предложи продолжение"
                    )
                db.add(
                    Directive(
                        id=action_id,
                        project_id=project.id,
                        message_id=source.id,
                        content=source.content,
                        state="awaiting_pm",
                        run_id=run.id,
                        iteration_id=run.iteration_id,
                    )
                )
            source.intent = route.intent
            source.directive = route.intent == "instruction"
            source.context_binding = {**binding, "intent_reason": route.reason}
            return {"intent": route.intent, "reason": route.reason, "run_started": False}
        if name == "query_data":
            query = DataQuestion.model_validate(arguments)
            count = db.scalar(
                select(func.count())
                .select_from(PMAction)
                .where(
                    PMAction.message_id == source.id,
                    PMAction.kind == "query_data",
                )
            )
            if count >= 4:
                raise ValueError("Лимит четырёх быстрых расчётов на сообщение исчерпан")
            item, path = self._input(db, source, str(query.input_id))
            result = calculate(path, item.name, item.sha256, query)
            if len(json.dumps(result, ensure_ascii=False).encode()) > 32000:
                raise ValueError("Ответ слишком большой: уменьши limit или подготовь исследование")
            return result
        if name == "read_input":
            item, path = self._input(db, source, str(arguments["input_id"]))
            if Path(item.name).suffix.lower() not in (
                ".txt",
                ".md",
                ".csv",
                ".tsv",
                ".json",
                ".jsonl",
            ):
                raise ValueError(
                    "Этот формат требует обработки; доступно чтение текстовых источников"
                )
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("Источник слишком большой для быстрого чтения")
            if hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256:
                raise ValueError("Хеш источника изменился")
            return {
                **self._read(path, arguments),
                "input_id": item.id,
                "name": item.name,
                "sha256": item.sha256,
            }
        if name in ("read_artifact", "list_artifacts"):
            run_id = arguments.get("run_id") or binding.get("evidence_run_id")
            run = db.scalar(select(Run).where(Run.project_id == project.id, Run.id == run_id))
            if not run or run.created > source.created:
                raise ValueError("Нет доступного запуска с этим ID на момент вопроса")
            root = (Path(os.getenv("RUNS_ROOT", "/data/runs")) / run.id).resolve()
            if name == "list_artifacts":
                files = []
                for index, candidate in enumerate(root.rglob("*")):
                    if index >= 2000 or len(files) >= 100:
                        return {"run_id": run.id, "files": files, "truncated": True}
                    if (
                        candidate.is_file()
                        and not candidate.is_symlink()
                        and candidate.resolve().is_relative_to(root)
                    ):
                        files.append(
                            {
                                "path": str(candidate.relative_to(root)),
                                "size": candidate.stat().st_size,
                            }
                        )
                return {"run_id": run.id, "files": files, "truncated": False}
            path = (root / arguments["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("Неизвестный артефакт этого запуска")
            return {
                **self._read(path, arguments),
                "run_id": run.id,
                "path": str(path.relative_to(root)),
            }
        if name == "list_memory":
            offset = max(0, int(arguments.get("offset", 0)))
            limit = min(100, max(1, int(arguments.get("limit", 30))))
            total = db.scalar(
                select(func.count())
                .select_from(ProjectMemory)
                .where(ProjectMemory.project_id == project.id)
            )
            return {
                "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None,
                "records": memory_records(
                    db,
                    project.id,
                    binding.get("goal_revision_id"),
                    set(binding.get("input_ids", [])),
                    include_stale=bool(arguments.get("include_stale", False)),
                    offset=offset,
                    limit=limit,
                ),
            }
        if name == "remember":
            note = MemoryNote.model_validate(arguments)
            query = None
            if note.kind == "user_statement":
                if not note.source_quote or note.source_quote not in source.content:
                    raise ValueError(
                        "Пользовательское утверждение требует точной цитаты исходного сообщения"
                    )
            if note.kind == "observation":
                query = (
                    db.scalar(
                        select(PMAction).where(
                            PMAction.project_id == project.id,
                            PMAction.id == str(note.query_id),
                            PMAction.kind == "query_data",
                        )
                    )
                    if note.query_id
                    else None
                )
                if not query or query.result.get("error"):
                    raise ValueError("Наблюдение требует ссылки на сохранённый расчёт проекта")
            if note.supersedes_id:
                previous = db.scalar(
                    select(ProjectMemory).where(
                        ProjectMemory.id == str(note.supersedes_id),
                        ProjectMemory.project_id == project.id,
                    )
                )
                if (
                    not previous
                    or previous.scope != note.scope
                    or (
                        note.scope == "goal"
                        and previous.goal_revision_id != binding.get("goal_revision_id")
                    )
                ):
                    raise ValueError("Заменяемая запись должна принадлежать этой области проекта")
            record = ProjectMemory(
                id=action_id,
                project_id=project.id,
                message_id=source.id,
                goal_revision_id=binding.get("goal_revision_id") if note.scope == "goal" else None,
                scope=note.scope,
                kind=note.kind,
                text=note.text,
                status="user_stated" if note.kind == "user_statement" else "reported",
                source_quote=note.source_quote if note.kind == "user_statement" else None,
                source_action_id=query.id if query else None,
                input_ids=[query.result["input_id"]] if query else [],
                supersedes_id=str(note.supersedes_id) if note.supersedes_id else None,
            )
            db.add(record)
            return {
                "memory_id": record.id,
                "status": record.status,
                "notice": "Working memory is attributed, not independent verification",
            }
        if name == "prepare_followup":
            if source.intent != "followup":
                raise ValueError(
                    "Продолжение требует определённого намерения followup; вопрос не создаёт итерацию"
                )
            followup = FollowupSpec.model_validate(arguments)
            current, previous = latest_goal(db, project.id), latest_iteration(db, project.id)
            if (
                not current
                or current.id != binding.get("goal_revision_id")
                or (previous.id if previous else None) != binding.get("last_iteration_id")
            ):
                raise ValueError(
                    "Цель или история изменились после сообщения. Уточни продолжение заново"
                )
            next_goal = GoalSpec.model_validate(
                {
                    **current.spec,
                    **followup.goal.model_dump(mode="json", exclude_unset=True),
                }
            )
            goal = append_goal(db, project, next_goal, origin="pm")
            project.description = next_goal.question
            iteration = append_iteration(db, project.id, goal.id)
            return {
                "goal_revision_id": goal.id,
                "goal_revision": goal.revision,
                "iteration_id": iteration.id,
                "sequence": iteration.sequence,
                "state": "prepared",
                "run_started": False,
            }
        if name == "send_to_tech_lead":
            if source.intent != "instruction":
                raise ValueError("Поручение требует определённого намерения instruction")
            instruction = ResearchInstruction.model_validate(arguments)
            run = db.scalar(
                select(Run).where(Run.id == binding.get("run_id"), Run.project_id == project.id)
            )
            if not run or run.status in TERMINAL:
                raise ValueError(
                    "У поручения нет активного адресата. Можно подготовить следующую итерацию"
                )
            directive = db.scalar(select(Directive).where(Directive.message_id == source.id))
            if not directive:
                raise ValueError("Поручение не зарегистрировано")
            if directive.state in ("awaiting_pm", "received", "needs_target"):
                directive.content = instruction.instruction
                directive.run_id, directive.iteration_id = run.id, run.iteration_id
                directive.state = "queued_for_tech_lead"
            return {
                "directive_id": directive.id,
                "state": directive.state,
                "run_id": directive.run_id,
                "iteration_id": directive.iteration_id,
                "decision": directive.decision,
            }
        raise ValueError("Unknown PM operation")

    @staticmethod
    def _input(db, source, input_id):
        if input_id not in (source.context_binding or {}).get("input_ids", []):
            raise ValueError("Файл не входит в версии данных этого вопроса")
        item = db.scalar(
            select(InputFile).where(
                InputFile.project_id == source.project_id, InputFile.id == input_id
            )
        )
        if not item:
            raise ValueError("Источник не найден")
        root = (DATA_ROOT / "projects" / source.project_id / "inputs").resolve()
        path = (root / item.stored_name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Файл источника недоступен")
        return item, path

    @staticmethod
    def _read(path, arguments):
        offset = max(0, int(arguments.get("offset", 0)))
        limit = min(16000, max(1, int(arguments.get("limit", 8000))))
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read(limit)
        return {
            "text": content.decode("utf-8", errors="replace"),
            "next_offset": offset + len(content),
            "size": path.stat().st_size,
        }
