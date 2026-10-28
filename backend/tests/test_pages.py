"""Page history, ownership and publication gates against real PostgreSQL."""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_analytical_results as analytical
import test_research_storage as storage
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from dolphinlab.agents.engine import PlatformRuntime
from dolphinlab.api.schemas import CreateProject
from dolphinlab.application.pages import PageService
from dolphinlab.domain.pages import PageFeedbackChange, PagePublish, PageSave, PageSpec
from dolphinlab.infrastructure.database import PageFeedback, PageRelease, PageRevision, Run, Session
from dolphinlab.main import create_app
from dolphinlab.research.staffing import freeze_profiles
from dolphinlab.research.tools import resolve_call


class PageContractsTest(unittest.TestCase):
    def test_unique_blocks_and_no_executable_extensions(self):
        block = {
            "id": str(uuid4()),
            "run_id": str(uuid4()),
            "result_id": "result-x",
            "sha256": "a" * 64,
        }
        with self.assertRaises(ValueError):
            PageSpec(title="Summary", blocks=[block, block])
        with self.assertRaises(ValueError):
            PageSpec(title="Summary", blocks=[{**block, "html": "<script>bad()</script>"}])
        with self.assertRaises(ValueError):
            PageSpec(title=" ")
        with self.assertRaises(ValueError):
            PageSave(client_id=uuid4(), expected_revision=0, kind="team", page={"title": "Team"})


