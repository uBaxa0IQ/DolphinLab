"""Real PostgreSQL tests. Never use a running project's database or volumes."""

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

TEST_URL = os.getenv("TEST_DATABASE_URL")
_workspace = tempfile.TemporaryDirectory(prefix="dolphinlab-stage1-")
os.environ["DATABASE_URL"] = TEST_URL or "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
os.environ["WORKSPACE_DATA_DIR"] = str(Path(_workspace.name) / "uploads")
os.environ["RUNS_ROOT"] = str(Path(_workspace.name) / "runs")
os.environ["ENABLE_MODEL_CALLS"] = "false"

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dolphinlab.api.schemas import (  # noqa: E402
    ControlRun,
    CreateGoal,
    CreateIteration,
    CreateProject,
    EditProject,
    StartRun,
)
from dolphinlab.application.projects import ProjectService  # noqa: E402
from dolphinlab.application.research import ResearchService  # noqa: E402
from dolphinlab.application.runs import RunService  # noqa: E402
from dolphinlab.domain.research import GoalSpec  # noqa: E402
from dolphinlab.infrastructure.database import (  # noqa: E402
    GoalRevision,
    InputFile,
    ResearchIteration,
    Run,
    Session,
    WorkspaceSettings,
    engine,
)
from dolphinlab.main import create_app  # noqa: E402


def tearDownModule():
    engine.dispose()
    _workspace.cleanup()


