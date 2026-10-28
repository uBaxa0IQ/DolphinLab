"""Editor queue, presentation freedom and preservation against PostgreSQL; no paid API."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import test_pages as pages
import test_research_storage as storage
from fastapi import HTTPException
from sqlalchemy import select

from dolphinlab.api.schemas import CreateMessage
from dolphinlab.application.editor import EditorService
from dolphinlab.application.messages import MessageService
from dolphinlab.application.pm_workspace import PMWorkspace
from dolphinlab.domain.pages import EditorPlan, PageFeedbackChange, PageSave
from dolphinlab.infrastructure.database import (
    EditorJob,
    ProjectPolicyRecord,
    Run,
    Session,
    WorkspaceSettings,
)
from dolphinlab.infrastructure.llm import ResponseProblem


@unittest.skipUnless(storage.TEST_URL, "Set disposable PostgreSQL TEST_DATABASE_URL")
class EditorTest(unittest.TestCase):
    reset = pages.PagesTest.reset
    start = pages.PagesTest.start
    dispatch = pages.PagesTest.dispatch
    team = pages.PagesTest.team
    member = pages.PagesTest.member
    runtime = pages.PagesTest.runtime
    job = pages.PagesTest.job
    register = pages.PagesTest.register
    table = pages.PagesTest.table
    assess = pages.PagesTest.assess
    report = pages.PagesTest.report
    status = pages.PagesTest.status

    @classmethod
    def setUpClass(cls):
        storage.ResearchStorageTest.setUpClass()

    def setUp(self):
        pages.PagesTest.setUp(self)
        self.assess(self.result)
        report = self.report([self.result])
        self.assess(report)
        publication = self.dispatch(
            self.rt.root, "publish_report", report_id=report["id"], outcome="complete"
        )
        with Session.begin() as db:
            run = db.get(Run, self.run["id"])
            run.status = "completed"
            run.publication = {
                "kind": "report",
                "report_id": report["id"],
                "page_id": publication["id"],
                "sha256": report["sha256"],
            }
            policy = db.get(ProjectPolicyRecord, self.pid)
            if not policy:
                policy = ProjectPolicyRecord(project_id=self.pid, revision=0, policy={})
                db.add(policy)
            policy.policy = {**policy.policy, "conversation": {"api_budget": 50}}
            db.get(WorkspaceSettings, "local").pricing = {
                "currency": "TEST",
                "input_per_million": 1,
                "output_per_million": 1,
                "max_input_tokens": 131072,
                "max_output_tokens": 8192,
            }
        self.service = EditorService()
        self.report_ref = report

    def plan(self):
        table = {
            **self.block,
            "presentation": {
                "heading": "Динамика по месяцам",
                "view": "chart",
                "chart": {
                    "type": "bar",
                    "x": "month",
                    "y": "amount",
                    "x_label": "Месяц",
                    "y_label": "RUB",
                },
            },
        }
        report = {
            **self.block,
            "id": str(uuid4()),
            "result_id": self.report_ref["id"],
            "sha256": self.report_ref["sha256"],
            "presentation": {"heading": "Вывод", "view": "summary"},
        }
        return EditorPlan.model_validate(
            {
                "pages": [
                    {
                        "kind": "team",
                        "run_id": self.run["id"],
                        "team_id": self.analysis["id"],
                        "page": {
                            "schema_version": 2,
                            "title": "Итоги продаж",
                            "lead": "Сравнение двух месяцев.",
                            "blocks": [table],
                        },
                    },
                    {
                        "kind": "main",
                        "page": {
                            "schema_version": 2,
                            "title": "Продажи выросли",
                            "layout": "dashboard",
                            "lead": "Рост составил 6,25%. Причинность не установлена.",
                            "blocks": [report, table],
                        },
                    },
                ]
            }
        )

    def process(self, plan, before_response=None, failure=None):
        calls = []

        class Fake:
            async def complete(_, messages, tools):
                calls.append(messages)
                if failure:
                    raise failure
                if before_response:
                    await asyncio.to_thread(before_response)
                return {"role": "assistant", "content": json.dumps(plan.model_dump(mode="json"))}, {
                    "prompt_tokens": 100,
                    "completion_tokens": 100,
                }

        factory = SimpleNamespace(create_conversation=lambda *_: Fake())
        asyncio.run(
            self.service.process_next(
                factory,
                lambda: {
                    "api_key": "fake",
                    "base_url": "https://example.invalid/v1",
                    "model": "fake",
                },
            )
        )
        return calls

    def queue(self):
        return self.service.queue(self.pid, self.run["id"], str(uuid4()))

    def test_one_editor_builds_direction_and_main_once_without_changing_science(self):
        request = str(uuid4())
        job = self.service.queue(self.pid, self.run["id"], request)
        self.assertEqual(self.service.queue(self.pid, self.run["id"], request)["id"], job["id"])
        self.assertEqual(len(self.process(self.plan())), 1)
        self.assertEqual(self.service.status(self.pid)["state"], "ready")
        index = self.pages.index(self.pid)
        self.assertEqual(len(index), 2)
        for p in index:
            record = self.pages.read(self.pid, p["id"])
            self.assertEqual(record["publication"]["page"]["schema_version"], 2)
            self.assertEqual(record["publication"]["author"]["kind"], "editor")
        with Session() as db:
            run = db.get(Run, self.run["id"])
            self.assertEqual(run.publication["report_id"], self.report_ref["id"])
            self.assertEqual(run.status, "completed")
        self.assertFalse(asyncio.run(self.service.process_next(None)))

    def test_invalid_plot_rolls_back_all_pages_and_keeps_known_response(self):
        self.queue()
        plan = self.plan()
        data = plan.model_dump(mode="json")
        data["pages"][1]["page"]["blocks"][1]["presentation"]["chart"]["y"] = "imaginary"
        self.process(EditorPlan.model_validate(data))
        self.assertEqual(self.service.status(self.pid)["state"], "failed")
        self.assertEqual(self.pages.index(self.pid), [])
        with Session() as db:
            self.assertIsNotNone(db.scalar(select(EditorJob)).plan)

    def test_recovery_applies_saved_response_without_a_second_model_call(self):
        job = self.queue()
        with Session.begin() as db:
            row = db.get(EditorJob, job["id"])
            row.state = "processing"
            row.plan = {"response": {"content": self.plan().model_dump_json()}}
        self.service.recover()
        asyncio.run(self.service.process_next(None))
        self.assertEqual(self.service.status(self.pid)["state"], "ready")

    def test_new_user_feedback_during_composition_prevents_overwrite(self):
        saved = self.pages.save(
            self.pid,
            PageSave(
                client_id=uuid4(),
                expected_revision=0,
                page={"title": "Existing", "blocks": [self.block]},
            ),
        )
        self.queue()

        def edit():
            self.pages.feedback(
                self.pid,
                saved["page_id"],
                PageFeedbackChange(
                    client_id=uuid4(), expected_revision=0, action="pin", block_id=self.block["id"]
                ),
            )

        self.process(self.plan(), edit)
        self.assertEqual(self.service.status(self.pid)["state"], "conflict")
        read = self.pages.read(self.pid, saved["page_id"])
        self.assertEqual(read["revision"], 1)
        self.assertEqual(len(read["user_state"]["pins"]), 1)

    def test_refuted_evidence_cannot_be_published_by_editor(self):
        self.queue()
        self.process(self.plan(), lambda: self.assess(self.result, "refuted"))
        self.assertEqual(self.pages.index(self.pid), [])
        self.assertEqual(self.service.status(self.pid)["state"], "conflict")

    def test_automatic_queue_is_idempotent_and_scope_checked(self):
        self.service.enqueue_completed()
        self.service.enqueue_completed()
        with Session() as db:
            self.assertEqual(len(list(db.scalars(select(EditorJob)))), 1)
        self.status(409, lambda: self.service.queue(self.pid, str(uuid4()), str(uuid4())))

    def test_budget_failure_preserves_previous_scientific_delivery(self):
        self.queue()
        with Session.begin() as db:
            policy = db.get(ProjectPolicyRecord, self.pid)
            policy.policy = {**policy.policy, "conversation": {"api_budget": 0}}
        self.assertEqual(self.process(self.plan()), [])
        self.assertEqual(self.service.status(self.pid)["state"], "failed")
        self.assertEqual(self.pages.index(self.pid), [])

    def test_pm_requests_editor_without_creating_scientific_run_and_replays_receipt(self):
        message = MessageService().save_message(
            self.pid,
            CreateMessage(
                client_id=uuid4(), content="Оформи результаты с графиком", intent="question"
            ),
        )
        arguments = {
            "run_id": self.run["id"],
            "instruction": "Показать сравнение по месяцам графиком",
        }
        workspace = PMWorkspace()
        first = workspace.dispatch(message["id"], "compose_pages", arguments)
        self.assertIsNone(first.get("error"), first)
        self.assertEqual(workspace.dispatch(message["id"], "compose_pages", arguments), first)
        with Session() as db:
            self.assertEqual(len(list(db.scalars(select(Run)))), 1)
            jobs = list(db.scalars(select(EditorJob)))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].snapshot["instruction"], arguments["instruction"])

    def test_project_deletion_waits_for_editor_and_then_cleans_all_editor_data(self):
        job = self.queue()
        with Session.begin() as db:
            db.get(EditorJob, job["id"]).state = "processing"
        self.status(409, lambda: self.projects.delete_project(self.pid))
        self.service.apply(job["id"], self.plan())
        self.projects.delete_project(self.pid)
        with Session() as db:
            self.assertEqual(list(db.scalars(select(EditorJob))), [])

    def test_truncated_reply_is_saved_but_not_published_or_automatically_retried(self):
        job = self.queue()
        error = ResponseProblem(
            "incomplete",
            "truncated",
            raw={"content": "{incomplete"},
            usage={"prompt_tokens": 100, "completion_tokens": 100},
        )
        self.assertEqual(len(self.process(self.plan(), failure=error)), 1)
        self.assertEqual(self.pages.index(self.pid), [])
        with Session() as db:
            row = db.get(EditorJob, job["id"])
            self.assertEqual(row.state, "failed")
            self.assertEqual(row.plan["problem"], "incomplete")
        self.service.recover()
        self.assertFalse(asyncio.run(self.service.process_next(None)))

    def test_unavailable_automatic_source_is_recorded_once_without_paid_retries(self):
        with patch.object(
            self.service, "queue", side_effect=HTTPException(413, "Source too large")
        ) as queue:
            self.service.enqueue_completed()
            self.service.enqueue_completed()
            self.assertEqual(queue.call_count, 1)
        self.assertEqual(self.service.status(self.pid)["state"], "failed")