@unittest.skipUnless(storage.TEST_URL, "Set TEST_DATABASE_URL to a disposable PostgreSQL database")
class PagesTest(unittest.TestCase):
    reset = analytical.AnalyticalResultsTest.reset
    start = analytical.AnalyticalResultsTest.start
    dispatch = analytical.AnalyticalResultsTest.dispatch
    team = analytical.AnalyticalResultsTest.team
    member = analytical.AnalyticalResultsTest.member
    runtime = analytical.AnalyticalResultsTest.runtime
    job = analytical.AnalyticalResultsTest.job
    register = analytical.AnalyticalResultsTest.register
    table = analytical.AnalyticalResultsTest.table
    assess = analytical.AnalyticalResultsTest.assess
    report = analytical.AnalyticalResultsTest.report

    @classmethod
    def setUpClass(cls):
        storage.ResearchStorageTest.setUpClass()

    def setUp(self):
        analytical.AnalyticalResultsTest.setUp(self)
        self.pages = PageService()
        self.result = self.table()
        self.block = {
            "id": str(uuid4()),
            "run_id": self.run["id"],
            "result_id": self.result["id"],
            "sha256": self.result["sha256"],
        }

    def save(self, revision=0, blocks=None, actor=None, **kwargs):
        body = PageSave(
            client_id=uuid4(),
            expected_revision=revision,
            page={"title": "Выручка", "blocks": blocks if blocks is not None else [self.block]},
            **kwargs,
        )
        return self.pages.save(self.pid, body, actor=actor)

    def publish(self, saved, **kwargs):
        read = self.pages.read(self.pid, saved["page_id"])
        return self.pages.publish(
            self.pid,
            saved["page_id"],
            PagePublish(
                client_id=uuid4(),
                expected_revision=read["revision"],
                expected_feedback_revision=read["feedback_revision"],
                revision_id=saved["id"],
                **kwargs,
            ),
        )

    def status(self, expected, callback):
        with self.assertRaises(HTTPException) as error:
            callback()
        self.assertEqual(error.exception.status_code, expected)

    def test_save_replay_conflict_scope_and_restart(self):
        body = PageSave(
            client_id=uuid4(),
            expected_revision=0,
            page={"title": "Summary", "blocks": [self.block]},
        )
        saved = self.pages.save(self.pid, body)
        self.assertEqual(self.pages.save(self.pid, body)["id"], saved["id"])
        self.status(
            409, lambda: self.pages.save(self.pid, body.model_copy(update={"expected_revision": 1}))
        )
        self.status(409, lambda: self.save())
        other = self.projects.create_project(CreateProject(name="Other"))["id"]
        self.status(404, lambda: self.pages.save(other, body))
        self.status(404, lambda: self.pages.read(other, saved["page_id"]))
        self.status(409, lambda: self.save(1, [{**self.block, "sha256": "0" * 64}]))
        restored = PageService().read(self.pid, saved["page_id"])
        self.assertEqual(restored["draft"]["page"]["blocks"][0]["result_id"], self.result["id"])

    def test_pins_comments_survive_agent_rebuild_and_are_user_owned(self):
        saved = self.save()
        page_id = saved["page_id"]
        self.pages.feedback(
            self.pid,
            page_id,
            PageFeedbackChange(
                client_id=uuid4(), expected_revision=0, action="pin", block_id=self.block["id"]
            ),
        )
        comment_id = uuid4()
        self.pages.feedback(
            self.pid,
            page_id,
            PageFeedbackChange(
                client_id=uuid4(),
                expected_revision=1,
                action="comment",
                comment_id=comment_id,
                text="Сохранить эту таблицу",
            ),
        )
        actor = {"run_id": self.run["id"], "id": self.editor["id"]}
        rebuilt = self.save(1, [], actor=actor)
        self.assertEqual(len(rebuilt["page"]["blocks"]), 1)
        self.status(409, lambda: self.save(2, [{**self.block, "caption": "Changed"}], actor=actor))
        self.status(
            409,
            lambda: self.save(
                2, [{**self.block, "id": str(uuid4())} for _ in range(20)], actor=actor
            ),
        )
        state = self.pages.read(self.pid, page_id)
        self.assertEqual(
            state["user_state"]["comments"][str(comment_id)]["text"], "Сохранить эту таблицу"
        )
        with self.assertRaises(ValueError):
            resolve_call("report_editor", "page_feedback", {}, revision=5)

    def test_publication_requires_review_and_preserves_historical_verdict(self):
        saved = self.save()
        self.status(409, lambda: self.publish(saved))
        self.assess(self.result)
        published = self.publish(saved)
        self.assertEqual(published["manifest"]["sources"][0]["status"], "verified")
        with Session() as db:
            self.assertIsNone(db.get(Run, self.run["id"]).publication)
        self.assess(self.result, "refuted")
        read = self.pages.publication(self.pid, saved["page_id"], published["id"])
        self.assertTrue(read["current_evidence"]["needs_review"])
        self.assertEqual(read["manifest"]["sources"][0]["status"], "verified")
        self.status(409, lambda: self.publish(saved))

    def test_restore_creates_new_publication_and_detects_feedback_conflict(self):
        self.assess(self.result)
        first = self.save()
        old = self.publish(first)
        second = self.save(1, [{**self.block, "caption": "Новая компоновка"}])
        self.publish(second)
        restore = PagePublish(
            client_id=uuid4(),
            expected_revision=2,
            expected_feedback_revision=0,
            revision_id=first["id"],
        )
        restored = self.pages.publish(self.pid, first["page_id"], restore)
        self.assertNotEqual(restored["id"], old["id"])
        self.assertEqual(restored["page"], old["page"])
        read = self.pages.read(self.pid, first["page_id"])
        self.assertEqual(read["revision"], 3)
        self.assertEqual(read["draft"]["page"], old["page"])
        self.assertEqual(read["draft"]["author"]["restored_from"], first["id"])
        self.assertEqual(len(read["history"]), 3)
        self.assertEqual(
            self.pages.publish(self.pid, first["page_id"], restore)["id"], restored["id"]
        )
        self.assertEqual(self.pages.read(self.pid, first["page_id"])["revision"], 3)
        stale = PagePublish(
            client_id=uuid4(),
            expected_revision=3,
            expected_feedback_revision=0,
            revision_id=first["id"],
        )
        self.pages.feedback(
            self.pid,
            first["page_id"],
            PageFeedbackChange(
                client_id=uuid4(),
                expected_revision=0,
                action="comment",
                comment_id=uuid4(),
                text="Review",
            ),
        )
        self.status(409, lambda: self.pages.publish(self.pid, first["page_id"], stale))

    def test_team_authorization_and_v4_profile_cannot_mutate_pages(self):
        team_id = self.analysis["id"]
        saved = self.save(kind="team", run_id=self.run["id"], team_id=team_id)
        another = self.team("report_editor")
        foreign = self.member(another, "report_editor")
        self.status(
            403,
            lambda: self.save(
                1,
                actor={"id": foreign["id"], "run_id": self.run["id"]},
                kind="team",
                run_id=self.run["id"],
                team_id=team_id,
            ),
        )
        self.status(
            403,
            lambda: self.save(
                1,
                actor={"id": self.reviewer["id"], "run_id": self.run["id"]},
                kind="team",
                run_id=self.run["id"],
                team_id=team_id,
            ),
        )
        self.save(
            1,
            actor={"id": self.editor["id"], "run_id": self.run["id"]},
            kind="team",
            run_id=self.run["id"],
            team_id=team_id,
        )
        self.assertEqual(self.pages.read(self.pid, saved["page_id"])["revision"], 2)
        self.dispatch(
            self.editor,
            "complete_assignment",
            handover={
                "summary": "Page complete",
                "results": [saved["id"]],
                "open_questions": [],
                "unsuccessful_approaches": [],
            },
        )
        self.status(
            403,
            lambda: self.save(
                2,
                actor={"id": self.editor["id"], "run_id": self.run["id"]},
                kind="team",
                run_id=self.run["id"],
                team_id=team_id,
            ),
        )
        profile = freeze_profiles(revision=4)[0]
        self.assertFalse(any(t["function"]["name"] == "save_page" for t in profile["tools"]))

    def test_main_assembles_two_team_snapshots_and_keeps_exact_origin(self):
        self.assess(self.result)
        first = self.save(kind="team", run_id=self.run["id"], team_id=self.analysis["id"])
        other = self.team("data_analyst", "report_editor")
        analyst = self.member(other, "data_analyst")
        metric = self.register(
            "metric",
            {"value": 6.25, "unit": "%", "selection": "Сентябрь к августу", "sample_size": 4},
            dependencies=[self.result["id"]],
            actor=analyst,
        )
        self.assess(metric)
        metric_block = {
            **self.block,
            "id": str(uuid4()),
            "result_id": metric["id"],
            "sha256": metric["sha256"],
        }
        second = self.save(
            blocks=[metric_block], kind="team", run_id=self.run["id"], team_id=other["id"]
        )
        main = self.save(
            blocks=[
                {**self.block, "origin_revision_id": first["id"]},
                {**metric_block, "origin_revision_id": second["id"]},
            ]
        )
        release = self.publish(main)
        self.assertEqual(len(release["page"]["blocks"]), 2)
        self.save(1, [], kind="team", run_id=self.run["id"], team_id=other["id"])
        self.assertEqual(
            self.pages.publication(self.pid, main["page_id"], release["id"])["page"],
            release["page"],
        )
        self.status(
            409, lambda: self.save(1, [{**metric_block, "origin_revision_id": first["id"]}])
        )

    def test_background_refresh_changes_only_unpinned_draft_and_is_restart_safe(self):
        self.assess(self.result)
        second_block = {**self.block, "id": str(uuid4())}
        saved = self.save(blocks=[self.block, second_block])
        self.pages.feedback(
            self.pid,
            saved["page_id"],
            PageFeedbackChange(
                client_id=uuid4(), expected_revision=0, action="pin", block_id=self.block["id"]
            ),
        )
        published = self.publish(saved)
        payload = copy.deepcopy(self.rt.results.get(self.result["id"])["payload"])
        payload["selection"] = "Updated prepared view"
        new = self.register("table", payload, previous=self.result["id"])
        for _ in range(5):
            self.pages.refresh_sources()
        after = PageService().read(self.pid, saved["page_id"])
        self.assertEqual(after["draft"]["page"]["blocks"][0]["result_id"], self.result["id"])
        self.assertEqual(after["draft"]["page"]["blocks"][1]["result_id"], new["id"])
        self.assertEqual(after["publication"]["id"], published["id"])
        self.assertEqual(after["publication"]["page"]["blocks"][1]["result_id"], self.result["id"])
        self.pages.refresh_sources()
        self.assertEqual(self.pages.read(self.pid, saved["page_id"])["revision"], after["revision"])

    def test_failed_save_and_publication_roll_back_and_history_is_immutable(self):
        first = self.save()
        self.assess(self.result)
        publication = self.publish(first)
        original = self.pages.write_revision

        def interrupted(*args):
            original(*args)
            raise RuntimeError("interrupted")

        with (
            patch.object(self.pages, "write_revision", interrupted),
            self.assertRaises(RuntimeError),
        ):
            self.save(1, [])
        self.assertEqual(self.pages.read(self.pid, first["page_id"])["revision"], 1)
        body = PagePublish(
            client_id=uuid4(),
            expected_revision=1,
            expected_feedback_revision=0,
            revision_id=first["id"],
        )
        with (
            patch.object(self.pages, "release_view", side_effect=RuntimeError("interrupted")),
            self.assertRaises(RuntimeError),
        ):
            self.pages.publish(self.pid, first["page_id"], body)
        self.assertEqual(
            self.pages.read(self.pid, first["page_id"])["published_id"], publication["id"]
        )
        self.pages.publish(self.pid, first["page_id"], body)
        with Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PageRelease)), 2)
        with self.assertRaises(IntegrityError), Session.begin() as db:
            db.execute(update(PageRevision).values(spec={}))
        with self.assertRaises(IntegrityError), Session.begin() as db:
            db.execute(update(PageRelease).values(manifest={}))

    def test_report_criteria_gate_and_export_include_transitive_prepared_data(self):
        self.assess(self.result)
        chart = self.register(
            "chart",
            {
                "type": "bar",
                "table_id": self.result["id"],
                "x": "month",
                "y": "amount",
                "x_label": "Месяц",
                "y_label": "RUB",
                "caption": "Подготовленные суммы",
            },
        )
        self.assess(chart)
        report = self.report([chart])
        self.assess(report, checks=[{"name": "Looks fine", "passed": True, "detail": "Too vague"}])
        saved = self.save(
            blocks=[{**self.block, "result_id": report["id"], "sha256": report["sha256"]}]
        )
        self.status(409, lambda: self.publish(saved))
        self.assess(report)
        release = self.publish(saved)
        exported = self.pages.export(self.pid, saved["page_id"], release["id"])
        self.assertEqual(
            {r["id"] for r in exported["results"].values()},
            {self.result["id"], chart["id"], report["id"]},
        )

    def test_runtime_page_receipts_replay_and_rollback_both_mutations(self):
        call = {}

        class Scripted:
            async def complete(self, messages, tools):
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [copy.deepcopy(call)],
                }, {}

        self.rt.client, self.rt.saver = Scripted(), MemorySaver()

        def turn(name, args, ident):
            call.clear()
            call.update(
                id=ident, type="function", function={"name": name, "arguments": json.dumps(args)}
            )
            asyncio.run(
                PlatformRuntime.execute_turn(self.rt, self.editor, self.rt.tools_for(self.editor))
            )

        original = self.rt.store.save_receipt

        def fail_success(agent, ident, result):
            if "error" not in result:
                raise RuntimeError("Receipt interrupted")
            return original(agent, ident, result)

        body = PageSave(
            client_id=uuid4(),
            expected_revision=0,
            page={"title": "Atomic editor", "blocks": [self.block]},
        ).model_dump(mode="json")
        with patch.object(self.rt.store, "save_receipt", side_effect=fail_success):
            turn("save_page", body, "save-failed")
        self.assertEqual(self.pages.index(self.pid), [])
        for _ in range(2):
            turn("save_page", body, "save-ok")
        saved = self.rt.store.receipt(self.editor["id"], "0:save-ok")
        self.assertNotIn("error", saved, saved)
        self.assertEqual(self.pages.read(self.pid, saved["page_id"])["revision"], 1)
        self.assess(self.result)
        publish = {
            "page_id": saved["page_id"],
            **PagePublish(
                client_id=uuid4(),
                expected_revision=1,
                expected_feedback_revision=0,
                revision_id=saved["id"],
            ).model_dump(mode="json"),
        }
        with patch.object(self.rt.store, "save_receipt", side_effect=fail_success):
            turn("publish_page", publish, "publish-failed")
        self.assertIsNone(self.pages.read(self.pid, saved["page_id"])["publication"])
        for _ in range(2):
            turn("publish_page", publish, "publish-ok")
        receipt = self.rt.store.receipt(self.editor["id"], "0:publish-ok")
        self.assertNotIn("error", receipt, receipt)
        self.assertEqual(len(self.pages.read(self.pid, saved["page_id"])["history"]), 1)
        with self.assertRaises(ValueError):
            self.dispatch(self.reviewer, "save_page", **body)

    def test_api_preview_export_and_project_delete(self):
        with TestClient(create_app(), base_url="http://localhost") as client:
            path = f"/api/projects/{self.pid}/pages"
            body = PageSave(
                client_id=uuid4(),
                expected_revision=0,
                page={"title": "Page", "blocks": [self.block]},
            ).model_dump(mode="json")
            response = client.post(path, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            page_id = response.json()["page_id"]
            self.assertEqual(client.get(path + "/catalog").status_code, 200)
            result_index = client.get(
                f"/api/projects/{self.pid}/runs/{self.run['id']}/results"
            ).json()
            self.assertEqual(result_index["results"][0]["status"], "reported")
            self.assertEqual(client.get(path + "/" + page_id).status_code, 200)
            export = client.get(path + "/" + page_id + "/export")
            self.assertEqual(export.status_code, 200, export.text)
            self.assertEqual(export.json()["format"], "dolphinlab-page-v1")
            self.assertIn(self.result["id"], str(export.json()["results"]))
        with Session.begin() as db:
            db.get(Run, self.run["id"]).status = "stopped"
        self.projects.delete_project(self.pid)
        with Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PageRevision)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(PageFeedback)), 0)