@unittest.skipUnless(TEST_URL, "Set TEST_DATABASE_URL to a disposable PostgreSQL database")
class ResearchStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not make_url(TEST_URL).database.endswith("_test"):
            raise RuntimeError("Disposable test database name must end in _test")

    def reset(self, revision="head"):
        with engine.begin() as db:
            db.execute(text("DROP SCHEMA public CASCADE"))
            db.execute(text("CREATE SCHEMA public"))
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        command.upgrade(config, revision)

    def setUp(self):
        self.reset()
        self.projects, self.research, self.runs = ProjectService(), ResearchService(), RunService()
        self.project = self.projects.create_project(
            CreateProject(name="Sales", description="First goal")
        )
        self.pid = self.project["id"]
        self.goal = self.project["current_goal"]

    def new_goal(self, question="Second goal", **kwargs):
        values = dict(client_id=uuid4(), expected_revision=1, goal=GoalSpec(question=question))
        values.update(kwargs)
        return CreateGoal(**values)

    def new_iteration(self, **kwargs):
        values = dict(client_id=uuid4(), expected_sequence=0, goal_revision_id=self.goal["id"])
        values.update(kwargs)
        return CreateIteration(**values)

    def seed_inputs(self):
        with Session.begin() as db:
            if not db.get(WorkspaceSettings, "local"):
                db.add(
                    WorkspaceSettings(
                        id="local",
                        base_url="http://model.invalid/v1",
                        model="test-model",
                        encrypted_api_key="test-placeholder-no-real-key",
                        pricing={},
                    )
                )
            db.add(
                InputFile(
                    id=str(uuid4()),
                    project_id=self.pid,
                    name="data.csv",
                    stored_name=str(uuid4()),
                    size=10,
                    sha256="0" * 64,
                )
            )

    def start(self, body=None):
        # This only creates a queued row. No worker/model client is started in this suite.
        with patch("dolphinlab.application.runs.execution_enabled", return_value=True):
            return self.runs.start_run(self.pid, body)

    def assert_status(self, status, callback):
        with self.assertRaises(HTTPException) as raised:
            callback()
        self.assertEqual(raised.exception.status_code, status)

    def test_project_starts_with_goal_but_no_execution(self):
        self.assertEqual(self.goal["revision"], 1)
        self.assertEqual(self.goal["goal"]["question"], "First goal")
        self.assertEqual(self.research.list_iterations(self.pid), [])
        self.assertEqual(self.runs.list_runs(self.pid), [])

    def test_goal_retry_and_payload_conflict(self):
        body = self.new_goal()
        first = self.research.create_goal(self.pid, body)
        self.assertEqual(self.research.create_goal(self.pid, body), first)
        changed = body.model_copy(update={"goal": GoalSpec(question="Different")})
        self.assert_status(409, lambda: self.research.create_goal(self.pid, changed))
        self.assertEqual(len(self.research.list_goals(self.pid)), 2)

    def test_stale_revision_and_database_immutability(self):
        self.research.create_goal(self.pid, self.new_goal())
        self.assert_status(409, lambda: self.research.create_goal(self.pid, self.new_goal("Other")))
        with self.assertRaises(IntegrityError), Session.begin() as db:
            db.execute(
                update(GoalRevision)
                .where(GoalRevision.id == self.goal["id"])
                .values(spec={"question": "rewritten"})
            )
        self.assertEqual(self.research.list_goals(self.pid)[-1]["goal"]["question"], "First goal")

    def test_concurrent_goal_writers_serialize(self):
        bodies = [self.new_goal("A"), self.new_goal("B")]

        def create(body):
            try:
                return self.research.create_goal(self.pid, body)["revision"]
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(create, bodies)), [2, 409])

    def test_concurrent_same_request_is_not_duplicated(self):
        body = self.new_goal()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.research.create_goal(self.pid, body), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(len(self.research.list_goals(self.pid)), 2)

    def test_iteration_retry_parent_chain_and_stale_sequence(self):
        body = self.new_iteration()
        first = self.research.create_iteration(self.pid, body)
        self.assertEqual(self.research.create_iteration(self.pid, body)["id"], first["id"])
        self.assert_status(
            409, lambda: self.research.create_iteration(self.pid, self.new_iteration())
        )
        second = self.research.create_iteration(
            self.pid,
            self.new_iteration(
                expected_sequence=1,
                parent_iteration_id=first["id"],
            ),
        )
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(second["parent_iteration_id"], first["id"])
        self.assertEqual(second["status"], "draft")
        self.assert_status(
            409,
            lambda: self.research.create_iteration(
                self.pid,
                body.model_copy(update={"expected_sequence": 1}),
            ),
        )

    def test_cross_project_goal_iteration_and_database_constraint(self):
        other = self.projects.create_project(CreateProject(name="Other"))
        self.assert_status(
            404,
            lambda: self.research.create_iteration(
                self.pid,
                self.new_iteration(
                    goal_revision_id=other["current_goal"]["id"],
                ),
            ),
        )
        iteration = self.research.create_iteration(self.pid, self.new_iteration())
        self.assert_status(404, lambda: self.research.get_iteration(other["id"], iteration["id"]))
        with self.assertRaises(IntegrityError), Session.begin() as db:
            db.add(
                ResearchIteration(
                    id=str(uuid4()),
                    project_id=other["id"],
                    sequence=1,
                    goal_revision_id=self.goal["id"],
                )
            )

    def test_archived_project_blocks_new_intent(self):
        self.projects.edit_project(
            self.pid,
            EditProject(
                name="Sales",
                description="First goal",
                archived=True,
            ),
        )
        self.assert_status(409, lambda: self.research.create_goal(self.pid, self.new_goal()))
        self.assert_status(
            409, lambda: self.research.create_iteration(self.pid, self.new_iteration())
        )
        self.assertEqual(len(self.research.list_goals(self.pid)), 1)

    def test_editing_description_creates_revision_without_rewriting_iteration(self):
        iteration = self.research.create_iteration(self.pid, self.new_iteration())
        self.projects.edit_project(
            self.pid,
            EditProject(
                name="Sales renamed",
                description="Changed goal",
                archived=False,
            ),
        )
        self.assertEqual(self.projects.get_project(self.pid)["current_goal"]["revision"], 2)
        self.assertEqual(self.research.get_iteration(self.pid, iteration["id"])["goal"], self.goal)

    def test_new_run_uses_frozen_goal_and_idempotent_start(self):
        self.seed_inputs()
        iteration = self.research.create_iteration(self.pid, self.new_iteration())
        self.research.create_goal(self.pid, self.new_goal())
        body = StartRun(client_id=uuid4(), iteration_id=iteration["id"])
        run = self.start(body)
        self.assertEqual(run["goal_snapshot"]["question"], "First goal")
        self.assertEqual(self.start(body)["id"], run["id"])
        with Session() as db:
            self.assertEqual(db.get(Run, run["id"]).task_description, "First goal")
        self.assert_status(409, lambda: self.start(StartRun(client_id=uuid4())))
        self.assert_status(409, lambda: self.start(body.model_copy(update={"iteration_id": None})))
        self.assertEqual(self.research.get_iteration(self.pid, iteration["id"])["status"], "queued")

    def test_project_editor_preserves_conditions_and_detects_stale_goal(self):
        self.research.create_goal(
            self.pid,
            self.new_goal(
                goal=GoalSpec(
                    question="Second goal",
                    definitions={"Revenue": "after discounts"},
                    constraints=("RUB",),
                )
            ),
        )
        self.assert_status(
            409,
            lambda: self.projects.edit_project(
                self.pid,
                EditProject(
                    name="Sales",
                    description="Stale edit",
                    archived=False,
                    expected_goal_revision=1,
                ),
            ),
        )
        updated = self.projects.edit_project(
            self.pid,
            EditProject(
                name="Sales",
                description="Edited goal",
                archived=False,
                expected_goal_revision=2,
            ),
        )
        self.assertEqual(
            updated["current_goal"]["goal"]["definitions"], {"Revenue": "after discounts"}
        )
        self.assertEqual(updated["current_goal"]["goal"]["constraints"], ["RUB"])

    def test_legacy_start_api_creates_new_iteration_and_resume_keeps_identity(self):
        self.seed_inputs()
        first = self.start()
        paused = self.runs.control_run(self.pid, first["id"], ControlRun(action="pause"))
        self.assertEqual(paused["status"], "paused")
        with patch("dolphinlab.application.runs.execution_enabled", return_value=True):
            resumed = self.runs.control_run(self.pid, first["id"], ControlRun(action="resume"))
        self.assertEqual(resumed["iteration_id"], first["iteration_id"])
        self.runs.control_run(self.pid, first["id"], ControlRun(action="stop"))
        second = self.start()
        self.assertNotEqual(second["iteration_id"], first["iteration_id"])
        iterations = self.research.list_iterations(self.pid)
        self.assertEqual(iterations[0]["parent_iteration_id"], first["iteration_id"])

    def test_start_disabled_does_not_create_iteration(self):
        self.assert_status(409, lambda: self.runs.start_run(self.pid))
        self.assertEqual(self.research.list_iterations(self.pid), [])

    def test_default_start_uses_prepared_iteration_for_current_goal(self):
        self.seed_inputs()
        prepared = self.research.create_iteration(self.pid, self.new_iteration())
        run = self.start()
        self.assertEqual(run["iteration_id"], prepared["id"])
        self.assertEqual(len(self.research.list_iterations(self.pid)), 1)

    def test_cross_project_start_is_rejected(self):
        self.seed_inputs()
        other = self.projects.create_project(CreateProject(name="Other"))
        foreign = self.research.create_iteration(
            other["id"],
            CreateIteration(
                client_id=uuid4(),
                expected_sequence=0,
                goal_revision_id=other["current_goal"]["id"],
            ),
        )
        self.assert_status(404, lambda: self.start(StartRun(iteration_id=foreign["id"])))

    def test_goal_conditions_are_saved_into_run_task(self):
        self.seed_inputs()
        goal = GoalSpec(question="Compare", definitions={"Revenue": "after discounts"})
        self.research.create_goal(self.pid, self.new_goal(goal=goal))
        run = self.start()
        with Session() as db:
            self.assertIn("Revenue: after discounts", db.get(Run, run["id"]).task_description)

    def test_storage_survives_new_connection_and_service(self):
        iteration = self.research.create_iteration(self.pid, self.new_iteration())
        self.research.create_goal(self.pid, self.new_goal())
        engine.dispose()
        restored = ResearchService().get_iteration(self.pid, iteration["id"])
        self.assertEqual(restored, iteration)
        self.assertEqual(len(ResearchService().list_goals(self.pid)), 2)

    def test_delete_project_removes_linked_history(self):
        self.seed_inputs()
        run = self.start()
        self.runs.control_run(self.pid, run["id"], ControlRun(action="stop"))
        self.research.create_iteration(
            self.pid,
            self.new_iteration(
                expected_sequence=1,
                parent_iteration_id=run["iteration_id"],
            ),
        )
        self.assertTrue(self.projects.delete_project(self.pid)["deleted"])
        with Session() as db:
            for model in (GoalRevision, ResearchIteration, Run):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)

    def test_http_validation_and_no_model_calls(self):
        with TestClient(create_app(), base_url="http://localhost") as client:
            path = f"/api/projects/{self.pid}"
            response = client.post(
                path + "/goals",
                json={
                    "client_id": str(uuid4()),
                    "expected_revision": 1,
                    "goal": {"question": "HTTP goal"},
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            goal = response.json()
            iteration = client.post(
                path + "/iterations",
                json={
                    "client_id": str(uuid4()),
                    "expected_sequence": 0,
                    "goal_revision_id": goal["id"],
                },
            )
            self.assertEqual(iteration.status_code, 201, iteration.text)
            self.assertEqual(client.get(path + "/iterations").json()[0]["goal"], goal)
            self.assertEqual(
                client.post(
                    path + "/goals",
                    json={
                        "client_id": str(uuid4()),
                        "expected_revision": 2,
                        "goal": {"question": "  "},
                    },
                ).status_code,
                422,
            )
            self.assertEqual(client.post(path + "/runs").status_code, 409)
            self.assertEqual(client.get(path + "/runs").json(), [])

    def test_migration_preserves_historical_snapshots_and_checkpoints(self):
        self.reset("0002")
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        metadata = MetaData()
        projects = Table("projects", metadata, autoload_with=engine)
        runs = Table("runs", metadata, autoload_with=engine)
        pid = str(uuid4())
        original = dict(
            id=str(uuid4()),
            project_id=pid,
            status="paused",
            requested_action="pause",
            policy={"frozen": True},
            inputs=[{"sha256": "original"}],
            connection={"model": "old"},
            task_description="Historical task",
            elapsed_seconds=12.5,
            usage={"requests": 2},
            snapshot={"teams": ["original"]},
            publication={"sha256": "published"},
            error=None,
            created=stamp,
            updated=stamp,
            paused_at=stamp,
        )
        with engine.begin() as db:
            db.execute(
                projects.insert().values(
                    id=pid,
                    name="Old",
                    description="Current changed task",
                    status="paused",
                    archived=False,
                    created=stamp,
                )
            )
            db.execute(runs.insert().values(**original))
            db.execute(text("CREATE TABLE checkpoints (thread_id text PRIMARY KEY, payload jsonb)"))
            db.execute(
                text("INSERT INTO checkpoints VALUES ('original', CAST(:payload AS jsonb))"),
                {"payload": '{"step":7}'},
            )
        command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        with Session() as db:
            migrated = db.get(Run, original["id"])
            for key, value in original.items():
                self.assertEqual(getattr(migrated, key), value, key)
            self.assertEqual(migrated.goal_snapshot["question"], "Historical task")
            self.assertEqual(
                db.execute(text("SELECT payload FROM checkpoints")).scalar(), {"step": 7}
            )
        history = self.research.list_iterations(pid)
        self.assertTrue(history[0]["legacy"])
        self.assertEqual(history[0]["goal"]["goal"]["question"], "Historical task")
        self.assertEqual(
            self.projects.get_project(pid)["current_goal"]["goal"]["question"],
            "Current changed task",
        )


if __name__ == "__main__":
    unittest.main()
